"""Numba-accelerated 6502 CPU inner loop (native x86-64 machine code)."""

import numpy as np

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False


if HAS_NUMBA:
    @njit(inline='always')
    def _r(addr, ram, prg, prg_off):
        if addr >= 0x8000:
            return np.int64(prg[prg_off[(addr >> 13) & 3] + (addr & 0x1FFF)])
        if addr < 0x2000:
            return np.int64(ram[addr & 0x07FF])
        return np.int64(-1)

    @njit(inline='always')
    def _w(addr, val, ram):
        if addr < 0x2000:
            ram[addr & 0x07FF] = val
            return True
        return False

    @njit(cache=True)
    def jit_cpu_run(budget, a, x, y, sp, pc, st, tot, ram, prg, prg_off):
        """Run 6502 instructions in native code until budget <= 0 or hardware I/O is hit.
        Returns (a, x, y, sp, pc, st, budget, tot, hit_io).
        """
        while budget > 0.0:
            start_pc = pc
            op = _r(pc, ram, prg, prg_off)
            if op < 0:
                return a, x, y, sp, start_pc, st, budget, tot, 1
            pc = (pc + 1) & 0xFFFF

            # --- Implied / Accumulator / Register ---
            if op == 0xEA:  # NOP
                c = 2
            elif op == 0x18:  # CLC
                st &= ~0x01; c = 2
            elif op == 0x38:  # SEC
                st |= 0x01; c = 2
            elif op == 0x58:  # CLI
                st &= ~0x04; c = 2
            elif op == 0x78:  # SEI
                st |= 0x04; c = 2
            elif op == 0xD8:  # CLD
                st &= ~0x08; c = 2
            elif op == 0xF8:  # SED
                st |= 0x08; c = 2
            elif op == 0xB8:  # CLV
                st &= ~0x40; c = 2
            elif op == 0xAA:  # TAX
                x = a; st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80); c = 2
            elif op == 0x8A:  # TXA
                a = x; st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0xA8:  # TAY
                y = a; st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80); c = 2
            elif op == 0x98:  # TYA
                a = y; st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0xBA:  # TSX
                x = sp; st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80); c = 2
            elif op == 0x9A:  # TXS
                sp = x; c = 2
            elif op == 0xE8:  # INX
                x = (x + 1) & 0xFF; st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80); c = 2
            elif op == 0xCA:  # DEX
                x = (x - 1) & 0xFF; st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80); c = 2
            elif op == 0xC8:  # INY
                y = (y + 1) & 0xFF; st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80); c = 2
            elif op == 0x88:  # DEY
                y = (y - 1) & 0xFF; st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80); c = 2
            elif op == 0x0A:  # ASL A
                st = (st & 0x7C) | ((a >> 7) & 1)
                a = (a << 1) & 0xFF
                st |= (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0x4A:  # LSR A
                st = (st & 0x7C) | (a & 1)
                a >>= 1
                st |= (0x02 if a == 0 else 0); c = 2
            elif op == 0x2A:  # ROL A
                old_c = st & 1
                st = (st & 0x7C) | ((a >> 7) & 1)
                a = ((a << 1) | old_c) & 0xFF
                st |= (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0x6A:  # ROR A
                old_c = st & 1
                st = (st & 0x7C) | (a & 1)
                a = ((old_c << 7) | (a >> 1)) & 0xFF
                st |= (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0x48:  # PHA
                ram[0x0100 + sp] = a; sp = (sp - 1) & 0xFF; c = 3
            elif op == 0x68:  # PLA
                sp = (sp + 1) & 0xFF; a = np.int64(ram[0x0100 + sp])
                st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 4
            elif op == 0x08:  # PHP
                ram[0x0100 + sp] = st | 0x30; sp = (sp - 1) & 0xFF; c = 3
            elif op == 0x28:  # PLP
                sp = (sp + 1) & 0xFF; st = (np.int64(ram[0x0100 + sp]) & ~0x10) | 0x20; c = 4

            # --- Branches ---
            elif op == 0xD0 or op == 0xF0 or op == 0x10 or op == 0x30 or op == 0x90 or op == 0xB0 or op == 0x50 or op == 0x70:
                off = _r(pc, ram, prg, prg_off)
                if off < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0xD0: take = (st & 0x02) == 0
                elif op == 0xF0: take = (st & 0x02) != 0
                elif op == 0x10: take = (st & 0x80) == 0
                elif op == 0x30: take = (st & 0x80) != 0
                elif op == 0x90: take = (st & 0x01) == 0
                elif op == 0xB0: take = (st & 0x01) != 0
                elif op == 0x50: take = (st & 0x40) == 0
                else: take = (st & 0x40) != 0
                if take:
                    if off & 0x80: off -= 0x100
                    npc = (pc + off) & 0xFFFF
                    c = 4 if (pc & 0xFF00) != (npc & 0xFF00) else 3
                    pc = npc
                else:
                    c = 2

            # --- Jumps / Subroutines ---
            elif op == 0x4C:  # JMP abs
                lo = _r(pc, ram, prg, prg_off)
                hi = _r((pc + 1) & 0xFFFF, ram, prg, prg_off)
                if lo < 0 or hi < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (hi << 8) | lo; c = 3
            elif op == 0x20:  # JSR
                lo = _r(pc, ram, prg, prg_off)
                hi = _r((pc + 1) & 0xFFFF, ram, prg, prg_off)
                if lo < 0 or hi < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                ret = (pc + 1) & 0xFFFF
                ram[0x0100 + sp] = (ret >> 8) & 0xFF; sp = (sp - 1) & 0xFF
                ram[0x0100 + sp] = ret & 0xFF; sp = (sp - 1) & 0xFF
                pc = (hi << 8) | lo; c = 6
            elif op == 0x60:  # RTS
                sp = (sp + 1) & 0xFF; lo = np.int64(ram[0x0100 + sp])
                sp = (sp + 1) & 0xFF; hi = np.int64(ram[0x0100 + sp])
                pc = (((hi << 8) | lo) + 1) & 0xFFFF; c = 6
            elif op == 0x40:  # RTI
                sp = (sp + 1) & 0xFF; st = (np.int64(ram[0x0100 + sp]) & ~0x10) | 0x20
                sp = (sp + 1) & 0xFF; lo = np.int64(ram[0x0100 + sp])
                sp = (sp + 1) & 0xFF; hi = np.int64(ram[0x0100 + sp])
                pc = (hi << 8) | lo; c = 6

            # --- Loads / Stores (Immediate & Zero Page) ---
            elif op == 0xA9:  # LDA #imm
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; a = v; st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0xA2:  # LDX #imm
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; x = v; st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80); c = 2
            elif op == 0xA0:  # LDY #imm
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; y = v; st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80); c = 2
            elif op == 0xA5:  # LDA zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; a = np.int64(ram[zp]); st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 3
            elif op == 0xB5:  # LDA zpx
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; a = np.int64(ram[(zp + x) & 0xFF]); st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 4
            elif op == 0xA6:  # LDX zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; x = np.int64(ram[zp]); st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80); c = 3
            elif op == 0xA4:  # LDY zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; y = np.int64(ram[zp]); st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80); c = 3
            elif op == 0x85:  # STA zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; ram[zp] = a; c = 3
            elif op == 0x95:  # STA zpx
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; ram[(zp + x) & 0xFF] = a; c = 4
            elif op == 0x86:  # STX zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; ram[zp] = x; c = 3
            elif op == 0x84:  # STY zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; ram[zp] = y; c = 3

            # --- Absolute / Indexed Loads & Stores ---
            elif op == 0xAD or op == 0xBD or op == 0xB9:  # LDA abs / abx / aby
                lo = _r(pc, ram, prg, prg_off)
                hi = _r((pc + 1) & 0xFFFF, ram, prg, prg_off)
                if lo < 0 or hi < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                base = (hi << 8) | lo
                idx = 0 if op == 0xAD else (x if op == 0xBD else y)
                addr = (base + idx) & 0xFFFF
                v = _r(addr, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 2) & 0xFFFF; a = v; st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80)
                c = 4 + (1 if (op != 0xAD and (base & 0xFF00) != (addr & 0xFF00)) else 0)
            elif op == 0xAE or op == 0xAC:  # LDX abs / LDY abs
                lo = _r(pc, ram, prg, prg_off)
                hi = _r((pc + 1) & 0xFFFF, ram, prg, prg_off)
                if lo < 0 or hi < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                v = _r((hi << 8) | lo, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 2) & 0xFFFF
                if op == 0xAE: x = v
                else: y = v
                st = (st & 0x7D) | (0x02 if v == 0 else 0) | (v & 0x80); c = 4
            elif op == 0x8D or op == 0x9D or op == 0x99:  # STA abs / abx / aby
                lo = _r(pc, ram, prg, prg_off)
                hi = _r((pc + 1) & 0xFFFF, ram, prg, prg_off)
                if lo < 0 or hi < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                base = (hi << 8) | lo
                idx = 0 if op == 0x8D else (x if op == 0x9D else y)
                addr = (base + idx) & 0xFFFF
                if not _w(addr, a, ram): return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 2) & 0xFFFF; c = 4 if op == 0x8D else 5
            elif op == 0x8E or op == 0x8C:  # STX abs / STY abs
                lo = _r(pc, ram, prg, prg_off)
                hi = _r((pc + 1) & 0xFFFF, ram, prg, prg_off)
                if lo < 0 or hi < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                addr = (hi << 8) | lo
                val = x if op == 0x8E else y
                if not _w(addr, val, ram): return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 2) & 0xFFFF; c = 4
            elif op == 0xB1:  # LDA (zp), Y
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                base = (np.int64(ram[(zp + 1) & 0xFF]) << 8) | np.int64(ram[zp])
                addr = (base + y) & 0xFFFF
                v = _r(addr, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; a = v; st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80)
                c = 5 + (1 if (base & 0xFF00) != (addr & 0xFF00) else 0)
            elif op == 0x91:  # STA (zp), Y
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                base = (np.int64(ram[(zp + 1) & 0xFF]) << 8) | np.int64(ram[zp])
                addr = (base + y) & 0xFFFF
                if not _w(addr, a, ram): return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF; c = 6

            # --- Arithmetic / Logic / Compare / INC / DEC (Imm & ZP) ---
            elif op == 0x69 or op == 0x65:  # ADC #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0x65: v = np.int64(ram[v]); c = 3
                else: c = 2
                res = a + v + (st & 1); r8 = res & 0xFF
                st = (st & ~0xC3) | (1 if res > 0xFF else 0) | (0x40 if (~(a ^ v) & (a ^ res) & 0x80) else 0) | (2 if r8 == 0 else 0) | (r8 & 0x80)
                a = r8
            elif op == 0xE9 or op == 0xE5:  # SBC #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0xE5: v = np.int64(ram[v]); c = 3
                else: c = 2
                v ^= 0xFF
                res = a + v + (st & 1); r8 = res & 0xFF
                st = (st & ~0xC3) | (1 if res > 0xFF else 0) | (0x40 if ((a ^ res) & (v ^ res) & 0x80) else 0) | (2 if r8 == 0 else 0) | (r8 & 0x80)
                a = r8
            elif op == 0x29 or op == 0x25:  # AND #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0x25: v = np.int64(ram[v]); c = 3
                else: c = 2
                a &= v; st = (st & 0x7D) | (2 if a == 0 else 0) | (a & 0x80)
            elif op == 0x09 or op == 0x05:  # ORA #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0x05: v = np.int64(ram[v]); c = 3
                else: c = 2
                a |= v; st = (st & 0x7D) | (2 if a == 0 else 0) | (a & 0x80)
            elif op == 0x49 or op == 0x45:  # EOR #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0x45: v = np.int64(ram[v]); c = 3
                else: c = 2
                a ^= v; st = (st & 0x7D) | (2 if a == 0 else 0) | (a & 0x80)
            elif op == 0xC9 or op == 0xC5:  # CMP #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0xC5: v = np.int64(ram[v]); c = 3
                else: c = 2
                r8 = (a - v) & 0xFF
                st = (st & ~0x83) | (1 if a >= v else 0) | (2 if r8 == 0 else 0) | (r8 & 0x80)
            elif op == 0xE0 or op == 0xE4:  # CPX #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0xE4: v = np.int64(ram[v]); c = 3
                else: c = 2
                r8 = (x - v) & 0xFF
                st = (st & ~0x83) | (1 if x >= v else 0) | (2 if r8 == 0 else 0) | (r8 & 0x80)
            elif op == 0xC0 or op == 0xC4:  # CPY #imm / zp
                v = _r(pc, ram, prg, prg_off)
                if v < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                if op == 0xC4: v = np.int64(ram[v]); c = 3
                else: c = 2
                r8 = (y - v) & 0xFF
                st = (st & ~0x83) | (1 if y >= v else 0) | (2 if r8 == 0 else 0) | (r8 & 0x80)
            elif op == 0xE6:  # INC zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                v = (np.int64(ram[zp]) + 1) & 0xFF; ram[zp] = v
                st = (st & 0x7D) | (2 if v == 0 else 0) | (v & 0x80); c = 5
            elif op == 0xC6:  # DEC zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                v = (np.int64(ram[zp]) - 1) & 0xFF; ram[zp] = v
                st = (st & 0x7D) | (2 if v == 0 else 0) | (v & 0x80); c = 5
            elif op == 0x24:  # BIT zp
                zp = _r(pc, ram, prg, prg_off)
                if zp < 0: return a, x, y, sp, start_pc, st, budget, tot, 1
                pc = (pc + 1) & 0xFFFF
                v = np.int64(ram[zp])
                st = (st & 0x3D) | (2 if (a & v) == 0 else 0) | (v & 0xC0); c = 3
            else:
                return a, x, y, sp, start_pc, st, budget, tot, 1

            budget -= c
            tot += c

        return a, x, y, sp, pc, st | 0x20, budget, tot, 0

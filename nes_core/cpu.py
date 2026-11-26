"""
NES MOS 6502 CPU Emulator
Implements the full 6502 instruction set used by the NES.
"""

import numpy as np
from .cpu_jit import HAS_NUMBA
if HAS_NUMBA:
    from .cpu_jit import jit_cpu_run


class CPU:
    # Status flag bit positions
    FLAG_C = 0  # Carry
    FLAG_Z = 1  # Zero
    FLAG_I = 2  # Interrupt Disable
    FLAG_D = 3  # Decimal (unused on NES)
    FLAG_B = 4  # Break
    FLAG_U = 5  # Unused (always 1)
    FLAG_V = 6  # Overflow
    FLAG_N = 7  # Negative

    def __init__(self, bus):
        self.bus = bus
        self.ram = bus.ram
        self.cart_read = bus.cart.mapper.cpu_read
        self.prg_pages = getattr(bus.cart.mapper, 'prg_pages', None)
        self.bus_read = bus.cpu_read
        self.bus_write = bus.cpu_write
        self._ram_np = np.frombuffer(bus.ram, dtype=np.uint8)
        self._prg_np = np.frombuffer(bus.cart.prg_rom, dtype=np.uint8)
        self._prg_off_np = np.zeros(4, dtype=np.int32)

        # Registers
        self.a = 0x00       # Accumulator
        self.x = 0x00       # X Index
        self.y = 0x00       # Y Index
        self.sp = 0xFD      # Stack Pointer
        self.pc = 0x0000    # Program Counter
        self.status = 0x24  # Processor Status (bit 5 always set)

        self.cycles = 0     # Remaining cycles for current instruction
        self.total_cycles = 0
        self.nmi_pending = False
        self.irq_pending = False

        # Build instruction table
        self._build_opcode_table()
        # Fast 256-element list lookup instead of dict
        self.opcodes_list = [self.opcodes.get(op, (self.NOP, self._imp, 2)) for op in range(256)]

    # -------------------------------------------------------------------------
    # Flag helpers
    # -------------------------------------------------------------------------
    def get_flag(self, bit):
        return (self.status >> bit) & 1

    def set_flag(self, bit, value):
        if value:
            self.status |= (1 << bit)
        else:
            self.status &= ~(1 << bit)

    def set_nz(self, value):
        """Set Negative and Zero flags directly without function call overhead."""
        v = value & 0xFF
        self.status = (self.status & 0x7D) | (0x02 if v == 0 else 0) | (v & 0x80)

    # -------------------------------------------------------------------------
    # Bus access (fast path for zero-page / RAM)
    # -------------------------------------------------------------------------
    def read(self, addr):
        addr &= 0xFFFF
        if addr >= 0x8000:
            if self.prg_pages is not None:
                return self.prg_pages[(addr >> 13) & 3][addr & 0x1FFF]
            return self.cart_read(addr)
        if addr < 0x2000:
            return self.ram[addr & 0x07FF]
        return self.bus_read(addr)

    def write(self, addr, data):
        addr &= 0xFFFF
        data &= 0xFF
        if addr < 0x2000:
            self.ram[addr & 0x07FF] = data
            return
        self.bus_write(addr, data)

    # -------------------------------------------------------------------------
    # Stack helpers
    # -------------------------------------------------------------------------
    def push(self, data):
        self.write(0x0100 + self.sp, data)
        self.sp = (self.sp - 1) & 0xFF

    def pop(self):
        self.sp = (self.sp + 1) & 0xFF
        return self.read(0x0100 + self.sp)

    def push16(self, data):
        self.push((data >> 8) & 0xFF)
        self.push(data & 0xFF)

    def pop16(self):
        lo = self.pop()
        hi = self.pop()
        return (hi << 8) | lo

    # -------------------------------------------------------------------------
    # Reset / Interrupts
    # -------------------------------------------------------------------------
    def reset(self):
        self.a = 0
        self.x = 0
        self.y = 0
        self.sp = 0xFD
        self.status = 0x24
        self.nmi_pending = False
        self.irq_pending = False
        lo = self.read(0xFFFC)
        hi = self.read(0xFFFD)
        self.pc = (hi << 8) | lo
        self.cycles = 8
        self.total_cycles = 0

    def irq(self):
        self.irq_pending = True

    def nmi(self):
        self.nmi_pending = True

    # -------------------------------------------------------------------------
    # Fast Step (one instruction at a time)
    # -------------------------------------------------------------------------
    def step(self):
        """Execute one complete instruction and return the number of CPU cycles consumed."""
        if self.nmi_pending:
            self.nmi_pending = False
            self.push16(self.pc)
            self.status = (self.status & ~0x10) | 0x20
            self.push(self.status)
            self.status |= 0x04  # FLAG_I = 1
            lo = self.read(0xFFFA)
            hi = self.read(0xFFFB)
            self.pc = (hi << 8) | lo
            self.total_cycles += 7
            return 7

        if self.irq_pending and not (self.status & 0x04):
            self.irq_pending = False
            self.push16(self.pc)
            self.status = (self.status & ~0x10) | 0x20
            self.push(self.status)
            self.status |= 0x04  # FLAG_I = 1
            lo = self.read(0xFFFE)
            hi = self.read(0xFFFF)
            self.pc = (hi << 8) | lo
            self.total_cycles += 7
            return 7

        opcode = self.read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF

        fn, addr_mode, base_cycles = self.opcodes_list[opcode]
        addr, page_crossed = addr_mode()
        extra = fn(addr, page_crossed)
        cycles = base_cycles + (1 if extra else 0)

        self.status |= 0x20  # FLAG_U = 1
        self.total_cycles += cycles
        return cycles

    def run_cycles(self, budget: float) -> float:
        """Fast inline 6502 execution loop using local variables; returns remaining cycle debt."""
        if self.nmi_pending:
            self.nmi_pending = False
            self.push16(self.pc)
            self.status = (self.status & ~0x10) | 0x20
            self.push(self.status)
            self.status |= 0x04
            self.pc = (self.read(0xFFFB) << 8) | self.read(0xFFFA)
            self.total_cycles += 7
            budget -= 7
            if budget <= 0:
                return budget

        if self.irq_pending and not (self.status & 0x04):
            self.irq_pending = False
            self.push16(self.pc)
            self.status = (self.status & ~0x10) | 0x20
            self.push(self.status)
            self.status |= 0x04
            self.pc = (self.read(0xFFFF) << 8) | self.read(0xFFFE)
            self.total_cycles += 7
            budget -= 7
            if budget <= 0:
                return budget

        if HAS_NUMBA and hasattr(self.bus.cart.mapper, 'prg_offsets'):
            ram_np = self._ram_np
            prg_np = self._prg_np
            off_np = self._prg_off_np
            mapper = self.bus.cart.mapper
            while budget > 0:
                offs = mapper.prg_offsets
                off_np[0], off_np[1], off_np[2], off_np[3] = offs[0], offs[1], offs[2], offs[3]
                a, x, y, sp, pc, st, budget, tot, hit_io = jit_cpu_run(
                    budget, self.a, self.x, self.y, self.sp, self.pc, self.status, self.total_cycles,
                    ram_np, prg_np, off_np
                )
                self.a, self.x, self.y, self.sp, self.pc, self.status, self.total_cycles = (
                    a, x, y, sp, pc, st, tot
                )
                if hit_io:
                    budget -= self.step()
            return budget

        prg = self.prg_pages
        if prg is None:
            while budget > 0:
                budget -= self.step()
            return budget

        ram = self.ram
        bus_read = self.bus_read
        bus_write = self.bus_write
        op_table = self.opcodes_list
        a = self.a
        x = self.x
        y = self.y
        sp = self.sp
        pc = self.pc
        st = self.status
        tot = self.total_cycles

        while budget > 0:
            if pc >= 0x8000:
                op = prg[(pc >> 13) & 3][pc & 0x1FFF]
            elif pc < 0x2000:
                op = ram[pc & 0x07FF]
            else:
                op = bus_read(pc)
            pc = (pc + 1) & 0xFFFF

            # --- Fast inline hot opcodes ---
            if op == 0xA9:  # LDA #imm
                a = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else (ram[pc & 0x07FF] if pc < 0x2000 else bus_read(pc))
                pc = (pc + 1) & 0xFFFF
                st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80)
                c = 2
            elif op == 0xA5:  # LDA zp
                zp = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else (ram[pc & 0x07FF] if pc < 0x2000 else bus_read(pc))
                pc = (pc + 1) & 0xFFFF
                a = ram[zp]
                st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80)
                c = 3
            elif op == 0x85:  # STA zp
                zp = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else (ram[pc & 0x07FF] if pc < 0x2000 else bus_read(pc))
                pc = (pc + 1) & 0xFFFF
                ram[zp] = a
                c = 3
            elif op == 0xAD:  # LDA abs
                lo = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc1 = (pc + 1) & 0xFFFF
                hi = prg[(pc1 >> 13) & 3][pc1 & 0x1FFF] if pc1 >= 0x8000 else bus_read(pc1)
                pc = (pc + 2) & 0xFFFF
                addr = (hi << 8) | lo
                a = prg[(addr >> 13) & 3][addr & 0x1FFF] if addr >= 0x8000 else (ram[addr & 0x07FF] if addr < 0x2000 else bus_read(addr))
                st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80)
                c = 4
            elif op == 0x8D:  # STA abs
                lo = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc1 = (pc + 1) & 0xFFFF
                hi = prg[(pc1 >> 13) & 3][pc1 & 0x1FFF] if pc1 >= 0x8000 else bus_read(pc1)
                pc = (pc + 2) & 0xFFFF
                addr = (hi << 8) | lo
                if addr < 0x2000:
                    ram[addr & 0x07FF] = a
                else:
                    bus_write(addr, a)
                c = 4
            elif op == 0xD0:  # BNE
                off = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                if not (st & 0x02):
                    if off & 0x80: off -= 0x100
                    npc = (pc + off) & 0xFFFF
                    c = 4 if (pc & 0xFF00) != (npc & 0xFF00) else 3
                    pc = npc
                else:
                    c = 2
            elif op == 0xF0:  # BEQ
                off = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                if st & 0x02:
                    if off & 0x80: off -= 0x100
                    npc = (pc + off) & 0xFFFF
                    c = 4 if (pc & 0xFF00) != (npc & 0xFF00) else 3
                    pc = npc
                else:
                    c = 2
            elif op == 0x10:  # BPL
                off = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                if not (st & 0x80):
                    if off & 0x80: off -= 0x100
                    npc = (pc + off) & 0xFFFF
                    c = 4 if (pc & 0xFF00) != (npc & 0xFF00) else 3
                    pc = npc
                else:
                    c = 2
            elif op == 0x30:  # BMI
                off = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                if st & 0x80:
                    if off & 0x80: off -= 0x100
                    npc = (pc + off) & 0xFFFF
                    c = 4 if (pc & 0xFF00) != (npc & 0xFF00) else 3
                    pc = npc
                else:
                    c = 2
            elif op == 0x4C:  # JMP abs
                lo = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc1 = (pc + 1) & 0xFFFF
                hi = prg[(pc1 >> 13) & 3][pc1 & 0x1FFF] if pc1 >= 0x8000 else bus_read(pc1)
                pc = (hi << 8) | lo
                c = 3
            elif op == 0x20:  # JSR abs
                lo = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc1 = (pc + 1) & 0xFFFF
                hi = prg[(pc1 >> 13) & 3][pc1 & 0x1FFF] if pc1 >= 0x8000 else bus_read(pc1)
                ret = (pc + 1) & 0xFFFF
                ram[0x0100 + sp] = (ret >> 8) & 0xFF
                sp = (sp - 1) & 0xFF
                ram[0x0100 + sp] = ret & 0xFF
                sp = (sp - 1) & 0xFF
                pc = (hi << 8) | lo
                c = 6
            elif op == 0x60:  # RTS
                sp = (sp + 1) & 0xFF
                lo = ram[0x0100 + sp]
                sp = (sp + 1) & 0xFF
                hi = ram[0x0100 + sp]
                pc = (((hi << 8) | lo) + 1) & 0xFFFF
                c = 6
            elif op == 0x65:  # ADC zp
                zp = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                val = ram[zp]
                res = a + val + (st & 0x01)
                res8 = res & 0xFF
                st = (st & ~0xC3) | (0x01 if res > 0xFF else 0) | (0x40 if (~(a ^ val) & (a ^ res) & 0x80) else 0) | (0x02 if res8 == 0 else 0) | (res8 & 0x80)
                a = res8
                c = 3
            elif op == 0x69:  # ADC #imm
                val = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                res = a + val + (st & 0x01)
                res8 = res & 0xFF
                st = (st & ~0xC3) | (0x01 if res > 0xFF else 0) | (0x40 if (~(a ^ val) & (a ^ res) & 0x80) else 0) | (0x02 if res8 == 0 else 0) | (res8 & 0x80)
                a = res8
                c = 2
            elif op == 0xE8:  # INX
                x = (x + 1) & 0xFF
                st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80)
                c = 2
            elif op == 0xCA:  # DEX
                x = (x - 1) & 0xFF
                st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80)
                c = 2
            elif op == 0xC8:  # INY
                y = (y + 1) & 0xFF
                st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80)
                c = 2
            elif op == 0x88:  # DEY
                y = (y - 1) & 0xFF
                st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80)
                c = 2
            elif op == 0xE6:  # INC zp
                zp = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                v = (ram[zp] + 1) & 0xFF
                ram[zp] = v
                st = (st & 0x7D) | (0x02 if v == 0 else 0) | (v & 0x80)
                c = 5
            elif op == 0xC6:  # DEC zp
                zp = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                v = (ram[zp] - 1) & 0xFF
                ram[zp] = v
                st = (st & 0x7D) | (0x02 if v == 0 else 0) | (v & 0x80)
                c = 5
            elif op == 0xA2:  # LDX #imm
                x = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80)
                c = 2
            elif op == 0xA0:  # LDY #imm
                y = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80)
                c = 2
            elif op == 0xC9:  # CMP #imm
                val = prg[(pc >> 13) & 3][pc & 0x1FFF] if pc >= 0x8000 else bus_read(pc)
                pc = (pc + 1) & 0xFFFF
                res = (a - val) & 0xFF
                st = (st & ~0x83) | (0x01 if a >= val else 0) | (0x02 if res == 0 else 0) | (res & 0x80)
                c = 2
            elif op == 0x18:  # CLC
                st &= ~0x01; c = 2
            elif op == 0x38:  # SEC
                st |= 0x01; c = 2
            elif op == 0xAA:  # TAX
                x = a; st = (st & 0x7D) | (0x02 if x == 0 else 0) | (x & 0x80); c = 2
            elif op == 0x8A:  # TXA
                a = x; st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0xA8:  # TAY
                y = a; st = (st & 0x7D) | (0x02 if y == 0 else 0) | (y & 0x80); c = 2
            elif op == 0x98:  # TYA
                a = y; st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 2
            elif op == 0x48:  # PHA
                ram[0x0100 + sp] = a; sp = (sp - 1) & 0xFF; c = 3
            elif op == 0x68:  # PLA
                sp = (sp + 1) & 0xFF; a = ram[0x0100 + sp]
                st = (st & 0x7D) | (0x02 if a == 0 else 0) | (a & 0x80); c = 4
            elif op == 0xEA:  # NOP
                c = 2
            else:
                # Sync state to self for fallback handler
                self.a, self.x, self.y, self.sp, self.pc, self.status = a, x, y, sp, pc, st
                fn, addr_mode, base_cycles = op_table[op]
                addr, page_crossed = addr_mode()
                extra = fn(addr, page_crossed)
                c = base_cycles + (1 if extra else 0)
                a, x, y, sp, pc, st = self.a, self.x, self.y, self.sp, self.pc, self.status | 0x20

            budget -= c
            tot += c

        self.a, self.x, self.y, self.sp, self.pc, self.status = a, x, y, sp, pc, st | 0x20
        self.total_cycles = tot
        return budget

    # -------------------------------------------------------------------------
    # Clock
    # -------------------------------------------------------------------------
    def clock(self):
        if self.cycles == 0:
            opcode = self.read(self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
            self.set_flag(self.FLAG_U, 1)

            entry = self.opcodes.get(opcode)
            if entry is None:
                self.cycles = 2
            else:
                fn, addr_mode, base_cycles = entry
                self.cycles = base_cycles
                addr, page_crossed = addr_mode()
                extra = fn(addr, page_crossed)
                if extra:
                    self.cycles += 1

            self.set_flag(self.FLAG_U, 1)

        self.cycles -= 1
        self.total_cycles += 1

    # =========================================================================
    # Addressing Modes  (return: effective_address, page_crossed)
    # =========================================================================
    def _imp(self):
        return None, False  # Implied / Accumulator

    def _imm(self):
        addr = self.pc
        self.pc = (self.pc + 1) & 0xFFFF
        return addr, False

    def _zp0(self):
        addr = self.read(self.pc) & 0xFF
        self.pc = (self.pc + 1) & 0xFFFF
        return addr, False

    def _zpx(self):
        addr = (self.read(self.pc) + self.x) & 0xFF
        self.pc = (self.pc + 1) & 0xFFFF
        return addr, False

    def _zpy(self):
        addr = (self.read(self.pc) + self.y) & 0xFF
        self.pc = (self.pc + 1) & 0xFFFF
        return addr, False

    def _abs(self):
        lo = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        hi = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        return (hi << 8) | lo, False

    def _abx(self):
        lo = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        hi = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        base = (hi << 8) | lo
        addr = (base + self.x) & 0xFFFF
        return addr, (addr & 0xFF00) != (base & 0xFF00)

    def _aby(self):
        lo = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        hi = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        base = (hi << 8) | lo
        addr = (base + self.y) & 0xFFFF
        return addr, (addr & 0xFF00) != (base & 0xFF00)

    def _ind(self):
        lo = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        hi = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        ptr = (hi << 8) | lo
        # 6502 page-boundary bug
        if lo == 0xFF:
            elo = self.read(ptr)
            ehi = self.read(ptr & 0xFF00)
        else:
            elo = self.read(ptr)
            ehi = self.read(ptr + 1)
        return (ehi << 8) | elo, False

    def _izx(self):
        t = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        ptr = (t + self.x) & 0xFF
        lo = self.read(ptr)
        hi = self.read((ptr + 1) & 0xFF)
        return (hi << 8) | lo, False

    def _izy(self):
        t = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        lo = self.read(t & 0xFF)
        hi = self.read((t + 1) & 0xFF)
        base = (hi << 8) | lo
        addr = (base + self.y) & 0xFFFF
        return addr, (addr & 0xFF00) != (base & 0xFF00)

    def _rel(self):
        offset = self.read(self.pc);  self.pc = (self.pc + 1) & 0xFFFF
        if offset & 0x80:
            offset -= 0x100
        return (self.pc + offset) & 0xFFFF, False

    # =========================================================================
    # Instructions
    # =========================================================================

    # --- Load / Store ---
    def LDA(self, addr, pc):
        self.a = self.read(addr)
        self.set_nz(self.a)
        return pc

    def LDX(self, addr, pc):
        self.x = self.read(addr)
        self.set_nz(self.x)
        return pc

    def LDY(self, addr, pc):
        self.y = self.read(addr)
        self.set_nz(self.y)
        return pc

    def STA(self, addr, _):
        self.write(addr, self.a)
        return False

    def STX(self, addr, _):
        self.write(addr, self.x)
        return False

    def STY(self, addr, _):
        self.write(addr, self.y)
        return False

    # --- Transfer ---
    def TAX(self, addr, _):
        self.x = self.a; self.set_nz(self.x); return False
    def TAY(self, addr, _):
        self.y = self.a; self.set_nz(self.y); return False
    def TXA(self, addr, _):
        self.a = self.x; self.set_nz(self.a); return False
    def TYA(self, addr, _):
        self.a = self.y; self.set_nz(self.a); return False
    def TSX(self, addr, _):
        self.x = self.sp; self.set_nz(self.x); return False
    def TXS(self, addr, _):
        self.sp = self.x; return False

    # --- Stack ---
    def PHA(self, addr, _):
        self.push(self.a); return False
    def PHP(self, addr, _):
        self.push(self.status | 0x30); return False
    def PLA(self, addr, _):
        self.a = self.pop(); self.set_nz(self.a); return False
    def PLP(self, addr, _):
        self.status = (self.pop() & ~0x10) | 0x20; return False

    # --- Arithmetic ---
    def ADC(self, addr, pc):
        val = self.read(addr)
        a = self.a
        result = a + val + (self.status & 0x01)
        res8 = result & 0xFF
        st = self.status & ~0xC3  # clear N, V, Z, C
        if result > 0xFF:
            st |= 0x01
        if (~(a ^ val) & (a ^ result) & 0x80):
            st |= 0x40
        if res8 == 0:
            st |= 0x02
        self.status = st | (res8 & 0x80)
        self.a = res8
        return pc

    def SBC(self, addr, pc):
        val = self.read(addr) ^ 0xFF
        a = self.a
        result = a + val + (self.status & 0x01)
        res8 = result & 0xFF
        st = self.status & ~0xC3  # clear N, V, Z, C
        if result > 0xFF:
            st |= 0x01
        if ((a ^ result) & (val ^ result) & 0x80):
            st |= 0x40
        if res8 == 0:
            st |= 0x02
        self.status = st | (res8 & 0x80)
        self.a = res8
        return pc

    # --- Logical ---
    def AND(self, addr, pc):
        self.a &= self.read(addr); self.set_nz(self.a); return pc
    def ORA(self, addr, pc):
        self.a |= self.read(addr); self.set_nz(self.a); return pc
    def EOR(self, addr, pc):
        self.a ^= self.read(addr); self.set_nz(self.a); return pc

    # --- Compare ---
    def _cmp(self, reg, addr, pc):
        val = self.read(addr)
        res = (reg - val) & 0xFF
        st = (self.status & ~0x83) | (0x01 if reg >= val else 0) | (0x02 if res == 0 else 0) | (res & 0x80)
        self.status = st
        return pc

    def CMP(self, addr, pc): return self._cmp(self.a, addr, pc)
    def CPX(self, addr, pc): return self._cmp(self.x, addr, pc)
    def CPY(self, addr, pc): return self._cmp(self.y, addr, pc)

    def BIT(self, addr, _):
        val = self.read(addr)
        self.set_flag(self.FLAG_Z, (self.a & val) == 0)
        self.set_flag(self.FLAG_N, (val >> 7) & 1)
        self.set_flag(self.FLAG_V, (val >> 6) & 1)
        return False

    # --- Shift / Rotate ---
    def ASL(self, addr, _):
        if addr is None:  # accumulator mode
            self.set_flag(self.FLAG_C, (self.a >> 7) & 1)
            self.a = (self.a << 1) & 0xFF
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, (val >> 7) & 1)
            new_val = (val << 1) & 0xFF
            if addr >= 0x8000:
                self.write(addr, val)
                self.bus.rmw_second_write = True
                self.write(addr, new_val)
                self.bus.rmw_second_write = False
            else:
                self.write(addr, new_val)
            self.set_nz(new_val)
        return False

    def LSR(self, addr, _):
        if addr is None:
            self.set_flag(self.FLAG_C, self.a & 1)
            self.a >>= 1
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, val & 1)
            new_val = val >> 1
            if addr >= 0x8000:
                self.write(addr, val)
                self.bus.rmw_second_write = True
                self.write(addr, new_val)
                self.bus.rmw_second_write = False
            else:
                self.write(addr, new_val)
            self.set_nz(new_val)
        return False

    def ROL(self, addr, _):
        c = self.get_flag(self.FLAG_C)
        if addr is None:
            self.set_flag(self.FLAG_C, (self.a >> 7) & 1)
            self.a = ((self.a << 1) | c) & 0xFF
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, (val >> 7) & 1)
            new_val = ((val << 1) | c) & 0xFF
            if addr >= 0x8000:
                self.write(addr, val)
                self.bus.rmw_second_write = True
                self.write(addr, new_val)
                self.bus.rmw_second_write = False
            else:
                self.write(addr, new_val)
            self.set_nz(new_val)
        return False

    def ROR(self, addr, _):
        c = self.get_flag(self.FLAG_C)
        if addr is None:
            self.set_flag(self.FLAG_C, self.a & 1)
            self.a = ((c << 7) | (self.a >> 1)) & 0xFF
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, val & 1)
            new_val = ((c << 7) | (val >> 1)) & 0xFF
            if addr >= 0x8000:
                self.write(addr, val)
                self.bus.rmw_second_write = True
                self.write(addr, new_val)
                self.bus.rmw_second_write = False
            else:
                self.write(addr, new_val)
            self.set_nz(new_val)
        return False

    # --- Increment / Decrement ---
    def INC(self, addr, _):
        val = self.read(addr)
        new_val = (val + 1) & 0xFF
        if addr >= 0x8000:
            self.write(addr, val)
            self.bus.rmw_second_write = True
            self.write(addr, new_val)
            self.bus.rmw_second_write = False
        else:
            self.write(addr, new_val)
        self.set_nz(new_val); return False
    def DEC(self, addr, _):
        val = self.read(addr)
        new_val = (val - 1) & 0xFF
        if addr >= 0x8000:
            self.write(addr, val)
            self.bus.rmw_second_write = True
            self.write(addr, new_val)
            self.bus.rmw_second_write = False
        else:
            self.write(addr, new_val)
        self.set_nz(new_val); return False
    def INX(self, addr, _):
        self.x = (self.x + 1) & 0xFF; self.set_nz(self.x); return False
    def INY(self, addr, _):
        self.y = (self.y + 1) & 0xFF; self.set_nz(self.y); return False
    def DEX(self, addr, _):
        self.x = (self.x - 1) & 0xFF; self.set_nz(self.x); return False
    def DEY(self, addr, _):
        self.y = (self.y - 1) & 0xFF; self.set_nz(self.y); return False

    # --- Jumps / Calls ---
    def JMP(self, addr, _):
        self.pc = addr; return False
    def JSR(self, addr, _):
        self.push16(self.pc - 1); self.pc = addr; return False
    def RTS(self, addr, _):
        self.pc = (self.pop16() + 1) & 0xFFFF; return False
    def RTI(self, addr, _):
        self.status = (self.pop() & ~0x10) | 0x20
        self.pc = self.pop16()
        return False

    # --- Branches ---
    def _branch(self, condition, addr):
        if condition:
            extra = 1
            if (self.pc & 0xFF00) != (addr & 0xFF00):
                extra = 2
            self.pc = addr
            return extra
        return 0

    def BCC(self, addr, _): return self._branch((self.status & 0x01) == 0, addr)
    def BCS(self, addr, _): return self._branch((self.status & 0x01) != 0, addr)
    def BEQ(self, addr, _): return self._branch((self.status & 0x02) != 0, addr)
    def BNE(self, addr, _): return self._branch((self.status & 0x02) == 0, addr)
    def BMI(self, addr, _): return self._branch((self.status & 0x80) != 0, addr)
    def BPL(self, addr, _): return self._branch((self.status & 0x80) == 0, addr)
    def BVC(self, addr, _): return self._branch((self.status & 0x40) == 0, addr)
    def BVS(self, addr, _): return self._branch((self.status & 0x40) != 0, addr)

    # --- Flag operations ---
    def CLC(self, addr, _): self.status &= ~0x01; return False
    def CLD(self, addr, _): self.status &= ~0x08; return False
    def CLI(self, addr, _): self.status &= ~0x04; return False
    def CLV(self, addr, _): self.status &= ~0x40; return False
    def SEC(self, addr, _): self.status |= 0x01; return False
    def SED(self, addr, _): self.status |= 0x08; return False
    def SEI(self, addr, _): self.status |= 0x04; return False

    # --- System ---
    def NOP(self, addr, _): return False
    def BRK(self, addr, _):
        self.pc = (self.pc + 1) & 0xFFFF
        self.set_flag(self.FLAG_I, 1)
        self.push16(self.pc)
        self.set_flag(self.FLAG_B, 1)
        self.push(self.status)
        self.set_flag(self.FLAG_B, 0)
        lo = self.read(0xFFFE)
        hi = self.read(0xFFFF)
        self.pc = (hi << 8) | lo
        return False

    # =========================================================================
    # Opcode Table
    # =========================================================================
    def _build_opcode_table(self):
        I = self._imp
        M = self._imm
        Z = self._zp0
        ZX = self._zpx
        ZY = self._zpy
        A = self._abs
        AX = self._abx
        AY = self._aby
        IN = self._ind
        IX = self._izx
        IY = self._izy
        R = self._rel

        # Format: opcode -> (method, addr_mode_fn, base_cycles)
        self.opcodes = {
            0x00: (self.BRK, I,  7),  0x01: (self.ORA, IX, 6),  0x05: (self.ORA, Z,  3),
            0x06: (self.ASL, Z,  5),  0x08: (self.PHP, I,  3),  0x09: (self.ORA, M,  2),
            0x0A: (self.ASL, I,  2),  0x0D: (self.ORA, A,  4),  0x0E: (self.ASL, A,  6),
            0x10: (self.BPL, R,  2),  0x11: (self.ORA, IY, 5),  0x15: (self.ORA, ZX, 4),
            0x16: (self.ASL, ZX, 6),  0x18: (self.CLC, I,  2),  0x19: (self.ORA, AY, 4),
            0x1D: (self.ORA, AX, 4),  0x1E: (self.ASL, AX, 7),
            0x20: (self.JSR, A,  6),  0x21: (self.AND, IX, 6),  0x24: (self.BIT, Z,  3),
            0x25: (self.AND, Z,  3),  0x26: (self.ROL, Z,  5),  0x28: (self.PLP, I,  4),
            0x29: (self.AND, M,  2),  0x2A: (self.ROL, I,  2),  0x2C: (self.BIT, A,  4),
            0x2D: (self.AND, A,  4),  0x2E: (self.ROL, A,  6),
            0x30: (self.BMI, R,  2),  0x31: (self.AND, IY, 5),  0x35: (self.AND, ZX, 4),
            0x36: (self.ROL, ZX, 6),  0x38: (self.SEC, I,  2),  0x39: (self.AND, AY, 4),
            0x3D: (self.AND, AX, 4),  0x3E: (self.ROL, AX, 7),
            0x40: (self.RTI, I,  6),  0x41: (self.EOR, IX, 6),  0x45: (self.EOR, Z,  3),
            0x46: (self.LSR, Z,  5),  0x48: (self.PHA, I,  3),  0x49: (self.EOR, M,  2),
            0x4A: (self.LSR, I,  2),  0x4C: (self.JMP, A,  3),  0x4D: (self.EOR, A,  4),
            0x4E: (self.LSR, A,  6),
            0x50: (self.BVC, R,  2),  0x51: (self.EOR, IY, 5),  0x55: (self.EOR, ZX, 4),
            0x56: (self.LSR, ZX, 6),  0x58: (self.CLI, I,  2),  0x59: (self.EOR, AY, 4),
            0x5D: (self.EOR, AX, 4),  0x5E: (self.LSR, AX, 7),
            0x60: (self.RTS, I,  6),  0x61: (self.ADC, IX, 6),  0x65: (self.ADC, Z,  3),
            0x66: (self.ROR, Z,  5),  0x68: (self.PLA, I,  4),  0x69: (self.ADC, M,  2),
            0x6A: (self.ROR, I,  2),  0x6C: (self.JMP, IN, 5),  0x6D: (self.ADC, A,  4),
            0x6E: (self.ROR, A,  6),
            0x70: (self.BVS, R,  2),  0x71: (self.ADC, IY, 5),  0x75: (self.ADC, ZX, 4),
            0x76: (self.ROR, ZX, 6),  0x78: (self.SEI, I,  2),  0x79: (self.ADC, AY, 4),
            0x7D: (self.ADC, AX, 4),  0x7E: (self.ROR, AX, 7),
            0x81: (self.STA, IX, 6),  0x84: (self.STY, Z,  3),  0x85: (self.STA, Z,  3),
            0x86: (self.STX, Z,  3),  0x88: (self.DEY, I,  2),  0x8A: (self.TXA, I,  2),
            0x8C: (self.STY, A,  4),  0x8D: (self.STA, A,  4),  0x8E: (self.STX, A,  4),
            0x90: (self.BCC, R,  2),  0x91: (self.STA, IY, 6),  0x94: (self.STY, ZX, 4),
            0x95: (self.STA, ZX, 4),  0x96: (self.STX, ZY, 4),  0x98: (self.TYA, I,  2),
            0x99: (self.STA, AY, 5),  0x9A: (self.TXS, I,  2),  0x9D: (self.STA, AX, 5),
            0xA0: (self.LDY, M,  2),  0xA1: (self.LDA, IX, 6),  0xA2: (self.LDX, M,  2),
            0xA4: (self.LDY, Z,  3),  0xA5: (self.LDA, Z,  3),  0xA6: (self.LDX, Z,  3),
            0xA8: (self.TAY, I,  2),  0xA9: (self.LDA, M,  2),  0xAA: (self.TAX, I,  2),
            0xAC: (self.LDY, A,  4),  0xAD: (self.LDA, A,  4),  0xAE: (self.LDX, A,  4),
            0xB0: (self.BCS, R,  2),  0xB1: (self.LDA, IY, 5),  0xB4: (self.LDY, ZX, 4),
            0xB5: (self.LDA, ZX, 4),  0xB6: (self.LDX, ZY, 4),  0xB8: (self.CLV, I,  2),
            0xB9: (self.LDA, AY, 4),  0xBA: (self.TSX, I,  2),  0xBC: (self.LDY, AX, 4),
            0xBD: (self.LDA, AX, 4),  0xBE: (self.LDX, AY, 4),
            0xC0: (self.CPY, M,  2),  0xC1: (self.CMP, IX, 6),  0xC4: (self.CPY, Z,  3),
            0xC5: (self.CMP, Z,  3),  0xC6: (self.DEC, Z,  5),  0xC8: (self.INY, I,  2),
            0xC9: (self.CMP, M,  2),  0xCA: (self.DEX, I,  2),  0xCC: (self.CPY, A,  4),
            0xCD: (self.CMP, A,  4),  0xCE: (self.DEC, A,  6),
            0xD0: (self.BNE, R,  2),  0xD1: (self.CMP, IY, 5),  0xD5: (self.CMP, ZX, 4),
            0xD6: (self.DEC, ZX, 6),  0xD8: (self.CLD, I,  2),  0xD9: (self.CMP, AY, 4),
            0xDD: (self.CMP, AX, 4),  0xDE: (self.DEC, AX, 7),
            0xE0: (self.CPX, M,  2),  0xE1: (self.SBC, IX, 6),  0xE4: (self.CPX, Z,  3),
            0xE5: (self.SBC, Z,  3),  0xE6: (self.INC, Z,  5),  0xE8: (self.INX, I,  2),
            0xE9: (self.SBC, M,  2),  0xEA: (self.NOP, I,  2),  0xEC: (self.CPX, A,  4),
            0xED: (self.SBC, A,  4),  0xEE: (self.INC, A,  6),
            0xF0: (self.BEQ, R,  2),  0xF1: (self.SBC, IY, 5),  0xF5: (self.SBC, ZX, 4),
            0xF6: (self.INC, ZX, 6),  0xF8: (self.SED, I,  2),  0xF9: (self.SBC, AY, 4),
            0xFD: (self.SBC, AX, 4),  0xFE: (self.INC, AX, 7),
        }

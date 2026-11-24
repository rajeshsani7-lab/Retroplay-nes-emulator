"""
NES MOS 6502 CPU Emulator
Implements the full 6502 instruction set used by the NES.
"""

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

        # Registers
        self.a = 0x00       # Accumulator
        self.x = 0x00       # X Index
        self.y = 0x00       # Y Index
        self.sp = 0xFD      # Stack Pointer
        self.pc = 0x0000    # Program Counter
        self.status = 0x24  # Processor Status (bit 5 always set)

        self.cycles = 0     # Remaining cycles for current instruction
        self.total_cycles = 0

        # Build instruction table
        self._build_opcode_table()

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
        """Set Negative and Zero flags based on value."""
        self.set_flag(self.FLAG_Z, (value & 0xFF) == 0)
        self.set_flag(self.FLAG_N, (value >> 7) & 1)

    # -------------------------------------------------------------------------
    # Bus access
    # -------------------------------------------------------------------------
    def read(self, addr):
        return self.bus.cpu_read(addr & 0xFFFF)

    def write(self, addr, data):
        self.bus.cpu_write(addr & 0xFFFF, data & 0xFF)

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
        lo = self.read(0xFFFC)
        hi = self.read(0xFFFD)
        self.pc = (hi << 8) | lo
        self.cycles = 8
        self.total_cycles = 0

    def irq(self):
        if self.get_flag(self.FLAG_I) == 0:
            self.push16(self.pc)
            self.set_flag(self.FLAG_B, 0)
            self.set_flag(self.FLAG_U, 1)
            self.set_flag(self.FLAG_I, 1)
            self.push(self.status)
            lo = self.read(0xFFFE)
            hi = self.read(0xFFFF)
            self.pc = (hi << 8) | lo
            self.cycles = 7

    def nmi(self):
        self.push16(self.pc)
        self.set_flag(self.FLAG_B, 0)
        self.set_flag(self.FLAG_U, 1)
        self.set_flag(self.FLAG_I, 1)
        self.push(self.status)
        lo = self.read(0xFFFA)
        hi = self.read(0xFFFB)
        self.pc = (hi << 8) | lo
        self.cycles = 8

    # -------------------------------------------------------------------------
    # Clock
    # -------------------------------------------------------------------------
    def clock(self):
        if self.cycles == 0:
            opcode = self.read(self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
            self.set_flag(self.FLAG_U, 1)

            instr = self.opcodes.get(opcode)
            if instr is None:
                # Unknown opcode — NOP
                self.cycles = 2
            else:
                name, addr_mode, base_cycles = instr
                self.cycles = base_cycles
                addr, page_crossed = addr_mode()
                extra = getattr(self, name)(addr, page_crossed)
                if extra:
                    self.cycles += 1

            self.set_flag(self.FLAG_U, 1)

        self.cycles -= 1
        self.total_cycles += 1

    # =========================================================================
    # Addressing Modes  (return: effective_address, page_crossed)
    # =========================================================================
    def _imp(self):
        return 0, False  # Implied / Accumulator

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
        return self.pc + offset, False

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
        result = self.a + val + self.get_flag(self.FLAG_C)
        self.set_flag(self.FLAG_C, result > 0xFF)
        self.set_flag(self.FLAG_V, (~(self.a ^ val) & (self.a ^ result) & 0x80) != 0)
        self.a = result & 0xFF
        self.set_nz(self.a)
        return pc

    def SBC(self, addr, pc):
        val = self.read(addr) ^ 0xFF
        result = self.a + val + self.get_flag(self.FLAG_C)
        self.set_flag(self.FLAG_C, result > 0xFF)
        self.set_flag(self.FLAG_V, ((self.a ^ result) & (val ^ result) & 0x80) != 0)
        self.a = result & 0xFF
        self.set_nz(self.a)
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
        result = reg - val
        self.set_flag(self.FLAG_C, reg >= val)
        self.set_nz(result & 0xFF)
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
        if addr == 0:  # accumulator mode
            self.set_flag(self.FLAG_C, (self.a >> 7) & 1)
            self.a = (self.a << 1) & 0xFF
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, (val >> 7) & 1)
            val = (val << 1) & 0xFF
            self.write(addr, val)
            self.set_nz(val)
        return False

    def LSR(self, addr, _):
        if addr == 0:
            self.set_flag(self.FLAG_C, self.a & 1)
            self.a >>= 1
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, val & 1)
            val >>= 1
            self.write(addr, val)
            self.set_nz(val)
        return False

    def ROL(self, addr, _):
        c = self.get_flag(self.FLAG_C)
        if addr == 0:
            self.set_flag(self.FLAG_C, (self.a >> 7) & 1)
            self.a = ((self.a << 1) | c) & 0xFF
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, (val >> 7) & 1)
            val = ((val << 1) | c) & 0xFF
            self.write(addr, val)
            self.set_nz(val)
        return False

    def ROR(self, addr, _):
        c = self.get_flag(self.FLAG_C)
        if addr == 0:
            self.set_flag(self.FLAG_C, self.a & 1)
            self.a = ((c << 7) | (self.a >> 1)) & 0xFF
            self.set_nz(self.a)
        else:
            val = self.read(addr)
            self.set_flag(self.FLAG_C, val & 1)
            val = ((c << 7) | (val >> 1)) & 0xFF
            self.write(addr, val)
            self.set_nz(val)
        return False

    # --- Increment / Decrement ---
    def INC(self, addr, _):
        val = (self.read(addr) + 1) & 0xFF
        self.write(addr, val); self.set_nz(val); return False
    def DEC(self, addr, _):
        val = (self.read(addr) - 1) & 0xFF
        self.write(addr, val); self.set_nz(val); return False
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

    def BCC(self, addr, _): return self._branch(self.get_flag(self.FLAG_C) == 0, addr)
    def BCS(self, addr, _): return self._branch(self.get_flag(self.FLAG_C) == 1, addr)
    def BEQ(self, addr, _): return self._branch(self.get_flag(self.FLAG_Z) == 1, addr)
    def BNE(self, addr, _): return self._branch(self.get_flag(self.FLAG_Z) == 0, addr)
    def BMI(self, addr, _): return self._branch(self.get_flag(self.FLAG_N) == 1, addr)
    def BPL(self, addr, _): return self._branch(self.get_flag(self.FLAG_N) == 0, addr)
    def BVC(self, addr, _): return self._branch(self.get_flag(self.FLAG_V) == 0, addr)
    def BVS(self, addr, _): return self._branch(self.get_flag(self.FLAG_V) == 1, addr)

    # --- Flag operations ---
    def CLC(self, addr, _): self.set_flag(self.FLAG_C, 0); return False
    def CLD(self, addr, _): self.set_flag(self.FLAG_D, 0); return False
    def CLI(self, addr, _): self.set_flag(self.FLAG_I, 0); return False
    def CLV(self, addr, _): self.set_flag(self.FLAG_V, 0); return False
    def SEC(self, addr, _): self.set_flag(self.FLAG_C, 1); return False
    def SED(self, addr, _): self.set_flag(self.FLAG_D, 1); return False
    def SEI(self, addr, _): self.set_flag(self.FLAG_I, 1); return False

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

        # Format: opcode -> (method_name, addr_mode_fn, base_cycles)
        self.opcodes = {
            0x00: ('BRK', I,  7),  0x01: ('ORA', IX, 6),  0x05: ('ORA', Z,  3),
            0x06: ('ASL', Z,  5),  0x08: ('PHP', I,  3),  0x09: ('ORA', M,  2),
            0x0A: ('ASL', I,  2),  0x0D: ('ORA', A,  4),  0x0E: ('ASL', A,  6),
            0x10: ('BPL', R,  2),  0x11: ('ORA', IY, 5),  0x15: ('ORA', ZX, 4),
            0x16: ('ASL', ZX, 6),  0x18: ('CLC', I,  2),  0x19: ('ORA', AY, 4),
            0x1D: ('ORA', AX, 4),  0x1E: ('ASL', AX, 7),
            0x20: ('JSR', A,  6),  0x21: ('AND', IX, 6),  0x24: ('BIT', Z,  3),
            0x25: ('AND', Z,  3),  0x26: ('ROL', Z,  5),  0x28: ('PLP', I,  4),
            0x29: ('AND', M,  2),  0x2A: ('ROL', I,  2),  0x2C: ('BIT', A,  4),
            0x2D: ('AND', A,  4),  0x2E: ('ROL', A,  6),
            0x30: ('BMI', R,  2),  0x31: ('AND', IY, 5),  0x35: ('AND', ZX, 4),
            0x36: ('ROL', ZX, 6),  0x38: ('SEC', I,  2),  0x39: ('AND', AY, 4),
            0x3D: ('AND', AX, 4),  0x3E: ('ROL', AX, 7),
            0x40: ('RTI', I,  6),  0x41: ('EOR', IX, 6),  0x45: ('EOR', Z,  3),
            0x46: ('LSR', Z,  5),  0x48: ('PHA', I,  3),  0x49: ('EOR', M,  2),
            0x4A: ('LSR', I,  2),  0x4C: ('JMP', A,  3),  0x4D: ('EOR', A,  4),
            0x4E: ('LSR', A,  6),
            0x50: ('BVC', R,  2),  0x51: ('EOR', IY, 5),  0x55: ('EOR', ZX, 4),
            0x56: ('LSR', ZX, 6),  0x58: ('CLI', I,  2),  0x59: ('EOR', AY, 4),
            0x5D: ('EOR', AX, 4),  0x5E: ('LSR', AX, 7),
            0x60: ('RTS', I,  6),  0x61: ('ADC', IX, 6),  0x65: ('ADC', Z,  3),
            0x66: ('ROR', Z,  5),  0x68: ('PLA', I,  4),  0x69: ('ADC', M,  2),
            0x6A: ('ROR', I,  2),  0x6C: ('JMP', IN, 5),  0x6D: ('ADC', A,  4),
            0x6E: ('ROR', A,  6),
            0x70: ('BVS', R,  2),  0x71: ('ADC', IY, 5),  0x75: ('ADC', ZX, 4),
            0x76: ('ROR', ZX, 6),  0x78: ('SEI', I,  2),  0x79: ('ADC', AY, 4),
            0x7D: ('ADC', AX, 4),  0x7E: ('ROR', AX, 7),
            0x81: ('STA', IX, 6),  0x84: ('STY', Z,  3),  0x85: ('STA', Z,  3),
            0x86: ('STX', Z,  3),  0x88: ('DEY', I,  2),  0x8A: ('TXA', I,  2),
            0x8C: ('STY', A,  4),  0x8D: ('STA', A,  4),  0x8E: ('STX', A,  4),
            0x90: ('BCC', R,  2),  0x91: ('STA', IY, 6),  0x94: ('STY', ZX, 4),
            0x95: ('STA', ZX, 4),  0x96: ('STX', ZY, 4),  0x98: ('TYA', I,  2),
            0x99: ('STA', AY, 5),  0x9A: ('TXS', I,  2),  0x9D: ('STA', AX, 5),
            0xA0: ('LDY', M,  2),  0xA1: ('LDA', IX, 6),  0xA2: ('LDX', M,  2),
            0xA4: ('LDY', Z,  3),  0xA5: ('LDA', Z,  3),  0xA6: ('LDX', Z,  3),
            0xA8: ('TAY', I,  2),  0xA9: ('LDA', M,  2),  0xAA: ('TAX', I,  2),
            0xAC: ('LDY', A,  4),  0xAD: ('LDA', A,  4),  0xAE: ('LDX', A,  4),
            0xB0: ('BCS', R,  2),  0xB1: ('LDA', IY, 5),  0xB4: ('LDY', ZX, 4),
            0xB5: ('LDA', ZX, 4),  0xB6: ('LDX', ZY, 4),  0xB8: ('CLV', I,  2),
            0xB9: ('LDA', AY, 4),  0xBA: ('TSX', I,  2),  0xBC: ('LDY', AX, 4),
            0xBD: ('LDA', AX, 4),  0xBE: ('LDX', AY, 4),
            0xC0: ('CPY', M,  2),  0xC1: ('CMP', IX, 6),  0xC4: ('CPY', Z,  3),
            0xC5: ('CMP', Z,  3),  0xC6: ('DEC', Z,  5),  0xC8: ('INY', I,  2),
            0xC9: ('CMP', M,  2),  0xCA: ('DEX', I,  2),  0xCC: ('CPY', A,  4),
            0xCD: ('CMP', A,  4),  0xCE: ('DEC', A,  6),
            0xD0: ('BNE', R,  2),  0xD1: ('CMP', IY, 5),  0xD5: ('CMP', ZX, 4),
            0xD6: ('DEC', ZX, 6),  0xD8: ('CLD', I,  2),  0xD9: ('CMP', AY, 4),
            0xDD: ('CMP', AX, 4),  0xDE: ('DEC', AX, 7),
            0xE0: ('CPX', M,  2),  0xE1: ('SBC', IX, 6),  0xE4: ('CPX', Z,  3),
            0xE5: ('SBC', Z,  3),  0xE6: ('INC', Z,  5),  0xE8: ('INX', I,  2),
            0xE9: ('SBC', M,  2),  0xEA: ('NOP', I,  2),  0xEC: ('CPX', A,  4),
            0xED: ('SBC', A,  4),  0xEE: ('INC', A,  6),
            0xF0: ('BEQ', R,  2),  0xF1: ('SBC', IY, 5),  0xF5: ('SBC', ZX, 4),
            0xF6: ('INC', ZX, 6),  0xF8: ('SED', I,  2),  0xF9: ('SBC', AY, 4),
            0xFD: ('SBC', AX, 4),  0xFE: ('INC', AX, 7),
        }

"""
NES Cartridge / ROM Loader
Supports iNES format (.nes files).

Supported Mappers:
  0  - NROM     (Donkey Kong, Excitebike, Balloon Fight)
  1  - MMC1     (Mega Man 2, Zelda, Metroid)
  2  - UxROM    (Contra, Duck Tales, Mega Man, Castlevania)
  3  - CNROM    (Donkey Kong Jr, Popeye)
  227- Mapper227 (1200-in-1, 57-in-1 multicarts)
"""

MAPPER_REGISTRY = {}

def register_mapper(mapper_id):
    def decorator(cls):
        MAPPER_REGISTRY[mapper_id] = cls
        return cls
    return decorator


class Header:
    """Parses a 16-byte iNES header."""
    def __init__(self, raw):
        if raw[:4] != b'NES\x1a':
            raise ValueError("Not a valid iNES ROM file!")

        self.prg_rom_size = raw[4] * 16384   # 16 KB units
        self.chr_rom_size = raw[5] * 8192    # 8 KB units
        flags6 = raw[6]
        flags7 = raw[7]

        self.mirroring = 'vertical' if (flags6 & 0x01) else 'horizontal'
        self.has_battery_ram = bool(flags6 & 0x02)
        self.has_trainer = bool(flags6 & 0x04)
        self.four_screen = bool(flags6 & 0x08)
        self.mapper_id = ((flags7 & 0xF0) | (flags6 >> 4))

        # iNES 2.0 detection
        self.ines2 = (flags7 & 0x0C) == 0x08


class Cartridge:
    """Loads a .nes ROM and provides access to PRG/CHR data via a mapper."""

    def __init__(self, rom_path: str):
        with open(rom_path, 'rb') as f:
            data = f.read()

        self.header = Header(data[:16])
        offset = 16
        if self.header.has_trainer:
            offset += 512  # skip trainer

        prg_end = offset + self.header.prg_rom_size
        self.prg_rom = bytearray(data[offset:prg_end])

        chr_end = prg_end + self.header.chr_rom_size
        if self.header.chr_rom_size > 0:
            self.chr_rom = bytearray(data[prg_end:chr_end])
        else:
            # CHR-RAM: 8 KB writable
            self.chr_rom = bytearray(8192)

        # PRG-RAM (battery-backed save RAM)
        self.prg_ram = bytearray(8192)

        mapper_cls = MAPPER_REGISTRY.get(self.header.mapper_id)
        if mapper_cls is None:
            raise NotImplementedError(
                f"Mapper {self.header.mapper_id} is not yet supported."
            )
        self.mapper = mapper_cls(self)

        print(f"[ROM] Loaded: {rom_path}")
        print(f"      Mapper : {self.header.mapper_id}")
        print(f"      PRG ROM: {self.header.prg_rom_size // 1024} KB")
        print(f"      CHR ROM: {self.header.chr_rom_size // 1024} KB")
        print(f"      Mirror : {self.header.mirroring}")

    # CPU-side access
    def cpu_read(self, addr):
        return self.mapper.cpu_read(addr)

    def cpu_write(self, addr, data):
        self.mapper.cpu_write(addr, data)

    # PPU-side access
    def ppu_read(self, addr):
        return self.mapper.ppu_read(addr)

    def ppu_write(self, addr, data):
        self.mapper.ppu_write(addr, data)

    @property
    def mirroring(self):
        return self.mapper.get_mirroring()


# =============================================================================
# Mapper 0 — NROM
# Used by: Donkey Kong, Excitebike, Balloon Fight, etc.
# =============================================================================
@register_mapper(0)
class Mapper0:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.mask = 0x3FFF if self.prg_len == 16384 else 0x7FFF

    def cpu_read(self, addr):
        if addr >= 0x8000:
            return self.prg_rom[(addr - 0x8000) & self.mask]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data

    def ppu_read(self, addr):
        if addr < 0x2000:
            return self.cart.chr_rom[addr]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000:
            self.cart.chr_rom[addr] = data  # CHR-RAM only

    def get_mirroring(self):
        return self.cart.header.mirroring


# =============================================================================
# Mapper 1 — MMC1
# Used by: Mega Man 2, Zelda, Metroid, etc.
# =============================================================================
@register_mapper(1)
class Mapper1:
    def __init__(self, cart):
        self.cart = cart
        self.shift = 0x10
        self.control = 0x0C
        self.chr_bank0 = 0
        self.chr_bank1 = 0
        self.prg_bank = 0
        self.mirroring_mode = cart.header.mirroring

    def _write_register(self, addr, data):
        if data & 0x80:
            self.shift = 0x10
            self.control |= 0x0C
            return
        complete = self.shift & 1
        self.shift = ((data & 1) << 4) | (self.shift >> 1)
        if complete:
            reg = (addr >> 13) & 3
            val = self.shift
            self.shift = 0x10
            if reg == 0:
                self.control = val
                m = val & 3
                self.mirroring_mode = ['single0', 'single1', 'vertical', 'horizontal'][m]
            elif reg == 1:
                self.chr_bank0 = val & 0x1F
            elif reg == 2:
                self.chr_bank1 = val & 0x1F
            elif reg == 3:
                self.prg_bank = val & 0x0F

    def cpu_read(self, addr):
        if 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        if addr >= 0x8000:
            mode = (self.control >> 2) & 3
            prg_size = len(self.cart.prg_rom)
            if mode in (0, 1):
                bank = (self.prg_bank & ~1) * 16384
                return self.cart.prg_rom[(bank + (addr - 0x8000)) % prg_size]
            elif mode == 2:
                if addr < 0xC000:
                    return self.cart.prg_rom[(addr - 0x8000) % prg_size]
                else:
                    bank = self.prg_bank * 16384
                    return self.cart.prg_rom[(bank + (addr - 0xC000)) % prg_size]
            else:
                if addr < 0xC000:
                    bank = self.prg_bank * 16384
                    return self.cart.prg_rom[(bank + (addr - 0x8000)) % prg_size]
                else:
                    return self.cart.prg_rom[(addr - 0x8000 + prg_size - 16384) % prg_size]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data
        elif addr >= 0x8000:
            self._write_register(addr, data)

    def ppu_read(self, addr):
        if addr < 0x1000:
            bank = self.chr_bank0 * 4096
            return self.cart.chr_rom[(bank + addr) % len(self.cart.chr_rom)]
        elif addr < 0x2000:
            bank = self.chr_bank1 * 4096
            return self.cart.chr_rom[(bank + (addr - 0x1000)) % len(self.cart.chr_rom)]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000:
            self.cart.chr_rom[addr % len(self.cart.chr_rom)] = data

    def get_mirroring(self):
        return self.mirroring_mode


# =============================================================================
# Mapper 2 — UxROM
# Used by: Contra, Duck Tales, Mega Man, Castlevania
# =============================================================================
@register_mapper(2)
class Mapper2:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.prg_bank_lo_base = 0 - 0x8000
        self.prg_bank_hi_base = (self.prg_len - 16384) - 0xC000

    def cpu_read(self, addr):
        if addr >= 0xC000:
            return self.prg_rom[self.prg_bank_hi_base + addr]
        elif addr >= 0x8000:
            return self.prg_rom[self.prg_bank_lo_base + addr]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def cpu_write(self, addr, data):
        if addr >= 0x8000:
            bank = (data & 0x0F) * 16384
            self.prg_bank_lo_base = (bank % self.prg_len) - 0x8000

    def ppu_read(self, addr):
        if addr < 0x2000:
            return self.cart.chr_rom[addr % len(self.cart.chr_rom)]
        return 0

    def ppu_write(self, addr, data):
        pass

    def get_mirroring(self):
        return self.cart.header.mirroring


# =============================================================================
# Mapper 3 — CNROM
# Used by: Donkey Kong Jr, Popeye, some Contra variants
# =============================================================================
@register_mapper(3)
class Mapper3:
    def __init__(self, cart):
        self.cart = cart
        self.chr_bank_select = 0

    def cpu_read(self, addr):
        if addr >= 0x8000:
            return self.cart.prg_rom[(addr - 0x8000) % len(self.cart.prg_rom)]
        return 0

    def cpu_write(self, addr, data):
        if addr >= 0x8000:
            self.chr_bank_select = data & 0x03

    def ppu_read(self, addr):
        if addr < 0x2000:
            bank = self.chr_bank_select * 8192
            return self.cart.chr_rom[(bank + addr) % len(self.cart.chr_rom)]
        return 0

    def ppu_write(self, addr, data):
        pass  # CHR-ROM read only

    def get_mirroring(self):
        return self.cart.header.mirroring


# =============================================================================
# Mapper 227 — 1200-in-1 / 57-in-1 Multicart (Address Latch)
# =============================================================================
@register_mapper(227)
class Mapper227:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.chr_rom = cart.chr_rom
        self.chr_len = len(cart.chr_rom)
        self.prg_bank_lo = 0
        self.prg_bank_hi = 0
        self.prg_lo_base = 0 - 0x8000
        self.prg_hi_base = 0 - 0xC000
        self._mirroring = 'vertical'
        self.chr_writable = True
        self.sync(0)

    def sync(self, latche):
        S = latche & 1
        p = ((latche >> 2) & 0x1F) + ((latche & 0x100) >> 3)
        L = (latche >> 9) & 1

        # CHR-RAM write protect when bit 7 is set
        self.chr_writable = not bool((latche & 0x80) == 0x80)

        if (latche >> 7) & 1:
            if S:
                bank = (p >> 1) * 2
                self.prg_bank_lo = bank
                self.prg_bank_hi = bank + 1
            else:
                self.prg_bank_lo = p
                self.prg_bank_hi = p
        else:
            if S:
                if L:
                    self.prg_bank_lo = p & 0x3E
                    self.prg_bank_hi = p | 7
                else:
                    self.prg_bank_lo = p & 0x3E
                    self.prg_bank_hi = p & 0x38
            else:
                if L:
                    self.prg_bank_lo = p
                    self.prg_bank_hi = p | 7
                else:
                    self.prg_bank_lo = p
                    self.prg_bank_hi = p & 0x38

        m = ((latche >> 1) & 1) ^ 1
        self._mirroring = 'vertical' if m else 'horizontal'
        self.prg_lo_base = ((self.prg_bank_lo * 0x4000) % self.prg_len) - 0x8000
        self.prg_hi_base = ((self.prg_bank_hi * 0x4000) % self.prg_len) - 0xC000

    def cpu_write(self, addr, data):
        if addr >= 0x8000:
            self.sync(addr)

    def cpu_read(self, addr):
        if addr >= 0xC000:
            return self.prg_rom[self.prg_hi_base + addr]
        elif addr >= 0x8000:
            return self.prg_rom[self.prg_lo_base + addr]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def ppu_read(self, addr):
        if addr < 0x2000:
            return self.chr_rom[addr % self.chr_len] if self.chr_len else 0
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000 and self.chr_writable and self.chr_len:
            self.chr_rom[addr % self.chr_len] = data

    def get_mirroring(self):
        return self._mirroring

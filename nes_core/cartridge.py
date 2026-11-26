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

        # iNES 2.0 detection & TV region (0 = NTSC, 1 = PAL)
        self.ines2 = (flags7 & 0x0C) == 0x08
        if self.ines2:
            self.is_pal = (raw[12] & 0x03) == 1
        else:
            self.is_pal = bool(raw[9] & 0x01)


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

        # Detect PAL vs NTSC from iNES header or standard filename region tags
        fname_lower = rom_path.lower()
        pal_tags = ("(europe)", "(e)", "(pal)", "(germany)", "(france)", "(spain)", "(italy)", "(uk)", "(australia)", "(sweden)")
        self.region = "PAL" if (self.header.is_pal or any(t in fname_lower for t in pal_tags)) else "NTSC"

        print(f"[ROM] Loaded: {rom_path}")
        print(f"      Mapper : {self.header.mapper_id}")
        print(f"      Region : {self.region} ({'50Hz' if self.region == 'PAL' else '60Hz'})")
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

    def clock_scanline(self):
        if hasattr(self.mapper, 'clock_scanline'):
            return self.mapper.clock_scanline()
        return False


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
        self.chr_offsets = [i * 1024 for i in range(8)]
        mv = memoryview(self.prg_rom)
        if self.prg_len == 16384:
            self.prg_pages = [mv[0:8192], mv[8192:16384], mv[0:8192], mv[8192:16384]]
            self.prg_offsets = [0, 8192, 0, 8192]
        else:
            self.prg_pages = [mv[0:8192], mv[8192:16384], mv[16384:24576], mv[24576:32768]]
            self.prg_offsets = [0, 8192, 16384, 24576]

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
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.shift = 0x10
        self.control = 0x0C
        self.chr_bank0 = 0
        self.chr_bank1 = 0
        self.prg_bank = 0
        self.mirroring_mode = cart.header.mirroring
        self.prg_lo_base = 0 - 0x8000
        self.prg_hi_base = (self.prg_len - 16384) - 0xC000
        self.prg_mv = memoryview(self.prg_rom)
        self.prg_pages = [None, None, None, None]
        self.prg_offsets = [0, 8192, 0, 8192]
        self.chr_len = len(cart.chr_rom)
        self.chr_offsets = [i * 1024 for i in range(8)]
        self._update_prg_bases()
        self._update_chr_bases()

    def _update_chr_bases(self):
        chr_len = self.chr_len
        if self.control & 0x10:
            b0 = (self.chr_bank0 * 4096) % chr_len
            b1 = (self.chr_bank1 * 4096) % chr_len
            self.chr_offsets = [b0, b0 + 1024, b0 + 2048, b0 + 3072, b1, b1 + 1024, b1 + 2048, b1 + 3072]
        else:
            b = ((self.chr_bank0 & ~1) * 4096) % chr_len
            self.chr_offsets = [(b + i * 1024) % chr_len for i in range(8)]

    def _update_prg_bases(self):
        mode = (self.control >> 2) & 3
        prg_len = self.prg_len
        if mode in (0, 1):
            lo = ((self.prg_bank & ~1) * 16384) % prg_len
            hi = (lo + 16384) % prg_len
        elif mode == 2:
            lo = 0
            hi = (self.prg_bank * 16384) % prg_len
        else:
            lo = (self.prg_bank * 16384) % prg_len
            hi = prg_len - 16384
        self.prg_lo_base = lo - 0x8000
        self.prg_hi_base = hi - 0xC000
        mv = self.prg_mv
        self.prg_pages[0] = mv[lo : lo + 8192]
        self.prg_pages[1] = mv[lo + 8192 : lo + 16384]
        self.prg_pages[2] = mv[hi : hi + 8192]
        self.prg_pages[3] = mv[hi + 8192 : hi + 16384]
        self.prg_offsets = [lo, lo + 8192, hi, hi + 8192]
        self.prg_offsets = [lo, lo + 8192, hi, hi + 8192]

    def _write_register(self, addr, data):
        bus = getattr(self.cart, 'bus', None)
        if bus is not None and bus.rmw_second_write:
            return
        if data & 0x80:
            self.shift = 0x10
            self.control |= 0x0C
            self._update_prg_bases()
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
                self._update_prg_bases()
                self._update_chr_bases()
            elif reg == 1:
                self.chr_bank0 = val & 0x1F
                self._update_chr_bases()
            elif reg == 2:
                self.chr_bank1 = val & 0x1F
                self._update_chr_bases()
            elif reg == 3:
                self.prg_bank = val & 0x0F
                self._update_prg_bases()

    def cpu_read(self, addr):
        if addr >= 0xC000:
            return self.prg_rom[self.prg_hi_base + addr]
        elif addr >= 0x8000:
            return self.prg_rom[self.prg_lo_base + addr]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data
        elif addr >= 0x8000:
            self._write_register(addr, data)

    def ppu_read(self, addr):
        if addr < 0x2000:
            chr_len = len(self.cart.chr_rom)
            if self.control & 0x10:
                if addr < 0x1000:
                    bank = self.chr_bank0 * 4096
                    return self.cart.chr_rom[(bank + addr) % chr_len]
                else:
                    bank = self.chr_bank1 * 4096
                    return self.cart.chr_rom[(bank + (addr - 0x1000)) % chr_len]
            else:
                bank = (self.chr_bank0 & ~1) * 4096
                return self.cart.chr_rom[(bank + addr) % chr_len]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000 and self.cart.header.chr_rom_size == 0:
            chr_len = len(self.cart.chr_rom)
            if self.control & 0x10:
                if addr < 0x1000:
                    bank = self.chr_bank0 * 4096
                    self.cart.chr_rom[(bank + addr) % chr_len] = data
                else:
                    bank = self.chr_bank1 * 4096
                    self.cart.chr_rom[(bank + (addr - 0x1000)) % chr_len] = data
            else:
                bank = (self.chr_bank0 & ~1) * 4096
                self.cart.chr_rom[(bank + addr) % chr_len] = data

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
        self.chr_offsets = [i * 1024 for i in range(8)]
        mv = memoryview(self.prg_rom)
        hi = self.prg_len - 16384
        self.prg_mv = mv
        self.prg_pages = [mv[0:8192], mv[8192:16384], mv[hi:hi+8192], mv[hi+8192:hi+16384]]
        self.prg_offsets = [0, 8192, hi, hi + 8192]

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
            bank = ((data & 0x0F) * 16384) % self.prg_len
            self.prg_bank_lo_base = bank - 0x8000
            self.prg_pages[0] = self.prg_mv[bank : bank + 8192]
            self.prg_pages[1] = self.prg_mv[bank + 8192 : bank + 16384]
            self.prg_offsets[0] = bank
            self.prg_offsets[1] = bank + 8192

    def ppu_read(self, addr):
        if addr < 0x2000:
            return self.cart.chr_rom[addr % len(self.cart.chr_rom)]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000 and self.cart.header.chr_rom_size == 0:
            self.cart.chr_rom[addr % len(self.cart.chr_rom)] = data

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
        self.chr_offsets = [i * 1024 for i in range(8)]

    def cpu_read(self, addr):
        if addr >= 0x8000:
            return self.cart.prg_rom[(addr - 0x8000) % len(self.cart.prg_rom)]
        return 0

    def cpu_write(self, addr, data):
        if addr >= 0x8000:
            self.chr_bank_select = data & 0x03
            b = (self.chr_bank_select * 8192) % len(self.cart.chr_rom)
            self.chr_offsets = [b + i * 1024 for i in range(8)]

    def ppu_read(self, addr):
        if addr < 0x2000:
            bank = self.chr_bank_select * 8192
            return self.cart.chr_rom[(bank + addr) % len(self.cart.chr_rom)]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000 and self.cart.header.chr_rom_size == 0:
            self.cart.chr_rom[addr % len(self.cart.chr_rom)] = data  # CHR-ROM read only

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
        self.chr_offsets = [i * 1024 for i in range(8)]
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
        if not hasattr(self, 'prg_pages'):
            self.prg_mv = memoryview(self.prg_rom)
            self.prg_pages = [None, None, None, None]
        lo = (self.prg_bank_lo * 0x4000) % self.prg_len
        hi = (self.prg_bank_hi * 0x4000) % self.prg_len
        mv = self.prg_mv
        self.prg_pages[0] = mv[lo : lo + 8192]
        self.prg_pages[1] = mv[lo + 8192 : lo + 16384]
        self.prg_pages[2] = mv[hi : hi + 8192]
        self.prg_pages[3] = mv[hi + 8192 : hi + 16384]
        self.prg_offsets = [lo, lo + 8192, hi, hi + 8192]
        self.prg_offsets = [lo, lo + 8192, hi, hi + 8192]

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


# =============================================================================
# Mapper 9 — MMC2
# Used by: Mike Tyson's Punch-Out!!
# =============================================================================
@register_mapper(9)
class Mapper9:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.chr_rom = cart.chr_rom
        self.chr_len = len(cart.chr_rom)
        self.num_prg_8k = max(1, self.prg_len // 8192)
        self.num_chr_4k = max(1, self.chr_len // 4096)

        self.prg_bank = 0
        self.chr_fd0 = 0
        self.chr_fe0 = 0
        self.chr_fd1 = 0
        self.chr_fe1 = 0
        self.latch0 = 0xFE
        self.latch1 = 0xFE
        self._mirroring = cart.header.mirroring

        self.prg_lo_base = 0 - 0x8000
        self.prg_hi_base = ((self.num_prg_8k - 3) * 8192) - 0xA000
        self.chr_offsets = [i * 1024 for i in range(8)]
        self._update_chr()

    def _update_chr(self):
        b0 = ((self.chr_fd0 if self.latch0 == 0xFD else self.chr_fe0) % self.num_chr_4k) * 4096
        b1 = ((self.chr_fd1 if self.latch1 == 0xFD else self.chr_fe1) % self.num_chr_4k) * 4096
        self.chr_offsets = [b0, b0 + 1024, b0 + 2048, b0 + 3072, b1, b1 + 1024, b1 + 2048, b1 + 3072]

    def cpu_read(self, addr):
        if addr >= 0xA000:
            return self.prg_rom[(self.prg_hi_base + addr) % self.prg_len]
        elif addr >= 0x8000:
            return self.prg_rom[(self.prg_lo_base + addr) % self.prg_len]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data
        elif 0xA000 <= addr <= 0xAFFF:
            self.prg_bank = data & 0x0F
            self.prg_lo_base = ((self.prg_bank % self.num_prg_8k) * 8192) - 0x8000
        elif 0xB000 <= addr <= 0xBFFF:
            self.chr_fd0 = data & 0x1F
            self._update_chr()
        elif 0xC000 <= addr <= 0xCFFF:
            self.chr_fe0 = data & 0x1F
            self._update_chr()
        elif 0xD000 <= addr <= 0xDFFF:
            self.chr_fd1 = data & 0x1F
            self._update_chr()
        elif 0xE000 <= addr <= 0xEFFF:
            self.chr_fe1 = data & 0x1F
            self._update_chr()
        elif 0xF000 <= addr <= 0xFFFF:
            self._mirroring = 'horizontal' if (data & 1) else 'vertical'

    def ppu_read(self, addr):
        if addr < 0x2000:
            val = self.chr_rom[(self.chr_offsets[addr >> 10] + (addr & 0x3FF)) % self.chr_len]
            if addr == 0x0FD8:
                self.latch0 = 0xFD; self._update_chr()
            elif addr == 0x0FE8:
                self.latch0 = 0xFE; self._update_chr()
            elif 0x1FD8 <= addr <= 0x1FDF:
                self.latch1 = 0xFD; self._update_chr()
            elif 0x1FE8 <= addr <= 0x1FEF:
                self.latch1 = 0xFE; self._update_chr()
            return val
        return 0

    def ppu_write(self, addr, data):
        pass

    def get_mirroring(self):
        return self._mirroring


# =============================================================================
# Mapper 23 — Konami VRC2b / VRC4e
# Used by: Contra (Japan), Wai Wai World, etc.
# =============================================================================
@register_mapper(23)
class Mapper23:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.chr_rom = cart.chr_rom
        self.chr_len = len(cart.chr_rom)
        self.num_prg_8k = max(1, self.prg_len // 8192)
        self.num_chr_1k = max(1, self.chr_len // 1024)

        self.prg_reg0 = 0
        self.prg_reg1 = 1
        self.prg_swap = False
        self.chr_regs = [i for i in range(8)]
        self._mirroring = cart.header.mirroring

        self.prg_offsets = [0, 8192, (self.num_prg_8k - 2) * 8192, (self.num_prg_8k - 1) * 8192]
        self.prg_mv = memoryview(self.prg_rom)
        self.prg_pages = [None, None, None, None]
        self.prg_offsets = [0, 8192, 0, 8192]
        self.chr_offsets = [i * 1024 for i in range(8)]
        self._update_prg()
        self._update_chr()

    def _update_prg(self):
        r0 = (self.prg_reg0 % self.num_prg_8k) * 8192
        r1 = (self.prg_reg1 % self.num_prg_8k) * 8192
        s2 = (self.num_prg_8k - 2) * 8192
        s1 = (self.num_prg_8k - 1) * 8192
        if self.prg_swap:
            self.prg_offsets = [s2, r1, r0, s1]
        else:
            self.prg_offsets = [r0, r1, s2, s1]
        mv = self.prg_mv
        for i in range(4):
            off = self.prg_offsets[i]
            self.prg_pages[i] = mv[off : off + 8192]

    def _update_chr(self):
        self.chr_offsets = [(self.chr_regs[i] % self.num_chr_1k) * 1024 for i in range(8)]

    def cpu_read(self, addr):
        if addr >= 0x8000:
            slot = (addr - 0x8000) >> 13
            return self.prg_rom[(self.prg_offsets[slot] + (addr & 0x1FFF)) % self.prg_len]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data
            return
        if addr < 0x8000:
            return
        a0 = (addr & 1) | ((addr >> 2) & 1)
        a1 = ((addr >> 1) & 1) | ((addr >> 3) & 1)
        sub = (a1 << 1) | a0
        region = addr & 0xF000

        if region == 0x8000:
            self.prg_reg0 = data & 0x1F
            self._update_prg()
        elif region == 0x9000:
            if sub in (0, 1):
                m = data & 3
                self._mirroring = ['vertical', 'horizontal', 'single0', 'single1'][m]
            elif sub in (2, 3):
                self.prg_swap = bool(data & 2)
                self._update_prg()
        elif region == 0xA000:
            self.prg_reg1 = data & 0x1F
            self._update_prg()
        elif 0xB000 <= region <= 0xE000:
            idx = ((region - 0xB000) >> 12) * 2 + (sub >> 1)
            if sub & 1:
                self.chr_regs[idx] = (self.chr_regs[idx] & 0x0F) | ((data & 0x1F) << 4)
            else:
                self.chr_regs[idx] = (self.chr_regs[idx] & 0x1F0) | (data & 0x0F)
            self._update_chr()

    def ppu_read(self, addr):
        if addr < 0x2000:
            return self.chr_rom[(self.chr_offsets[addr >> 10] + (addr & 0x3FF)) % self.chr_len]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000 and self.cart.header.chr_rom_size == 0:
            self.chr_rom[(self.chr_offsets[addr >> 10] + (addr & 0x3FF)) % self.chr_len] = data

    def get_mirroring(self):
        return self._mirroring


# =============================================================================
# Mapper 4 — MMC3
# Used by: Super Mario Bros. 3, Mega Man 3-6, Kirby's Adventure, Ninja Gaiden
# =============================================================================
@register_mapper(4)
class Mapper4:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.chr_rom = cart.chr_rom
        self.chr_len = len(cart.chr_rom)
        self.num_prg_8k = max(1, self.prg_len // 8192)
        self.num_chr_1k = max(1, self.chr_len // 1024)

        self.regs = [0] * 8
        self.bank_select = 0
        self.prg_mode = 0
        self.chr_mode = 0
        self._mirroring = cart.header.mirroring

        self.irq_latch = 0
        self.irq_counter = 0
        self.irq_reload = False
        self.irq_enabled = False
        self.irq_pending = False

        self.prg_offsets = [0, 8192, (self.num_prg_8k - 2) * 8192, (self.num_prg_8k - 1) * 8192]
        self.prg_mv = memoryview(self.prg_rom)
        self.prg_pages = [None, None, None, None]
        self.prg_offsets = [0, 8192, 0, 8192]
        self.chr_offsets = [i * 1024 for i in range(8)]
        self._update_banks()

    def _update_banks(self):
        last = (self.num_prg_8k - 1) * 8192
        second_last = (self.num_prg_8k - 2) * 8192
        r6 = (self.regs[6] % self.num_prg_8k) * 8192
        r7 = (self.regs[7] % self.num_prg_8k) * 8192
        if self.prg_mode == 0:
            self.prg_offsets = [r6, r7, second_last, last]
        else:
            self.prg_offsets = [second_last, r7, r6, last]
        mv = self.prg_mv
        for i in range(4):
            off = self.prg_offsets[i]
            self.prg_pages[i] = mv[off : off + 8192]

        c = [
            ((self.regs[0] & ~1) % self.num_chr_1k) * 1024,
            ((self.regs[0] | 1) % self.num_chr_1k) * 1024,
            ((self.regs[1] & ~1) % self.num_chr_1k) * 1024,
            ((self.regs[1] | 1) % self.num_chr_1k) * 1024,
            (self.regs[2] % self.num_chr_1k) * 1024,
            (self.regs[3] % self.num_chr_1k) * 1024,
            (self.regs[4] % self.num_chr_1k) * 1024,
            (self.regs[5] % self.num_chr_1k) * 1024,
        ]
        if self.chr_mode == 0:
            self.chr_offsets = c
        else:
            self.chr_offsets = c[4:] + c[:4]

    def cpu_read(self, addr):
        if 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        if addr >= 0x8000:
            slot = (addr - 0x8000) >> 13
            return self.prg_rom[(self.prg_offsets[slot] + (addr & 0x1FFF)) % self.prg_len]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data
        elif addr >= 0x8000:
            even = (addr & 1) == 0
            if addr < 0xA000:
                if even:
                    self.bank_select = data & 0x07
                    self.prg_mode = (data >> 6) & 1
                    self.chr_mode = (data >> 7) & 1
                    self._update_banks()
                else:
                    self.regs[self.bank_select] = data
                    self._update_banks()
            elif addr < 0xC000:
                if even and not self.cart.header.four_screen:
                    self._mirroring = 'horizontal' if (data & 1) else 'vertical'
            elif addr < 0xE000:
                if even:
                    self.irq_latch = data
                else:
                    self.irq_reload = True
            else:
                if even:
                    self.irq_enabled = False
                    self.irq_pending = False
                else:
                    self.irq_enabled = True

    def ppu_read(self, addr):
        if addr < 0x2000:
            slot = addr >> 10
            return self.chr_rom[(self.chr_offsets[slot] + (addr & 0x03FF)) % self.chr_len]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000 and self.cart.header.chr_rom_size == 0:
            slot = addr >> 10
            self.chr_rom[(self.chr_offsets[slot] + (addr & 0x03FF)) % self.chr_len] = data

    def clock_scanline(self):
        if self.irq_counter == 0 or self.irq_reload:
            self.irq_counter = self.irq_latch
            self.irq_reload = False
        else:
            self.irq_counter -= 1
        if self.irq_counter == 0 and self.irq_enabled:
            self.irq_pending = True
            return True
        return False

    def get_mirroring(self):
        return self._mirroring


# =============================================================================
# Mapper 9 — MMC2
# Used by: Mike Tyson's Punch-Out!!
# =============================================================================
@register_mapper(9)
class Mapper9:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.chr_rom = cart.chr_rom
        self.chr_len = len(cart.chr_rom)
        self.num_prg_8k = max(1, self.prg_len // 8192)
        self.num_chr_4k = max(1, self.chr_len // 4096)

        self.prg_bank = 0
        self.chr_fd0 = 0
        self.chr_fe0 = 0
        self.chr_fd1 = 0
        self.chr_fe1 = 0
        self.latch0 = 0xFE
        self.latch1 = 0xFE
        self._mirroring = cart.header.mirroring

        self.prg_lo_base = 0 - 0x8000
        self.prg_hi_base = ((self.num_prg_8k - 3) * 8192) - 0xA000
        self.chr_offsets = [i * 1024 for i in range(8)]
        self._update_chr()

    def _update_chr(self):
        b0 = ((self.chr_fd0 if self.latch0 == 0xFD else self.chr_fe0) % self.num_chr_4k) * 4096
        b1 = ((self.chr_fd1 if self.latch1 == 0xFD else self.chr_fe1) % self.num_chr_4k) * 4096
        self.chr_offsets = [b0, b0 + 1024, b0 + 2048, b0 + 3072, b1, b1 + 1024, b1 + 2048, b1 + 3072]

    def cpu_read(self, addr):
        if addr >= 0xA000:
            return self.prg_rom[(self.prg_hi_base + addr) % self.prg_len]
        elif addr >= 0x8000:
            return self.prg_rom[(self.prg_lo_base + addr) % self.prg_len]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data
        elif 0xA000 <= addr <= 0xAFFF:
            self.prg_bank = data & 0x0F
            self.prg_lo_base = ((self.prg_bank % self.num_prg_8k) * 8192) - 0x8000
        elif 0xB000 <= addr <= 0xBFFF:
            self.chr_fd0 = data & 0x1F
            self._update_chr()
        elif 0xC000 <= addr <= 0xCFFF:
            self.chr_fe0 = data & 0x1F
            self._update_chr()
        elif 0xD000 <= addr <= 0xDFFF:
            self.chr_fd1 = data & 0x1F
            self._update_chr()
        elif 0xE000 <= addr <= 0xEFFF:
            self.chr_fe1 = data & 0x1F
            self._update_chr()
        elif 0xF000 <= addr <= 0xFFFF:
            self._mirroring = 'horizontal' if (data & 1) else 'vertical'

    def ppu_read(self, addr):
        if addr < 0x2000:
            val = self.chr_rom[(self.chr_offsets[addr >> 10] + (addr & 0x3FF)) % self.chr_len]
            if addr == 0x0FD8:
                self.latch0 = 0xFD; self._update_chr()
            elif addr == 0x0FE8:
                self.latch0 = 0xFE; self._update_chr()
            elif 0x1FD8 <= addr <= 0x1FDF:
                self.latch1 = 0xFD; self._update_chr()
            elif 0x1FE8 <= addr <= 0x1FEF:
                self.latch1 = 0xFE; self._update_chr()
            return val
        return 0

    def ppu_write(self, addr, data):
        pass

    def get_mirroring(self):
        return self._mirroring


# =============================================================================
# Mapper 23 — Konami VRC2b / VRC4e
# Used by: Contra (Japan), Wai Wai World, etc.
# =============================================================================
@register_mapper(23)
class Mapper23:
    def __init__(self, cart):
        self.cart = cart
        self.prg_rom = cart.prg_rom
        self.prg_len = len(cart.prg_rom)
        self.chr_rom = cart.chr_rom
        self.chr_len = len(cart.chr_rom)
        self.num_prg_8k = max(1, self.prg_len // 8192)
        self.num_chr_1k = max(1, self.chr_len // 1024)

        self.prg_reg0 = 0
        self.prg_reg1 = 1
        self.prg_swap = False
        self.chr_regs = [i for i in range(8)]
        self._mirroring = cart.header.mirroring

        self.prg_offsets = [0, 8192, (self.num_prg_8k - 2) * 8192, (self.num_prg_8k - 1) * 8192]
        self.prg_mv = memoryview(self.prg_rom)
        self.prg_pages = [None, None, None, None]
        self.prg_offsets = [0, 8192, 0, 8192]
        self.chr_offsets = [i * 1024 for i in range(8)]
        self._update_prg()
        self._update_chr()

    def _update_prg(self):
        r0 = (self.prg_reg0 % self.num_prg_8k) * 8192
        r1 = (self.prg_reg1 % self.num_prg_8k) * 8192
        s2 = (self.num_prg_8k - 2) * 8192
        s1 = (self.num_prg_8k - 1) * 8192
        if self.prg_swap:
            self.prg_offsets = [s2, r1, r0, s1]
        else:
            self.prg_offsets = [r0, r1, s2, s1]
        mv = self.prg_mv
        for i in range(4):
            off = self.prg_offsets[i]
            self.prg_pages[i] = mv[off : off + 8192]

    def _update_chr(self):
        self.chr_offsets = [(self.chr_regs[i] % self.num_chr_1k) * 1024 for i in range(8)]

    def cpu_read(self, addr):
        if addr >= 0x8000:
            slot = (addr - 0x8000) >> 13
            return self.prg_rom[(self.prg_offsets[slot] + (addr & 0x1FFF)) % self.prg_len]
        elif 0x6000 <= addr <= 0x7FFF:
            return self.cart.prg_ram[addr - 0x6000]
        return 0

    def cpu_write(self, addr, data):
        if 0x6000 <= addr <= 0x7FFF:
            self.cart.prg_ram[addr - 0x6000] = data
            return
        if addr < 0x8000:
            return
        a0 = (addr & 1) | ((addr >> 2) & 1)
        a1 = ((addr >> 1) & 1) | ((addr >> 3) & 1)
        sub = (a1 << 1) | a0
        region = addr & 0xF000

        if region == 0x8000:
            self.prg_reg0 = data & 0x1F
            self._update_prg()
        elif region == 0x9000:
            if sub in (0, 1):
                m = data & 3
                self._mirroring = ['vertical', 'horizontal', 'single0', 'single1'][m]
            elif sub in (2, 3):
                self.prg_swap = bool(data & 2)
                self._update_prg()
        elif region == 0xA000:
            self.prg_reg1 = data & 0x1F
            self._update_prg()
        elif 0xB000 <= region <= 0xE000:
            idx = ((region - 0xB000) >> 12) * 2 + (sub >> 1)
            if sub & 1:
                self.chr_regs[idx] = (self.chr_regs[idx] & 0x0F) | ((data & 0x1F) << 4)
            else:
                self.chr_regs[idx] = (self.chr_regs[idx] & 0x1F0) | (data & 0x0F)
            self._update_chr()

    def ppu_read(self, addr):
        if addr < 0x2000:
            return self.chr_rom[(self.chr_offsets[addr >> 10] + (addr & 0x3FF)) % self.chr_len]
        return 0

    def ppu_write(self, addr, data):
        if addr < 0x2000 and self.cart.header.chr_rom_size == 0:
            self.chr_rom[(self.chr_offsets[addr >> 10] + (addr & 0x3FF)) % self.chr_len] = data

    def get_mirroring(self):
        return self._mirroring

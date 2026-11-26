"""
NES System Bus
Connects: CPU, PPU, APU, Cartridge, Controllers, RAM.

Memory Map:
  $0000-$07FF  — 2 KB internal RAM (mirrored to $1FFF)
  $2000-$2007  — PPU registers (mirrored to $3FFF)
  $4000-$4013  — APU + I/O registers
  $4014        — OAM DMA
  $4015        — APU status
  $4016        — Controller 1
  $4017        — Controller 2 / APU frame counter
  $4020-$FFFF  — Cartridge space (PRG ROM/RAM via mapper)
"""

from .cpu import CPU
from .ppu import PPU
from .apu import APU


class Bus:
    def __init__(self, cartridge):
        self.cart = cartridge
        self.ram  = bytearray(2048)  # 2 KB internal RAM

        # Devices
        self.is_pal = (getattr(cartridge, 'region', 'NTSC') == 'PAL')
        self.ppu = PPU(self)
        self.apu = APU()
        self.apu.set_region(self.is_pal)
        self.cpu = CPU(self)

        # Controller state: bit mask of pressed buttons
        # Bits: A B Select Start Up Down Left Right
        self.controller1 = 0x00
        self.controller2 = 0x00
        self._ctrl1_shift = 0x00
        self._ctrl2_shift = 0x00

        # Timing
        self.system_clock = 0
        self.rmw_second_write = False
        cartridge.bus = self
        self.dma_active   = False
        self.dma_page     = 0x00
        self.dma_addr     = 0x00
        self.dma_data     = 0x00
        self.dma_dummy    = True

    # -------------------------------------------------------------------------
    # CPU Bus Read/Write
    # -------------------------------------------------------------------------
    def cpu_read(self, addr):
        addr &= 0xFFFF
        if addr >= 0x8000:
            return self.cart.cpu_read(addr)
        elif addr <= 0x1FFF:
            return self.ram[addr & 0x07FF]
        elif addr <= 0x3FFF:
            return self.ppu.reg_read(addr & 0x0007)
        elif addr == 0x4016:
            data = (self._ctrl1_shift >> 7) & 1
            self._ctrl1_shift = (self._ctrl1_shift << 1) & 0xFF
            return data
        elif addr == 0x4015:
            return self.apu.read_status()
        elif addr == 0x4017:
            data = (self._ctrl2_shift >> 7) & 1
            self._ctrl2_shift = (self._ctrl2_shift << 1) & 0xFF
            return data
        elif addr >= 0x4020:
            return self.cart.cpu_read(addr)
        return 0

    def cpu_write(self, addr, data):
        addr &= 0xFFFF
        data &= 0xFF
        if addr <= 0x1FFF:
            self.ram[addr & 0x07FF] = data
        elif addr <= 0x3FFF:
            self.ppu.reg_write(addr & 0x0007, data)
        elif 0x4000 <= addr <= 0x4013 or addr == 0x4015 or addr == 0x4017:
            self.apu.write(addr, data)
        elif addr == 0x4014:
            # OAM DMA
            self.dma_page  = data
            self.dma_addr  = 0
            self.dma_active = True
            self.dma_dummy  = True
        elif addr == 0x4016:
            # Controller strobe: latch current state
            self._ctrl1_shift = self.controller1
            self._ctrl2_shift = self.controller2
        elif addr >= 0x4020:
            self.cart.cpu_write(addr, data)

    # -------------------------------------------------------------------------
    # Fast Frame Step (Scanline based - 10x-30x faster)
    # -------------------------------------------------------------------------
    def step_frame(self, render=True):
        """Emulate one full video frame (262 NTSC / 312 PAL scanlines) with VBlank spin-loop fast forwarding."""
        self.ppu.frame_complete = False
        cycles_per_scanline = (341.0 / 3.2) if self.is_pal else (341.0 / 3.0)
        vblank_scanlines = 70 if self.is_pal else 20
        cpu = self.cpu
        ppu = self.ppu

        # 1. Visible scanlines (0 to 239)
        carry = 0.0
        for scanline in range(240):
            target = cycles_per_scanline + carry
            if self.dma_active:
                self._handle_dma_fast()
                target -= 513
            if target > 0:
                if not (ppu.status & 0x80):
                    pc = cpu.pc
                    op = cpu.read(pc)
                    if op in (0xAD, 0x2C) and cpu.read(pc + 1) == 0x02 and cpu.read(pc + 2) == 0x20:
                        if cpu.read(pc + 3) == 0x10 and cpu.read(pc + 4) == 0xFB:
                            target = 0.0
                if target > 0:
                    carry = cpu.run_cycles(target)
                else:
                    carry = 0.0
            else:
                carry = target
            ppu.render_scanline(scanline, render=render)
            if (ppu.mask & 0x18) and self.cart.clock_scanline():
                cpu.irq()

        # 2. Post-render scanline (240)
        target = cycles_per_scanline + carry
        if self.dma_active:
            self._handle_dma_fast()
            target -= 513
        if target > 0:
            carry = cpu.run_cycles(target)
        else:
            carry = target

        # 3. VBlank (scanline 241)
        ppu.status |= 0x80
        if ppu.ctrl & 0x80:
            cpu.nmi()
        ppu.frame_complete = True

        vblank_cycles = cycles_per_scanline * vblank_scanlines + carry
        if self.dma_active:
            self._handle_dma_fast()
            vblank_cycles -= 513
        if vblank_cycles > 0:
            carry = cpu.run_cycles(vblank_cycles)
        else:
            carry = vblank_cycles

        # 4. Pre-render scanline (261)
        ppu.status &= ~0xE0
        if ppu.mask & 0x18:
            ppu.v = (ppu.v & ~0x7BE0) | (ppu.t & 0x7BE0)
            ppu.v = (ppu.v & ~0x041F) | (ppu.t & 0x041F)
        target = cycles_per_scanline + carry
        if self.dma_active:
            self._handle_dma_fast()
            target -= 513
        if target > 0:
            cpu.run_cycles(target)

    def _handle_dma_fast(self):
        page = self.dma_page << 8
        for i in range(256):
            self.ppu.oam[i] = self.cpu_read(page + i)
        self.dma_active = False

    # -------------------------------------------------------------------------
    # Main clock — drives the whole system (cycle-accurate fallback)
    # -------------------------------------------------------------------------
    def clock(self):
        # PPU runs 3x faster than CPU
        self.ppu.clock()
        self.apu.clock()

        if self.system_clock % 3 == 0:
            if self.dma_active:
                self._handle_dma()
            else:
                self.cpu.clock()

        # NMI from PPU
        if self.ppu.nmi_triggered:
            self.ppu.nmi_triggered = False
            self.cpu.nmi()

        # IRQ from APU (cartridge mappers can also trigger)
        if self.apu.irq_pending:
            self.apu.irq_pending = False
            self.cpu.irq()

        self.system_clock += 1

    def _handle_dma(self):
        if self.dma_dummy:
            if self.system_clock % 2 == 1:
                self.dma_dummy = False
            return

        if self.system_clock % 2 == 0:
            self.dma_data = self.cpu_read((self.dma_page << 8) | self.dma_addr)
        else:
            self.ppu.oam[self.dma_addr] = self.dma_data
            self.dma_addr = (self.dma_addr + 1) & 0xFF
            if self.dma_addr == 0:
                self.dma_active = False
                self.dma_dummy  = True

    # -------------------------------------------------------------------------
    # Reset
    # -------------------------------------------------------------------------
    def reset(self):
        self.ram[:] = b'\x00' * 2048
        self.ppu.palette[:] = b'\x0F' * 32
        self.ppu.pal_dirty = True
        self.system_clock = 0
        self.dma_active   = False
        self.cpu.reset()

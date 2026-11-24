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
        self.ppu = PPU(self)
        self.apu = APU()
        self.cpu = CPU(self)

        # Controller state: bit mask of pressed buttons
        # Bits: A B Select Start Up Down Left Right
        self.controller1 = 0x00
        self.controller2 = 0x00
        self._ctrl1_shift = 0x00
        self._ctrl2_shift = 0x00

        # Timing
        self.system_clock = 0
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
        if addr <= 0x1FFF:
            return self.ram[addr & 0x07FF]
        elif addr <= 0x3FFF:
            return self.ppu.reg_read(addr & 0x0007)
        elif addr == 0x4015:
            return self.apu.read_status()
        elif addr == 0x4016:
            data = (self._ctrl1_shift >> 7) & 1
            self._ctrl1_shift = (self._ctrl1_shift << 1) & 0xFF
            return data
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
    # Main clock — drives the whole system
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
        self.ram       = bytearray(2048)
        self.system_clock = 0
        self.dma_active   = False
        self.cpu.reset()

"""Automated unit and integration tests for RetroPlay NES Emulator."""

import os
import tempfile
import unittest
import numpy as np

from nes_core.cartridge import Cartridge
from nes_core.bus import Bus
from nes_core.apu import APU


def make_ines_rom(mapper_id=0, prg_banks=2, chr_banks=1, mirroring=0, prg_data=None):
    """Build a minimal valid iNES ROM in a temporary file and return its path."""
    header = bytearray(16)
    header[0:4] = b'NES\x1a'
    header[4] = prg_banks
    header[5] = chr_banks
    header[6] = ((mapper_id & 0x0F) << 4) | (mirroring & 0x01)
    header[7] = mapper_id & 0xF0

    prg = bytearray(prg_banks * 16384)
    if prg_data:
        prg[:len(prg_data)] = prg_data
    # Set reset vector ($FFFC-$FFFD) to $8000
    prg[-4] = 0x00
    prg[-3] = 0x80

    chr_rom = bytearray(chr_banks * 8192)
    fd, path = tempfile.mkstemp(suffix='.nes')
    with os.fdopen(fd, 'wb') as f:
        f.write(header + prg + chr_rom)
    return path


class TestCPUBugs(unittest.TestCase):
    def setUp(self):
        self.rom_path = make_ines_rom(mapper_id=0, prg_banks=1, chr_banks=1)
        self.cart = Cartridge(self.rom_path)
        self.bus = Bus(self.cart)
        self.bus.reset()

    def tearDown(self):
        os.remove(self.rom_path)

    def test_shift_zero_page_0000_vs_accumulator(self):
        cpu = self.bus.cpu
        # ASL $00 (0x06, 0x00) should shift RAM[0x0000], NOT Accumulator A
        self.bus.ram[0x0000] = 0x41
        cpu.a = 0x05
        # Write ASL $00 at $8000
        self.cart.prg_rom[0] = 0x06
        self.cart.prg_rom[1] = 0x00
        # ASL A (0x0A) at $8002
        self.cart.prg_rom[2] = 0x0A
        cpu.pc = 0x8000

        cpu.step()
        self.assertEqual(self.bus.ram[0x0000], 0x82)
        self.assertEqual(cpu.a, 0x05)

        cpu.step()
        self.assertEqual(cpu.a, 0x0A)
        self.assertEqual(self.bus.ram[0x0000], 0x82)

    def test_rel_branch_wrap(self):
        cpu = self.bus.cpu
        cpu.pc = 0xFFFF
        self.cart.prg_rom[0x3FFF] = 0x05  # +5 offset
        addr, _ = cpu._rel()
        self.assertEqual(addr, 0x0005)


class TestMappers(unittest.TestCase):
    def test_mapper2_uxrom_chr_ram_writable(self):
        # Contra uses Mapper 2 with 0 CHR banks (CHR-RAM)
        path = make_ines_rom(mapper_id=2, prg_banks=2, chr_banks=0)
        try:
            cart = Cartridge(path)
            cart.ppu_write(0x0123, 0xAB)
            self.assertEqual(cart.ppu_read(0x0123), 0xAB)
        finally:
            os.remove(path)

    def test_mapper4_mmc3_banking_and_irq(self):
        path = make_ines_rom(mapper_id=4, prg_banks=4, chr_banks=2)
        try:
            cart = Cartridge(path)
            # Mark each 8KB PRG bank with its index at offset 0
            for b in range(8):
                cart.prg_rom[b * 8192] = b + 10
            # Select R6 (PRG bank 0 at $8000) = bank 3
            cart.cpu_write(0x8000, 0x06)
            cart.cpu_write(0x8001, 0x03)
            self.assertEqual(cart.cpu_read(0x8000), 13)
            # Last bank ($E000) is fixed to bank 7
            self.assertEqual(cart.cpu_read(0xE000), 17)

            # Test scanline IRQ
            cart.cpu_write(0xC000, 2)     # latch = 2
            cart.cpu_write(0xC001, 0)     # reload
            cart.cpu_write(0xE001, 0)     # enable IRQ
            self.assertFalse(cart.clock_scanline())  # counter -> 2
            self.assertFalse(cart.clock_scanline())  # counter -> 1
            self.assertTrue(cart.clock_scanline())   # counter -> 0 -> IRQ!
        finally:
            os.remove(path)


class TestAPU(unittest.TestCase):
    def test_pulse_sweep_updates_period_and_audio_shape(self):
        apu = APU()
        apu.write(0x4015, 0x0F)  # Enable all channels
        apu.write(0x4000, 0x9F)  # Pulse 1: duty 50%, constant vol 15
        apu.write(0x4001, 0x81)  # Sweep enabled, period 0, shift 1 (increase period)
        apu.write(0x4002, 0x80)  # timer lo = 0x80
        apu.write(0x4003, 0x08)  # length load, timer hi = 0

        initial_period = apu.pulse1.timer_period
        audio = apu.generate_frame_audio(count=None)
        self.assertEqual(audio.ndim, 2)
        self.assertEqual(audio.shape[1], 2)
        self.assertGreater(np.max(np.abs(audio)), 0)
        # Sweep should have updated timer_period during the 2 half-frames
        self.assertNotEqual(apu.pulse1.timer_period, initial_period)


class TestPPUAndIntegration(unittest.TestCase):
    def test_loopy_scroll_scanline_render(self):
        path = make_ines_rom(mapper_id=0, prg_banks=1, chr_banks=1)
        try:
            cart = Cartridge(path)
            bus = Bus(cart)
            bus.reset()
            ppu = bus.ppu
            ppu.mask = 0x18  # Enable BG + Sprites
            ppu.palette[0] = 0x01  # Blue backdrop
            ppu.palette[1] = 0x30  # White
            # Write non-zero tile 1 in CHR
            cart.chr_rom[16:32] = b'\xFF' * 16
            ppu.vram[0] = 1
            ppu.reg_write(5, 0)
            ppu.reg_write(5, 0)
            ppu.v = ppu.t
            ppu.render_scanline(0, render=True)
            ppu.render_scanline(239, render=True)
            self.assertGreater(int(ppu.frame[0].sum()), 0)
        finally:
            os.remove(path)


class TestGamepadManager(unittest.TestCase):
    def test_sdl_and_generic_gamepad_mapping(self):
        import pygame
        from nes_core.controllers import GamepadManager

        class FakeCtrl:
            def __init__(self):
                self.btns = {pygame.CONTROLLER_BUTTON_DPAD_RIGHT, pygame.CONTROLLER_BUTTON_A, pygame.CONTROLLER_BUTTON_START}
            def get_button(self, b): return b in self.btns
            def get_axis(self, a): return 0

        class FakeJoy:
            def get_numhats(self): return 1
            def get_hat(self, _): return (-1, 1)  # Left + Up
            def get_numaxes(self): return 2
            def get_axis(self, _): return 0.0
            def get_numbuttons(self): return 10
            def get_button(self, b): return b in (1, 8)  # B + Select (retro USB layout)

        pygame.init()
        gm = GamepadManager()
        gm.devices = [
            {"id": 1, "name": "Xbox Pad", "ctrl": FakeCtrl(), "joy": None},
            {"id": 2, "name": "Retro USB", "ctrl": None, "joy": FakeJoy()},
        ]
        p1, pause1 = gm.read_player(0)
        p2, pause2 = gm.read_player(1)
        self.assertEqual(p1, (1 << 0) | (1 << 4) | (1 << 7))  # Right + Start + A
        self.assertFalse(pause1)
        self.assertEqual(p2, (1 << 1) | (1 << 3) | (1 << 5) | (1 << 6))  # Left + Up + Select + B
        self.assertFalse(pause2)
        pygame.quit()


if __name__ == '__main__':
    unittest.main()

"""
NES Emulator — Main Entry Point
Requires: pygame, numpy

Usage:
  python main.py path/to/game.nes
  python main.py path/to/game.nes --scale 3
  python main.py path/to/game.nes --debug

Controls (Player 1):
  Arrow Keys   → D-Pad
  Z            → A button
  X            → B button
  Enter        → Start
  Right Shift  → Select

Controls (Player 2):
  WASD         → D-Pad
  Comma (,)    → A button
  Period (.)   → B button
  1            → Start
  2            → Select

Emulator Keys:
  ESC          → Quit
  F5           → Reset
  F7           → Save state
  F8           → Load state
  F11          → Toggle fullscreen
  P            → Pause / Resume
"""

import sys
import os
import time
import argparse
import pickle
import numpy as np

try:
    import pygame
    import pygame.mixer
except ImportError:
    print("ERROR: pygame not found. Install it with:  pip install pygame numpy")
    sys.exit(1)

from nes_core.cartridge import Cartridge
from nes_core.bus       import Bus

# NES timing constants
NES_FPS         = 60.0988
NES_CPU_CLOCK   = 1789773   # Hz
NES_PPU_CLOCK   = NES_CPU_CLOCK * 3
CYCLES_PER_FRAME = NES_CPU_CLOCK / NES_FPS

SCREEN_W = 256
SCREEN_H = 240
WINDOW_TITLE = "RetroPlay NES Emulator"


# ─── Controller bit mapping ───────────────────────────────────────────────────
# Bit 7=A, 6=B, 5=Select, 4=Start, 3=Up, 2=Down, 1=Left, 0=Right

P1_KEYMAP = {
    pygame.K_z:      7,   # A
    pygame.K_x:      6,   # B
    pygame.K_RSHIFT: 5,   # Select
    pygame.K_RETURN: 4,   # Start
    pygame.K_UP:     3,   # Up
    pygame.K_DOWN:   2,   # Down
    pygame.K_LEFT:   1,   # Left
    pygame.K_RIGHT:  0,   # Right
}

P2_KEYMAP = {
    pygame.K_COMMA:  7,   # A
    pygame.K_PERIOD: 6,   # B
    pygame.K_2:      5,   # Select
    pygame.K_1:      4,   # Start
    pygame.K_w:      3,   # Up
    pygame.K_s:      2,   # Down
    pygame.K_a:      1,   # Left
    pygame.K_d:      0,   # Right
}


class NESEmulator:
    def __init__(self, rom_path: str, scale: int = 3, debug: bool = False):
        self.scale = scale
        self.debug = debug
        self.paused = False
        self.running = True
        self.fullscreen = False
        self.save_slot = 0

        # Load ROM and build system
        print(f"Loading ROM: {rom_path}")
        cart = Cartridge(rom_path)
        self.bus = Bus(cart)
        self.bus.reset()

        # Init pygame mixer before pygame.init()
        try:
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.init()
        except Exception:
            pass
        pygame.init()

        self.win_w = SCREEN_W * scale
        self.win_h = SCREEN_H * scale + (40 if debug else 0)
        self.screen = pygame.display.set_mode((self.win_w, self.win_h))
        pygame.display.set_caption(f"{WINDOW_TITLE} — {os.path.basename(rom_path)}")

        # NES frame surface (256x240)
        self.nes_surface = pygame.Surface((SCREEN_W, SCREEN_H))

        # Font for debug overlay
        self.font = pygame.font.SysFont("Consolas", 11) if debug else None

        # Audio
        self.audio_buffer = np.array([], dtype=np.float32)
        mixer_info = pygame.mixer.get_init()
        self.audio_channels = mixer_info[2] if mixer_info else 0
        self.audio_channel = None

        # Save state directory
        self.save_dir = os.path.join(os.path.dirname(rom_path), "saves")
        os.makedirs(self.save_dir, exist_ok=True)
        self.save_path = os.path.join(
            self.save_dir,
            os.path.splitext(os.path.basename(rom_path))[0] + ".state"
        )

        # Clock
        self.clock = pygame.time.Clock()
        self.frame_time = 1.0 / NES_FPS
        self.cycle_budget = 0.0

        print("[Emulator] Ready! Press F5 to reset, ESC to quit.")
        print("[Controls] Z=A  X=B  Enter=Start  RShift=Select  Arrows=D-Pad")

    # ─────────────────────────────────────────────────────────────────────────
    def run(self):
        while self.running:
            t_start = time.perf_counter()

            self._handle_events()

            if not self.paused:
                self._emulate_frame()
                self._render_frame()
                self._play_audio()

            self.clock.tick(60)

            if self.debug:
                self._draw_debug()

            pygame.display.flip()

    # ─────────────────────────────────────────────────────────────────────────
    def _emulate_frame(self):
        """Run enough bus clocks to produce one complete video frame."""
        self.bus.ppu.frame_complete = False
        while not self.bus.ppu.frame_complete:
            self.bus.clock()

    # ─────────────────────────────────────────────────────────────────────────
    def _render_frame(self):
        """Blit the PPU frame buffer onto the pygame window."""
        frame = self.bus.ppu.frame   # numpy (240, 256, 3) uint8
        pygame.surfarray.blit_array(
            self.nes_surface,
            frame.swapaxes(0, 1)     # pygame wants (W, H, 3)
        )
        scaled = pygame.transform.scale(
            self.nes_surface,
            (SCREEN_W * self.scale, SCREEN_H * self.scale)
        )
        self.screen.blit(scaled, (0, 0))

    # ─────────────────────────────────────────────────────────────────────────
    def _play_audio(self):
        """Convert APU samples to pygame audio and queue them."""
        if not pygame.mixer.get_init():
            return
        samples = self.bus.apu.get_samples()
        if samples:
            arr = np.array(samples, dtype=np.float32)
            arr = np.clip(arr, -1.0, 1.0)
            int_arr = (arr * 32767).astype(np.int16)
            if len(int_arr) > 0 and pygame.mixer.get_busy() == 0:
                mixer_info = pygame.mixer.get_init()
                channels = mixer_info[2] if mixer_info else 1
                if channels == 2:
                    audio_data = np.column_stack((int_arr, int_arr))
                else:
                    audio_data = int_arr
                try:
                    sound = pygame.sndarray.make_sound(audio_data)
                    sound.play()
                except Exception:
                    pass

    # ─────────────────────────────────────────────────────────────────────────
    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.KEYDOWN:
                self._on_key_down(event.key)

            elif event.type == pygame.KEYUP:
                self._on_key_up(event.key)

    def _on_key_down(self, key):
        if key == pygame.K_ESCAPE:
            self.running = False
        elif key == pygame.K_F5:
            print("[Emulator] Reset")
            self.bus.reset()
        elif key == pygame.K_p:
            self.paused = not self.paused
            print(f"[Emulator] {'Paused' if self.paused else 'Resumed'}")
        elif key == pygame.K_F7:
            self._save_state()
        elif key == pygame.K_F8:
            self._load_state()
        elif key == pygame.K_F11:
            self._toggle_fullscreen()
        # Controller buttons
        elif key in P1_KEYMAP:
            self.bus.controller1 |= (1 << P1_KEYMAP[key])
        elif key in P2_KEYMAP:
            self.bus.controller2 |= (1 << P2_KEYMAP[key])

    def _on_key_up(self, key):
        if key in P1_KEYMAP:
            self.bus.controller1 &= ~(1 << P1_KEYMAP[key])
        elif key in P2_KEYMAP:
            self.bus.controller2 &= ~(1 << P2_KEYMAP[key])

    # ─────────────────────────────────────────────────────────────────────────
    def _toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            info = pygame.display.Info()
            self.screen = pygame.display.set_mode(
                (info.current_w, info.current_h), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode((self.win_w, self.win_h))

    # ─────────────────────────────────────────────────────────────────────────
    def _save_state(self):
        try:
            state = {
                'cpu_a':   self.bus.cpu.a,
                'cpu_x':   self.bus.cpu.x,
                'cpu_y':   self.bus.cpu.y,
                'cpu_sp':  self.bus.cpu.sp,
                'cpu_pc':  self.bus.cpu.pc,
                'cpu_st':  self.bus.cpu.status,
                'ram':     bytes(self.bus.ram),
                'vram':    bytes(self.bus.ppu.vram),
                'oam':     bytes(self.bus.ppu.oam),
                'palette': bytes(self.bus.ppu.palette),
                'prg_ram': bytes(self.bus.cart.prg_ram),
            }
            with open(self.save_path, 'wb') as f:
                pickle.dump(state, f)
            print(f"[Save] State saved → {self.save_path}")
        except Exception as e:
            print(f"[Save] Failed: {e}")

    def _load_state(self):
        try:
            with open(self.save_path, 'rb') as f:
                state = pickle.load(f)
            self.bus.cpu.a      = state['cpu_a']
            self.bus.cpu.x      = state['cpu_x']
            self.bus.cpu.y      = state['cpu_y']
            self.bus.cpu.sp     = state['cpu_sp']
            self.bus.cpu.pc     = state['cpu_pc']
            self.bus.cpu.status = state['cpu_st']
            self.bus.ram        = bytearray(state['ram'])
            self.bus.ppu.vram   = bytearray(state['vram'])
            self.bus.ppu.oam    = bytearray(state['oam'])
            self.bus.ppu.palette = bytearray(state['palette'])
            self.bus.cart.prg_ram = bytearray(state['prg_ram'])
            print(f"[Load] State loaded ← {self.save_path}")
        except FileNotFoundError:
            print("[Load] No save state found. Press F7 to save first.")
        except Exception as e:
            print(f"[Load] Failed: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    def _draw_debug(self):
        """Overlay CPU / PPU debug info at the bottom of the window."""
        cpu = self.bus.cpu
        ppu = self.bus.ppu
        y = SCREEN_H * self.scale + 2

        def flag(name, bit):
            return f"{name}={'1' if cpu.get_flag(bit) else '0'}"

        lines = [
            f"PC:{cpu.pc:04X}  A:{cpu.a:02X}  X:{cpu.x:02X}  Y:{cpu.y:02X}  SP:{cpu.sp:02X}",
            f"  {flag('N',7)} {flag('V',6)} {flag('B',4)} {flag('D',3)} {flag('I',2)} {flag('Z',1)} {flag('C',0)}"
            f"   SL:{ppu.scanline:3d} DOT:{ppu.dot:3d}  Cycles:{cpu.total_cycles}",
        ]
        for i, line in enumerate(lines):
            surf = self.font.render(line, True, (200, 255, 200), (0, 0, 0))
            self.screen.blit(surf, (4, y + i * 14))

    # ─────────────────────────────────────────────────────────────────────────
    def quit(self):
        pygame.quit()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="RetroPlay NES Emulator — play classic NES games")
    parser.add_argument("rom", nargs="?", help="Path to a .nes ROM file")
    parser.add_argument("--scale", type=int, default=3,
                        help="Screen scale factor (default: 3 → 768x720 window)")
    parser.add_argument("--debug", action="store_true",
                        help="Show CPU/PPU debug overlay")
    args = parser.parse_args()

    if not args.rom:
        # Show ROM picker if no file given
        pygame.init()
        root_dir = os.path.join(os.path.dirname(__file__), "roms")
        roms = [f for f in os.listdir(root_dir) if f.endswith(".nes")] if os.path.isdir(root_dir) else []

        if not roms:
            print("Usage: python main.py path/to/game.nes")
            print("  Put your .nes ROM files in the 'roms/' folder and re-run.")
            sys.exit(0)

        # Simple text-based picker
        print("\n=== RetroPlay NES Emulator ===")
        print("Available ROMs in ./roms/:\n")
        for i, r in enumerate(roms, 1):
            print(f"  {i:2}. {r}")
        try:
            choice = int(input("\nEnter number to play (or 0 to quit): "))
            if choice == 0:
                sys.exit(0)
            args.rom = os.path.join(root_dir, roms[choice - 1])
        except (ValueError, IndexError):
            print("Invalid choice.")
            sys.exit(1)

    if not os.path.isfile(args.rom):
        print(f"ROM not found: {args.rom}")
        sys.exit(1)

    emu = NESEmulator(args.rom, scale=args.scale, debug=args.debug)
    try:
        emu.run()
    finally:
        emu.quit()


if __name__ == "__main__":
    main()

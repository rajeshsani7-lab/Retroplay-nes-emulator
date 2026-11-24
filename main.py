"""
NES Emulator — Main Entry Point
Requires: pygame-ce, numpy

Usage:
  python main.py path/to/game.nes
  python main.py path/to/game.nes --fullscreen
  python main.py path/to/game.nes --scale 3

Controls (Player 1):
  Arrow Keys   → D-Pad
  Z / J        → A button (Jump)
  X / K        → B button (Shoot / Attack)
  Enter        → Start
  Space/Shift  → Select

Controls (Player 2):
  WASD         → D-Pad
  Comma (,)    → A button
  Period (.)   → B button
  1            → Start
  2            → Select

Emulator Hotkeys:
  F11          → Toggle Fullscreen / Windowed
  F10          → Toggle Aspect Ratio (16:9 Widescreen vs 4:3 Pillarbox)
  ESC          → Quit
  F5           → Reset
  F7           → Save state
  F8           → Load state
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
    import pygame.sndarray
except ImportError:
    print("ERROR: pygame not found. Please run via play.bat or install pygame-ce.")
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


class NESEmulator:
    def __init__(self, rom_path: str, scale: int = 3, debug: bool = False, frameskip: int = 0, fullscreen: bool = False):
        self.scale = scale
        self.debug = debug
        self.frameskip = frameskip
        self.frame_count = 0
        self.paused = False
        self.running = True
        self.fullscreen = fullscreen
        self.aspect_16_9 = True  # Default to 16:9 widescreen in fullscreen
        self.save_slot = 0

        # Load ROM and build system
        print(f"Loading ROM: {rom_path}")
        cart = Cartridge(rom_path)
        self.bus = Bus(cart)
        self.bus.reset()

        # Init pygame mixer with low-latency buffer before display init
        try:
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=1024)
            pygame.mixer.init()
        except Exception as e:
            print(f"[Audio] Mixer init warning: {e}")
        pygame.init()

        self.win_w = SCREEN_W * scale
        self.win_h = SCREEN_H * scale + (40 if debug else 0)

        if self.fullscreen:
            info = pygame.display.Info()
            self.screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode((self.win_w, self.win_h))
        pygame.display.set_caption(f"{WINDOW_TITLE} — {os.path.basename(rom_path)}")

        # NES frame surface (256x240)
        self.nes_surface = pygame.Surface((SCREEN_W, SCREEN_H))

        # Font for debug overlay
        self.font = pygame.font.SysFont("Consolas", 11) if debug else None

        # Audio Channel setup for smooth continuous playback
        self.audio_channel = None
        if pygame.mixer.get_init():
            try:
                self.audio_channel = pygame.mixer.Channel(0)
            except Exception:
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

        print("[Emulator] Ready! Press F11 for Fullscreen, F10 for 16:9 / 4:3 Toggle.")
        print("[Controls] Arrows=D-Pad  Enter=Start  Space/Shift=Select  Z/J=A (Jump)  X/K=B (Shoot)")

    # ─────────────────────────────────────────────────────────────────────────
    def run(self):
        target_frame_time = 1.0 / NES_FPS
        accumulator = 0.0
        last_time = time.perf_counter()

        while self.running:
            now = time.perf_counter()
            dt = now - last_time
            last_time = now
            if dt > 0.1:
                dt = 0.1
            accumulator += dt

            self._handle_events()
            self._update_controller_state()

            if not self.paused:
                frames_to_run = int(accumulator // target_frame_time)
                if frames_to_run > 0:
                    if frames_to_run > 3:
                        frames_to_run = 3
                        accumulator = 0.0

                    for i in range(frames_to_run):
                        accumulator -= target_frame_time
                        self.frame_count += 1
                        if self.frameskip > 0:
                            should_render = (self.frame_count % (self.frameskip + 1) == 0)
                        else:
                            should_render = (i == frames_to_run - 1)
                        self.bus.step_frame(render=should_render)

                    self._render_frame()
                    self._play_audio()

                    if self.debug:
                        self._draw_debug()

                    pygame.display.flip()

            # Responsive timing yield
            remaining = target_frame_time - (time.perf_counter() - now)
            if remaining > 0.001:
                time.sleep(remaining * 0.75)

    # ─────────────────────────────────────────────────────────────────────────
    def _update_controller_state(self):
        """Active hardware keyboard polling for ultra-smooth, responsive controls."""
        keys = pygame.key.get_pressed()

        # Controller 1: Bit 7=A, 6=B, 5=Select, 4=Start, 3=Up, 2=Down, 1=Left, 0=Right
        p1 = 0
        if keys[pygame.K_RIGHT]: p1 |= (1 << 0)
        if keys[pygame.K_LEFT]:  p1 |= (1 << 1)
        if keys[pygame.K_DOWN]:  p1 |= (1 << 2)
        if keys[pygame.K_UP]:    p1 |= (1 << 3)
        if keys[pygame.K_RETURN] or keys[pygame.K_KP_ENTER]: p1 |= (1 << 4)
        if keys[pygame.K_RSHIFT] or keys[pygame.K_LSHIFT] or keys[pygame.K_SPACE] or keys[pygame.K_TAB]:
            p1 |= (1 << 5)
        if keys[pygame.K_x] or keys[pygame.K_k]: p1 |= (1 << 6)  # B (Shoot)
        if keys[pygame.K_z] or keys[pygame.K_j]: p1 |= (1 << 7)  # A (Jump)

        # Smart Multicart Menu Navigation:
        # On Mapper 227 multicart menu (e.g. 1200-in-1), Select advances cursor down.
        # When RAM[0x38] == 0xFF, cart is on the menu screen, so pressing Down automatically sends Select.
        # In actual gameplay (Contra, etc.), RAM[0x38] != 0xFF, so Down is purely duck/aim down!
        if getattr(self.bus.cart, 'mapper_id', 0) == 227:
            if self.bus.ram[0x38] == 0xFF and (keys[pygame.K_DOWN] or keys[pygame.K_s]):
                p1 |= (1 << 5)

        self.bus.controller1 = p1

        # Controller 2
        p2 = 0
        if keys[pygame.K_d]: p2 |= (1 << 0)
        if keys[pygame.K_a]: p2 |= (1 << 1)
        if keys[pygame.K_s]: p2 |= (1 << 2)
        if keys[pygame.K_w]: p2 |= (1 << 3)
        if keys[pygame.K_1]: p2 |= (1 << 4)
        if keys[pygame.K_2]: p2 |= (1 << 5)
        if keys[pygame.K_PERIOD]: p2 |= (1 << 6)
        if keys[pygame.K_COMMA]:  p2 |= (1 << 7)
        self.bus.controller2 = p2

    # ─────────────────────────────────────────────────────────────────────────
    def _render_frame(self):
        """Blit the PPU frame buffer onto the pygame window (supporting 16:9 & 4:3)."""
        surf = pygame.image.frombuffer(self.bus.ppu.frame.data, (SCREEN_W, SCREEN_H), 'RGB')

        if self.fullscreen:
            win_w, win_h = self.screen.get_size()
            if self.aspect_16_9:
                # 16:9 Fullscreen Stretch (Edge-to-Edge)
                scaled = pygame.transform.scale(surf, (win_w, win_h))
                self.screen.blit(scaled, (0, 0))
            else:
                # 4:3 Classic Pillarbox
                scale = min(win_w / SCREEN_W, win_h / SCREEN_H)
                dest_w = int(SCREEN_W * scale)
                dest_h = int(SCREEN_H * scale)
                offset_x = (win_w - dest_w) // 2
                offset_y = (win_h - dest_h) // 2
                self.screen.fill((0, 0, 0))
                scaled = pygame.transform.scale(surf, (dest_w, dest_h))
                self.screen.blit(scaled, (offset_x, offset_y))
        else:
            scaled = pygame.transform.scale_by(surf, self.scale)
            self.screen.blit(scaled, (0, 0))

    # ─────────────────────────────────────────────────────────────────────────
    def _play_audio(self):
        """Synthesize frame audio and stream seamlessly to pygame mixer."""
        if not pygame.mixer.get_init():
            return
        audio_data = self.bus.apu.generate_frame_audio(count=735)
        if audio_data is not None and len(audio_data) > 0:
            try:
                sound = pygame.sndarray.make_sound(audio_data)
                if self.audio_channel is None:
                    self.audio_channel = pygame.mixer.Channel(0)
                if not self.audio_channel.get_busy():
                    self.audio_channel.play(sound)
                elif self.audio_channel.get_queue() is None:
                    self.audio_channel.queue(sound)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_F5:
                    print("[Emulator] Reset")
                    self.bus.reset()
                elif event.key == pygame.K_p:
                    self.paused = not self.paused
                    print(f"[Emulator] {'Paused' if self.paused else 'Resumed'}")
                elif event.key == pygame.K_F7:
                    self._save_state()
                elif event.key == pygame.K_F8:
                    self._load_state()
                elif event.key == pygame.K_F11:
                    self._toggle_fullscreen()
                elif event.key == pygame.K_F10:
                    self._toggle_aspect()

    # ─────────────────────────────────────────────────────────────────────────
    def _toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            info = pygame.display.Info()
            self.screen = pygame.display.set_mode(
                (info.current_w, info.current_h), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode((self.win_w, self.win_h))
        mode_str = "Fullscreen (16:9)" if (self.fullscreen and self.aspect_16_9) else ("Fullscreen (4:3)" if self.fullscreen else "Windowed")
        print(f"[Display] {mode_str}")

    def _toggle_aspect(self):
        self.aspect_16_9 = not self.aspect_16_9
        aspect_str = "16:9 Widescreen Stretch" if self.aspect_16_9 else "4:3 Classic Pillarbox"
        print(f"[Display] Aspect ratio set to: {aspect_str}")

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
    parser.add_argument("--fullscreen", action="store_true",
                        help="Start directly in 16:9 fullscreen mode")
    parser.add_argument("--frameskip", type=int, default=0,
                        help="Frame skip (0=dynamic auto, 1=render every 2nd frame for maximum speed)")
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

    emu = NESEmulator(args.rom, scale=args.scale, debug=args.debug, frameskip=args.frameskip, fullscreen=args.fullscreen)
    try:
        emu.run()
    finally:
        emu.quit()


if __name__ == "__main__":
    main()


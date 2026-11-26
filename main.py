"""
NES Emulator — Main Entry Point
Requires: pygame-ce, numpy

Usage:
  python main.py                  (opens graphical ROM browser)
  python main.py path/to/game.nes
  python main.py path/to/game.nes --fullscreen
  python main.py path/to/game.nes --scale 3

Emulator Hotkeys:
  ESC / F1     → Pause & Settings Menu
  F11          → Toggle Fullscreen / Windowed
  F10          → Cycle Aspect Ratio (16:9 / 4:3 / 1:1)
  F9           → Cycle CRT Scanlines (Off / Light / Classic)
  F3           → Toggle FPS & Slot HUD
  F5           → Reset Console
  F7 / F8      → Save / Load State (current slot)
  F12          → Take Screenshot
  M            → Mute / Unmute Audio
  Tab (hold)   → Fast Forward (2x/3x/4x)
  P            → Quick Pause / Resume
"""

import sys
import os
import time
import argparse
import pickle
import ctypes
import numpy as np

try:
    import pygame
    import pygame.mixer
    import pygame.sndarray
except ImportError:
    print("ERROR: pygame not found. Please run via play.bat or install pygame-ce.")
    sys.exit(1)

from nes_core.cartridge   import Cartridge
from nes_core.bus         import Bus
from nes_core.settings    import load_settings, save_settings
from nes_core.ui          import EmulatorUI
from nes_core.controllers import GamepadManager

# NES timing constants
NES_FPS         = 60.0988
NES_CPU_CLOCK   = 1789773   # Hz
NES_PPU_CLOCK   = NES_CPU_CLOCK * 3
CYCLES_PER_FRAME = NES_CPU_CLOCK / NES_FPS

SCREEN_W = 256
SCREEN_H = 240
WINDOW_TITLE = "RetroPlay NES Emulator"


class NESEmulator:
    def __init__(self, rom_path: str = None, scale: int = None, debug: bool = False,
                 frameskip: int = None, fullscreen: bool = None):
        self.cfg = load_settings()
        if scale is not None:
            self.cfg["video"]["scale"] = scale
        if frameskip is not None:
            self.cfg["video"]["frameskip"] = frameskip
        if fullscreen is not None:
            self.cfg["video"]["fullscreen"] = fullscreen

        self.debug = debug
        self.frame_count = 0
        self.paused = False
        self.running = True
        self.rom_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roms")
        self.rom_path = rom_path

        # Enable 1ms Windows timer precision so time.sleep() never overshoots by 15ms
        self._winmm = None
        if sys.platform == "win32":
            try:
                self._winmm = ctypes.windll.winmm
                self._winmm.timeBeginPeriod(1)
            except Exception:
                self._winmm = None

        # Init pygame mixer with 512-sample buffer (~11.6ms < 16.6ms frame) + seamless PCM staging
        try:
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.init()
        except Exception as e:
            print(f"[Audio] Mixer init warning: {e}")
        pygame.init()
        self.gamepads = GamepadManager()

        self.bus = None
        if rom_path:
            self._load_rom(rom_path)

        self._apply_video_mode()
        self.nes_surface = pygame.Surface((SCREEN_W, SCREEN_H))
        self._scaled_surf = None
        self._scaled_size = (0, 0)
        self._scanline_surf = None
        self._scanline_key = None
        self._latched_p1 = 0
        self._latched_p2 = 0

        self.font = pygame.font.SysFont("Consolas", 11) if debug else None
        self.ui = EmulatorUI(self.cfg)
        if not self.bus:
            self.ui.open_rom_browser(self.rom_dir)

        # Audio Channel setup with seamless PCM staging buffer
        self.audio_channel = None
        self._audio_buf = []
        self._audio_samples = 0
        if pygame.mixer.get_init():
            try:
                self.audio_channel = pygame.mixer.Channel(0)
            except Exception:
                self.audio_channel = None

        self._apply_audio_settings()
        self._fps_ema = 60.0
        self._last_fps_t = time.perf_counter()

        print("[Emulator] Ready! Press ESC/F1 for Pause & Settings Menu, F11 Fullscreen, F10 Aspect, F9 Scanlines.")
        print("[Controls] Arrows=D-Pad  Enter=Start  RShift/Space=Select  Z/J=A  X/K=B  Tab=Fast-Forward")

    # ─────────────────────────────────────────────────────────────────────────
    def _save_thumb(self):
        """Cache a 256x240 cover preview thumbnail in roms/saves/ when pausing or switching games."""
        if not self.bus or not self.rom_path or not self.save_dir:
            return
        try:
            base = os.path.splitext(os.path.basename(self.rom_path))[0]
            tp = os.path.join(self.save_dir, f"{base}.thumb.png")
            if int((self.bus.ppu.frame != 0).sum()) > 0:
                surf = pygame.image.frombuffer(self.bus.ppu.frame.data, (SCREEN_W, SCREEN_H), 'RGB')
                pygame.image.save(surf, tp)
                self.ui.invalidate_thumb(self.rom_path)
        except Exception:
            pass

    def _load_rom(self, rom_path: str):
        if self.bus:
            self._save_thumb()
            if self.cfg["emulation"].get("auto_sram", True):
                self._save_sram()
        print(f"Loading ROM: {rom_path}")
        cart = Cartridge(rom_path)
        self.bus = Bus(cart)
        self.bus.reset()
        self.rom_path = rom_path
        self.save_dir = os.path.join(os.path.dirname(os.path.abspath(rom_path)), "saves")
        os.makedirs(self.save_dir, exist_ok=True)
        if self.cfg["emulation"].get("auto_sram", True):
            self._load_sram()
        if pygame.display.get_surface():
            pygame.display.set_caption(f"{WINDOW_TITLE} — {os.path.basename(rom_path)}")
        self._apply_audio_settings()

    def _sram_path(self) -> str:
        base = os.path.splitext(os.path.basename(self.rom_path))[0]
        return os.path.join(self.save_dir, f"{base}.sav")

    def _save_sram(self):
        if not self.bus or not self.rom_path or not self.bus.cart.header.has_battery_ram:
            return
        try:
            with open(self._sram_path(), "wb") as f:
                f.write(self.bus.cart.prg_ram)
        except Exception:
            pass

    def _load_sram(self):
        if not self.bus or not self.rom_path or not self.bus.cart.header.has_battery_ram:
            return
        p = self._sram_path()
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    data = f.read(8192)
                self.bus.cart.prg_ram[:len(data)] = data
                print(f"[SRAM] Loaded battery save ← {p}")
            except Exception:
                pass

    def _get_save_path(self, slot: int = None) -> str:
        if slot is None:
            slot = self.cfg["emulation"]["save_slot"]
        base = os.path.splitext(os.path.basename(self.rom_path))[0]
        return os.path.join(self.save_dir, f"{base}.slot{slot}.state")

    def _apply_video_mode(self):
        v = self.cfg["video"]
        self.scale = v["scale"]
        self.fullscreen = v["fullscreen"]
        self.frameskip = v["frameskip"]
        aspect = v.get("aspect_mode", "4:3")
        base_h = SCREEN_H * self.scale
        if aspect == "4:3":
            self.win_w = int(round(base_h * 4.0 / 3.0))
        elif aspect == "16:9":
            self.win_w = int(round(base_h * 16.0 / 9.0))
        else:
            self.win_w = SCREEN_W * self.scale
        self.win_h = base_h + (40 if self.debug else 0)
        if self.fullscreen:
            info = pygame.display.Info()
            self.screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN | pygame.DOUBLEBUF)
        else:
            self.screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.DOUBLEBUF)
        title_suffix = f" — {os.path.basename(self.rom_path)}" if self.rom_path else ""
        pygame.display.set_caption(f"{WINDOW_TITLE}{title_suffix}")
        self._scaled_surf = None
        self._scanline_surf = None

    def _apply_audio_settings(self):
        if not self.bus:
            return
        a = self.cfg["audio"]
        apu = self.bus.apu
        apu.master_volume = (a["volume"] / 100.0) if a["enabled"] else 0.0
        apu.lowpass_enabled = a["lowpass"]
        apu.mute_p1 = not a["pulse1"]
        apu.mute_p2 = not a["pulse2"]
        apu.mute_tri = not a["triangle"]
        apu.mute_noi = not a["noise"]

    # ─────────────────────────────────────────────────────────────────────────
    def run(self):
        target_frame_time = 1.0 / NES_FPS
        next_frame_time = time.perf_counter()

        while self.running:
            now = time.perf_counter()
            remaining = next_frame_time - now
            if remaining > 0.002:
                time.sleep(remaining - 0.0015)
            while time.perf_counter() < next_frame_time:
                pass

            self._handle_events()

            if self.ui.mode is not None or not self.bus:
                if self.bus:
                    self._render_frame()
                else:
                    is_light = self.cfg["video"].get("theme", "light") == "light"
                    self.screen.fill((238, 234, 224) if is_light else (14, 16, 26))
                self.ui.draw(self.screen, self.gamepads)
                pygame.display.flip()
                next_frame_time = time.perf_counter() + target_frame_time
                continue

            self._update_controller_state()

            keys = pygame.key.get_pressed()
            ff_active = bool(keys[pygame.K_TAB])
            sub_steps = self.cfg["emulation"]["fast_forward_speed"] if ff_active else 1

            if not self.paused:
                behind = time.perf_counter() > next_frame_time
                for s in range(sub_steps):
                    self.frame_count += 1
                    should_render = (s == sub_steps - 1)
                    if self.frameskip > 0 and should_render:
                        should_render = (self.frame_count % (self.frameskip + 1) == 0)
                    elif behind and should_render and (self.frame_count % 2 == 1):
                        # Auto-frameskip when behind 60Hz so audio & physics stay at full speed
                        should_render = False

                    self.bus.step_frame(render=should_render)
                    if s == 0:
                        self._play_audio()

                    if should_render:
                        self._render_frame()
                        if self.debug:
                            self._draw_debug()
                        self.ui.draw_hud(self.screen, self._fps_ema, ff_active)
                        pygame.display.flip()

            cur = time.perf_counter()
            dt = cur - self._last_fps_t
            self._last_fps_t = cur
            if dt > 0:
                inst_fps = sub_steps / dt
                self._fps_ema = self._fps_ema * 0.9 + inst_fps * 0.1

            next_frame_time += target_frame_time
            if cur - next_frame_time > target_frame_time * 3:
                next_frame_time = cur

    # ─────────────────────────────────────────────────────────────────────────
    def _update_controller_state(self):
        """Hardware keyboard + USB/Bluetooth gamepad polling with quick-tap latching."""
        keys = pygame.key.get_pressed()
        c1 = self.cfg["controls_p1"]

        # Controller 1: Bit 7=A, 6=B, 5=Select, 4=Start, 3=Up, 2=Down, 1=Left, 0=Right
        p1 = self._latched_p1
        self._latched_p1 = 0
        if keys[c1["Right"]]: p1 |= (1 << 0)
        if keys[c1["Left"]]:  p1 |= (1 << 1)
        if keys[c1["Down"]]:  p1 |= (1 << 2)
        if keys[c1["Up"]]:    p1 |= (1 << 3)
        if keys[c1["Start"]] or keys[pygame.K_KP_ENTER]: p1 |= (1 << 4)
        if keys[c1["Select"]] or keys[pygame.K_LSHIFT] or keys[pygame.K_SPACE]: p1 |= (1 << 5)
        if keys[c1["B"]] or keys[pygame.K_k]: p1 |= (1 << 6)
        if keys[c1["A"]] or keys[pygame.K_j]: p1 |= (1 << 7)

        # Universal USB / Bluetooth Gamepad 0 -> Player 1
        gp1, _ = self.gamepads.read_player(0)
        p1 |= gp1

        # Smart Multicart Menu Navigation (only when in Mapper 227 menu bank 0):
        if self.bus.cart.header.mapper_id == 227 and getattr(self.bus.cart.mapper, 'prg_bank_lo', 1) == 0:
            if self.bus.ram[0x38] == 0xFF and (keys[c1["Down"]] or keys[pygame.K_s] or (gp1 & (1 << 2))):
                p1 |= (1 << 5)

        self.bus.controller1 = p1

        # Controller 2 (Keyboard + Gamepad 1 if connected)
        p2 = self._latched_p2
        self._latched_p2 = 0
        if keys[pygame.K_d]: p2 |= (1 << 0)
        if keys[pygame.K_a]: p2 |= (1 << 1)
        if keys[pygame.K_s]: p2 |= (1 << 2)
        if keys[pygame.K_w]: p2 |= (1 << 3)
        if keys[pygame.K_1]: p2 |= (1 << 4)
        if keys[pygame.K_2]: p2 |= (1 << 5)
        if keys[pygame.K_PERIOD]: p2 |= (1 << 6)
        if keys[pygame.K_COMMA]:  p2 |= (1 << 7)
        gp2, _ = self.gamepads.read_player(1)
        p2 |= gp2
        self.bus.controller2 = p2

    # ─────────────────────────────────────────────────────────────────────────
    def _scale_into_cache(self, surf, dest_w, dest_h):
        if self._scaled_size != (dest_w, dest_h) or self._scaled_surf is None:
            self._scaled_surf = pygame.Surface((dest_w, dest_h), 0, surf)
            self._scaled_size = (dest_w, dest_h)
        if self.cfg["video"]["smooth_scaling"]:
            pygame.transform.smoothscale(surf, (dest_w, dest_h), self._scaled_surf)
        else:
            pygame.transform.scale(surf, (dest_w, dest_h), self._scaled_surf)
        return self._scaled_surf

    def _apply_scanlines(self, dest_w, dest_h, offset_x, offset_y):
        level = self.cfg["video"]["scanlines"]
        if level == 0:
            return
        alpha = 55 if level == 1 else 115
        key = (dest_w, dest_h, alpha)
        if self._scanline_key != key or self._scanline_surf is None:
            sl = pygame.Surface((dest_w, dest_h), pygame.SRCALPHA)
            step = max(2, dest_h // SCREEN_H)
            for y in range(0, dest_h, step):
                pygame.draw.line(sl, (0, 0, 0, alpha), (0, y), (dest_w, y))
            self._scanline_surf = sl
            self._scanline_key = key
        self.screen.blit(self._scanline_surf, (offset_x, offset_y))

    def _render_frame(self):
        """Blit the PPU frame buffer onto the pygame window with overscan, true 4:3/1:1/16:9 aspect & scanlines."""
        surf = pygame.image.frombuffer(self.bus.ppu.frame.data, (SCREEN_W, SCREEN_H), 'RGB')
        if self.cfg["video"].get("overscan_crop", True):
            surf = surf.subsurface((0, 8, SCREEN_W, SCREEN_H - 16))
        src_w, src_h = surf.get_size()

        win_w, win_h = self.screen.get_size()
        game_h = win_h - (40 if (self.debug and not self.fullscreen) else 0)
        aspect = self.cfg["video"].get("aspect_mode", "4:3")

        if aspect == "16:9":
            dest_w, dest_h = win_w, game_h
        elif aspect == "1:1":
            int_s = max(1, min(win_w // src_w, game_h // src_h))
            dest_w, dest_h = src_w * int_s, src_h * int_s
        else:
            # Classic 4:3 CRT TV aspect ratio
            if win_w / max(1, game_h) >= 4.0 / 3.0:
                dest_h = game_h
                dest_w = int(round(dest_h * 4.0 / 3.0))
            else:
                dest_w = win_w
                dest_h = int(round(dest_w * 3.0 / 4.0))

        offset_x = (win_w - dest_w) // 2
        offset_y = (game_h - dest_h) // 2
        if offset_x > 0 or offset_y > 0:
            self.screen.fill((0, 0, 0))
        scaled = self._scale_into_cache(surf, dest_w, dest_h)
        self.screen.blit(scaled, (offset_x, offset_y))
        self._apply_scanlines(dest_w, dest_h, offset_x, offset_y)

    # ─────────────────────────────────────────────────────────────────────────
    def _play_audio(self):
        """Synthesize frame audio and stream seamlessly without dropping chunks."""
        if not pygame.mixer.get_init() or not self.cfg["audio"]["enabled"]:
            return
        audio_data = self.bus.apu.generate_frame_audio(count=None)
        if audio_data is not None and len(audio_data) > 0:
            self._audio_buf.append(audio_data)
            self._audio_samples += len(audio_data)

        while self._audio_samples > 2940 and len(self._audio_buf) > 1:
            dropped = self._audio_buf.pop(0)
            self._audio_samples -= len(dropped)

        if self.audio_channel is None:
            try:
                self.audio_channel = pygame.mixer.Channel(0)
            except Exception:
                return

        if not self._audio_buf:
            return

        try:
            need_play = not self.audio_channel.get_busy()
            need_queue = (not need_play) and (self.audio_channel.get_queue() is None)
            if need_play or need_queue:
                merged = self._audio_buf[0] if len(self._audio_buf) == 1 else np.vstack(self._audio_buf)
                self._audio_buf.clear()
                self._audio_samples = 0
                sound = pygame.sndarray.make_sound(merged)
                if need_play:
                    self.audio_channel.play(sound)
                else:
                    self.audio_channel.queue(sound)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    def _dispatch_ui_result(self, res):
        if not res:
            return
        act = res.get("action")
        if act == "quit":
            self.running = False
        elif act == "reset" and self.bus:
            self.bus.reset()
            self.ui.show_toast("Console Reset")
        elif act == "save":
            self._save_state()
        elif act == "load":
            self._load_state()
        elif act == "screenshot":
            self._take_screenshot()
        elif act == "apply_video":
            self._apply_video_mode()
        elif act == "apply_audio":
            self._apply_audio_settings()
        elif act == "load_rom":
            self._load_rom(res["path"])
            self.paused = False

    def _handle_events(self):
        c1 = self.cfg["controls_p1"]
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                continue

            if event.type == pygame.JOYDEVICEADDED:
                name = self.gamepads.add_device(event.device_index)
                if name:
                    self.ui.show_toast(f"Controller Connected: {name[:24]}")
                continue

            if event.type == pygame.JOYDEVICEREMOVED:
                name = self.gamepads.remove_device(event.instance_id)
                if name:
                    self.ui.show_toast(f"Controller Removed: {name[:24]}")
                continue

            if self.ui.mode is not None:
                res = self.ui.handle_event(event, self.rom_dir)
                self._dispatch_ui_result(res)
                continue

            if event.type == pygame.KEYDOWN:
                k = event.key
                if k == c1["Right"]:  self._latched_p1 |= (1 << 0)
                elif k == c1["Left"]: self._latched_p1 |= (1 << 1)
                elif k == c1["Down"]: self._latched_p1 |= (1 << 2)
                elif k == c1["Up"]:   self._latched_p1 |= (1 << 3)
                elif k in (c1["Start"], pygame.K_KP_ENTER): self._latched_p1 |= (1 << 4)
                elif k in (c1["Select"], pygame.K_LSHIFT, pygame.K_SPACE): self._latched_p1 |= (1 << 5)
                elif k in (c1["B"], pygame.K_k): self._latched_p1 |= (1 << 6)
                elif k in (c1["A"], pygame.K_j): self._latched_p1 |= (1 << 7)
                elif k == pygame.K_d:      self._latched_p2 |= (1 << 0)
                elif k == pygame.K_a:      self._latched_p2 |= (1 << 1)
                elif k == pygame.K_s:      self._latched_p2 |= (1 << 2)
                elif k == pygame.K_w:      self._latched_p2 |= (1 << 3)
                elif k == pygame.K_1:      self._latched_p2 |= (1 << 4)
                elif k == pygame.K_2:      self._latched_p2 |= (1 << 5)
                elif k == pygame.K_PERIOD: self._latched_p2 |= (1 << 6)
                elif k == pygame.K_COMMA:  self._latched_p2 |= (1 << 7)

                if k in (pygame.K_ESCAPE, pygame.K_F1):
                    self._save_thumb()
                    self.ui.open_pause()
                elif k == pygame.K_F5 and self.bus:
                    self.bus.reset()
                    self.ui.show_toast("Console Reset")
                elif k == pygame.K_p:
                    self.paused = not self.paused
                    self.ui.show_toast("Paused" if self.paused else "Resumed")
                elif k == pygame.K_F7:
                    self._save_state()
                elif k == pygame.K_F8:
                    self._load_state()
                elif k == pygame.K_F9:
                    self.cfg["video"]["scanlines"] = (self.cfg["video"]["scanlines"] + 1) % 3
                    save_settings(self.cfg)
                    lbl = ("Off", "Light", "Classic")[self.cfg["video"]["scanlines"]]
                    self.ui.show_toast(f"Scanlines: {lbl}")
                elif k == pygame.K_F10:
                    modes = ["16:9", "4:3", "1:1"]
                    cur_m = self.cfg["video"]["aspect_mode"]
                    nxt = modes[(modes.index(cur_m) + 1) % len(modes)]
                    self.cfg["video"]["aspect_mode"] = nxt
                    save_settings(self.cfg)
                    self.ui.show_toast(f"Aspect: {nxt}")
                elif k == pygame.K_F11:
                    self.cfg["video"]["fullscreen"] = not self.cfg["video"]["fullscreen"]
                    save_settings(self.cfg)
                    self._apply_video_mode()
                elif k == pygame.K_F12:
                    self._take_screenshot()
                elif k == pygame.K_F3:
                    self.cfg["video"]["show_fps"] = not self.cfg["video"]["show_fps"]
                    save_settings(self.cfg)
                elif k == pygame.K_m:
                    self.cfg["audio"]["enabled"] = not self.cfg["audio"]["enabled"]
                    save_settings(self.cfg)
                    self._apply_audio_settings()
                    self.ui.show_toast("Audio On" if self.cfg["audio"]["enabled"] else "Audio Muted")

        # Gamepad menu navigation or Guide / Select+Start pause trigger
        if self.ui.mode is not None:
            for gk in self.gamepads.poll_menu_keys():
                res = self.ui.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": gk}), self.rom_dir)
                self._dispatch_ui_result(res)
        elif self.gamepads.check_pause_pressed():
            self.ui.open_pause()

    # ─────────────────────────────────────────────────────────────────────────
    def _take_screenshot(self):
        if not self.bus:
            return
        shot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(shot_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(self.rom_path))[0]
        fname = f"{base}_{int(time.time())}.png"
        out = os.path.join(shot_dir, fname)
        surf = pygame.image.frombuffer(self.bus.ppu.frame.data, (SCREEN_W, SCREEN_H), 'RGB')
        pygame.image.save(surf, out)
        self.ui.show_toast(f"Screenshot: {fname}")

    # ─────────────────────────────────────────────────────────────────────────
    def _save_state(self):
        if not self.bus:
            return
        try:
            ppu = self.bus.ppu
            slot = self.cfg["emulation"]["save_slot"]
            path = self._get_save_path(slot)
            mapper_state = {
                k: v for k, v in self.bus.cart.mapper.__dict__.items()
                if k not in ('cart', 'prg_rom', 'chr_rom')
            }
            state = {
                'cpu_a':   self.bus.cpu.a,
                'cpu_x':   self.bus.cpu.x,
                'cpu_y':   self.bus.cpu.y,
                'cpu_sp':  self.bus.cpu.sp,
                'cpu_pc':  self.bus.cpu.pc,
                'cpu_st':  self.bus.cpu.status,
                'ram':     bytes(self.bus.ram),
                'vram':    bytes(ppu.vram),
                'oam':     bytes(ppu.oam),
                'palette': bytes(ppu.palette),
                'ppu_regs': (ppu.ctrl, ppu.mask, ppu.status, ppu.v, ppu.t, ppu.x, ppu.w, ppu.oam_addr, ppu.scroll_x, ppu.scroll_y),
                'prg_ram': bytes(self.bus.cart.prg_ram),
                'chr_ram': bytes(self.bus.cart.chr_rom) if self.bus.cart.header.chr_rom_size == 0 else None,
                'mapper':  mapper_state,
            }
            with open(path, 'wb') as f:
                pickle.dump(state, f)
            self.ui.show_toast(f"State Saved (Slot {slot})")
        except Exception as e:
            self.ui.show_toast(f"Save Failed: {e}")

    def _load_state(self):
        if not self.bus:
            return
        slot = self.cfg["emulation"]["save_slot"]
        path = self._get_save_path(slot)
        try:
            with open(path, 'rb') as f:
                state = pickle.load(f)
            ppu = self.bus.ppu
            self.bus.cpu.a      = state['cpu_a']
            self.bus.cpu.x      = state['cpu_x']
            self.bus.cpu.y      = state['cpu_y']
            self.bus.cpu.sp     = state['cpu_sp']
            self.bus.cpu.pc     = state['cpu_pc']
            self.bus.cpu.status = state['cpu_st']
            self.bus.ram[:]     = state['ram']
            ppu.vram[:]         = state['vram']
            ppu.oam[:]          = state['oam']
            ppu.palette[:]      = state['palette']
            ppu.pal_dirty       = True
            if 'ppu_regs' in state:
                (ppu.ctrl, ppu.mask, ppu.status, ppu.v, ppu.t,
                 ppu.x, ppu.w, ppu.oam_addr, ppu.scroll_x, ppu.scroll_y) = state['ppu_regs']
            self.bus.cart.prg_ram[:] = state['prg_ram']
            if state.get('chr_ram') is not None and self.bus.cart.header.chr_rom_size == 0:
                self.bus.cart.chr_rom[:] = state['chr_ram']
            if 'mapper' in state:
                for k, v in state['mapper'].items():
                    setattr(self.bus.cart.mapper, k, v)
            self.ui.show_toast(f"State Loaded (Slot {slot})")
        except FileNotFoundError:
            self.ui.show_toast(f"No Save in Slot {slot}")
        except Exception as e:
            self.ui.show_toast(f"Load Failed: {e}")

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

    def quit(self):
        if self.bus:
            self._save_thumb()
            if self.cfg["emulation"].get("auto_sram", True):
                self._save_sram()
        self.gamepads.quit()
        if self._winmm is not None:
            try:
                self._winmm.timeEndPeriod(1)
            except Exception:
                pass
        pygame.quit()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="RetroPlay NES Emulator — play classic NES games")
    parser.add_argument("rom", nargs="?", help="Path to a .nes ROM file (optional; opens ROM browser)")
    parser.add_argument("--scale", type=int, default=None,
                        help="Screen scale factor (2, 3, or 4)")
    parser.add_argument("--fullscreen", action="store_true",
                        help="Start directly in fullscreen mode")
    parser.add_argument("--frameskip", type=int, default=None,
                        help="Frame skip (0=none, 1=render every 2nd frame)")
    parser.add_argument("--debug", action="store_true",
                        help="Show CPU/PPU debug overlay")
    args = parser.parse_args()

    if args.rom and not os.path.isfile(args.rom):
        print(f"ROM not found: {args.rom}")
        sys.exit(1)

    emu = NESEmulator(
        args.rom,
        scale=args.scale,
        debug=args.debug,
        frameskip=args.frameskip,
        fullscreen=True if args.fullscreen else None,
    )
    try:
        emu.run()
    finally:
        emu.quit()


if __name__ == "__main__":
    main()

"""RetroPlay NES Emulator — Console-Grade UI, Cartridge Art, Settings & ROM Library."""

import os
import time
import pygame
from .cartridge import Header
from .settings import DEFAULT_SETTINGS, save_settings

BUTTON_NAMES = ["Up", "Down", "Left", "Right", "A", "B", "Start", "Select"]

MAPPER_NAMES = {
    0: "NROM",
    1: "MMC1",
    2: "UxROM",
    3: "CNROM",
    4: "MMC3",
    9: "MMC2",
    23: "VRC2/4",
    227: "Multi",
}

MAPPER_COLORS_LIGHT = {
    0: (28, 92, 188),
    1: (116, 62, 186),
    2: (196, 76, 44),
    3: (24, 134, 68),
    4: (188, 96, 18),
    9: (186, 48, 86),
    23: (194, 34, 46),
    227: (22, 128, 72),
}

MAPPER_COLORS_DARK = {
    0: (88, 166, 255),
    1: (163, 113, 247),
    2: (247, 129, 102),
    3: (63, 185, 80),
    4: (255, 166, 87),
    9: (242, 109, 133),
    23: (230, 57, 70),
    227: (86, 211, 100),
}

THEMES = {
    "light": {
        "bg":             (238, 234, 224),
        "bg_grid":        (225, 219, 205),
        "header_bg":      (248, 245, 238),
        "panel_bg":       (250, 248, 242),
        "card_bg":        (250, 248, 242, 255),
        "card_inner":     (232, 227, 215),
        "card_hover":     (222, 216, 202),
        "badge_bg":       (244, 240, 231),
        "accent":         (36, 98, 196),
        "accent_hi":      (22, 72, 158),
        "accent_soft":    (216, 230, 250),
        "sel_text":       (184, 28, 38),
        "red_nes":        (214, 40, 50),
        "red_hi":         (232, 58, 68),
        "green_ok":       (22, 136, 74),
        "green_bg":       (210, 240, 220),
        "text":           (34, 37, 46),
        "muted":          (102, 107, 120),
        "border":         (202, 195, 180),
        "tab_fg":         (255, 255, 255),
        "track_bg":       (212, 206, 192),
        "toggle_off_bg":  (212, 206, 192),
        "set_btn_bg":     (222, 233, 248),
        "set_btn_hover":  (198, 218, 246),
        "set_btn_fg":     (22, 72, 158),
        "quit_btn_bg":    (248, 224, 226),
        "quit_btn_hover": (244, 200, 204),
        "quit_btn_fg":    (176, 30, 40),
        "overlay":        (32, 35, 44, 168),
        "art_frame":      (236, 231, 218),
        "cart_shell":     (166, 170, 178),
        "cart_groove":    (124, 128, 138),
        "mapper_colors":  MAPPER_COLORS_LIGHT,
    },
    "dark": {
        "bg":             (10, 12, 20),
        "bg_grid":        (19, 23, 37),
        "header_bg":      (15, 19, 32),
        "panel_bg":       (15, 19, 32),
        "card_bg":        (16, 20, 34, 246),
        "card_inner":     (24, 29, 47),
        "card_hover":     (32, 40, 64),
        "badge_bg":       (28, 36, 58),
        "accent":         (88, 166, 255),
        "accent_hi":      (132, 198, 255),
        "accent_soft":    (35, 62, 110),
        "sel_text":       (255, 210, 102),
        "red_nes":        (229, 57, 70),
        "red_hi":         (246, 84, 96),
        "green_ok":       (63, 195, 128),
        "green_bg":       (28, 92, 66),
        "text":           (242, 245, 252),
        "muted":          (146, 158, 184),
        "border":         (46, 58, 88),
        "tab_fg":         (12, 14, 22),
        "track_bg":       (38, 44, 66),
        "toggle_off_bg":  (42, 48, 68),
        "set_btn_bg":     (35, 62, 110),
        "set_btn_hover":  (52, 88, 150),
        "set_btn_fg":     (132, 198, 255),
        "quit_btn_bg":    (62, 30, 38),
        "quit_btn_hover": (96, 38, 48),
        "quit_btn_fg":    (255, 148, 156),
        "overlay":        (7, 9, 16, 230),
        "art_frame":      (22, 27, 44),
        "cart_shell":     (112, 118, 134),
        "cart_groove":    (84, 90, 106),
        "mapper_colors":  MAPPER_COLORS_DARK,
    },
}


class EmulatorUI:
    """Renders the ROM Library, Pause Menu, 4-Tab Settings Menu, and HUD."""

    GOLD = (255, 210, 102)

    def _theme(self):
        name = self.cfg.get("video", {}).get("theme", "light")
        return THEMES.get(name, THEMES["light"])

    def __init__(self, settings: dict):
        self.cfg = settings
        self.mode = None          # None, "pause", "settings", "rom_browser"
        self.return_mode = "pause"
        self.cursor = 0
        self.scroll_offset = 0
        self.settings_tab = 0     # 0=Video, 1=Audio, 2=Emulation, 3=Controls
        self.waiting_key_for = None
        self.roms = []            # list of (display_name, filename, full_path, info_dict)
        self.toast_text = ""
        self.toast_until = 0.0
        self._click_zones = []    # list of (pygame.Rect, callback_tag, payload)
        self._hover_zone = None
        self._thumb_cache = {}    # rom_path -> pygame.Surface or None
        self._last_rom_dir = ""

        pygame.font.init()
        self.font_brand = pygame.font.SysFont("Segoe UI", 26, bold=True)
        self.font_title = pygame.font.SysFont("Segoe UI", 19, bold=True)
        self.font_item  = pygame.font.SysFont("Segoe UI", 16, bold=False)
        self.font_bold  = pygame.font.SysFont("Segoe UI", 16, bold=True)
        self.font_small = pygame.font.SysFont("Consolas", 13)
        self.font_badge = pygame.font.SysFont("Consolas", 12, bold=True)
        self.font_hud   = pygame.font.SysFont("Consolas", 14, bold=True)

    def show_toast(self, text: str, duration: float = 2.0):
        self.toast_text = text
        self.toast_until = time.perf_counter() + duration

    def scan_roms(self, rom_dir: str):
        self._last_rom_dir = rom_dir
        self.roms = []
        self._thumb_cache.clear()
        if not os.path.isdir(rom_dir):
            return
        for fname in sorted(os.listdir(rom_dir)):
            if fname.lower().endswith(".nes"):
                path = os.path.join(rom_dir, fname)
                display = os.path.splitext(fname)[0]
                info = {
                    "mapper": "Unknown", "mapper_short": "NES",
                    "mapper_id": -1, "prg": "?", "chr": "?",
                    "mirror": "?", "battery": False, "thumb_path": "",
                }
                try:
                    with open(path, "rb") as f:
                        hdr = Header(f.read(16))
                    m_name = MAPPER_NAMES.get(hdr.mapper_id, f"M{hdr.mapper_id}")
                    mapper_label = f"{m_name} (#{hdr.mapper_id})" if m_name != str(hdr.mapper_id) else f"Mapper {hdr.mapper_id}"
                    thumb_path = os.path.join(rom_dir, "saves", f"{display}.thumb.png")
                    info = {
                        "mapper": mapper_label,
                        "mapper_short": m_name,
                        "mapper_id": hdr.mapper_id,
                        "prg": f"{hdr.prg_rom_size // 1024} KB",
                        "chr": f"{hdr.chr_rom_size // 1024} KB" if hdr.chr_rom_size else "CHR-RAM",
                        "mirror": hdr.mirroring.title(),
                        "battery": hdr.has_battery_ram,
                        "thumb_path": thumb_path,
                    }
                except Exception:
                    pass
                self.roms.append((display, fname, path, info))

    def invalidate_thumb(self, rom_path: str):
        self._thumb_cache.pop(rom_path, None)

    def _get_thumb(self, path: str, info: dict):
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        tp = info.get("thumb_path", "")
        surf = None
        if tp and os.path.isfile(tp):
            try:
                surf = pygame.image.load(tp).convert()
            except Exception:
                surf = None
        self._thumb_cache[path] = surf
        return surf

    def open_pause(self):
        self.mode = "pause"
        self.return_mode = "pause"
        self.cursor = 0
        self.waiting_key_for = None

    def open_rom_browser(self, rom_dir: str):
        self.scan_roms(rom_dir)
        self.mode = "rom_browser"
        self.return_mode = "rom_browser"
        self.cursor = 0
        self.scroll_offset = 0

    def close(self):
        self.mode = None
        self.waiting_key_for = None

    # ─────────────────────────────────────────────────────────────────────────
    # Event handling (Keyboard + Mouse)
    # ─────────────────────────────────────────────────────────────────────────
    def handle_event(self, event, rom_dir: str):
        if event.type == pygame.MOUSEWHEEL and self.mode == "rom_browser" and self.roms:
            self.cursor = max(0, min(len(self.roms) - 1, self.cursor - event.y))
            return None

        if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN):
            mx, my = event.pos
            self._hover_zone = None
            for rect, tag, payload in self._click_zones:
                if rect.collidepoint(mx, my):
                    self._hover_zone = (tag, payload)
                    if tag == "rom_row":
                        self.cursor = payload
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                            _, _, path, _ = self.roms[payload]
                            self.close()
                            return {"action": "load_rom", "path": path}
                    elif tag == "play_selected" and self.roms:
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                            _, _, path, _ = self.roms[self.cursor]
                            self.close()
                            return {"action": "load_rom", "path": path}
                    elif tag == "open_settings":
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                            self.return_mode = self.mode
                            self.mode = "settings"
                            self.cursor = 0
                    elif tag == "pause_row":
                        self.cursor = payload
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                            items = self._pause_items()
                            if items[payload][0] == "slot":
                                delta = 1 if mx > rect.centerx else -1
                                slot = ((self.cfg["emulation"]["save_slot"] - 1 + delta) % 9) + 1
                                self.cfg["emulation"]["save_slot"] = slot
                                save_settings(self.cfg)
                                return None
                            return self._trigger_pause_item(items[payload][0], rom_dir)
                    elif tag == "settings_tab":
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                            self.settings_tab = payload
                            self.cursor = 0
                    elif tag == "settings_row":
                        self.cursor = payload
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                            items = self._settings_items()
                            delta = -1 if (event.button == 1 and mx < rect.right - 95 and items[payload][0] in ("volume", "scale", "frameskip", "fast_forward_speed", "save_slot")) else 1
                            return self._activate_setting(items[payload], delta, is_enter=True)
                    elif tag == "quit_app":
                        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                            return {"action": "quit"}
                    break
            return None

        if event.type != pygame.KEYDOWN:
            return None

        key = event.key

        if self.waiting_key_for is not None:
            if key != pygame.K_ESCAPE:
                self.cfg["controls_p1"][self.waiting_key_for] = key
                save_settings(self.cfg)
                self.show_toast(f"{self.waiting_key_for} -> {pygame.key.name(key).upper()}")
            self.waiting_key_for = None
            return None

        if self.mode == "pause":
            items = self._pause_items()
            if key in (pygame.K_ESCAPE, pygame.K_F1):
                self.close()
                return {"action": "resume"}
            elif key == pygame.K_UP:
                self.cursor = (self.cursor - 1) % len(items)
            elif key == pygame.K_DOWN:
                self.cursor = (self.cursor + 1) % len(items)
            elif key in (pygame.K_LEFT, pygame.K_RIGHT) and items[self.cursor][0] == "slot":
                delta = 1 if key == pygame.K_RIGHT else -1
                slot = ((self.cfg["emulation"]["save_slot"] - 1 + delta) % 9) + 1
                self.cfg["emulation"]["save_slot"] = slot
                save_settings(self.cfg)
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_z):
                return self._trigger_pause_item(items[self.cursor][0], rom_dir)

        elif self.mode == "settings":
            items = self._settings_items()
            if key == pygame.K_ESCAPE:
                self.mode = self.return_mode
                self.cursor = 0
            elif key == pygame.K_TAB:
                self.settings_tab = (self.settings_tab + 1) % 4
                self.cursor = 0
            elif key == pygame.K_UP:
                self.cursor = (self.cursor - 1) % len(items)
            elif key == pygame.K_DOWN:
                self.cursor = (self.cursor + 1) % len(items)
            elif key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_RETURN, pygame.K_KP_ENTER):
                delta = -1 if key == pygame.K_LEFT else 1
                return self._activate_setting(
                    items[self.cursor], delta, is_enter=(key in (pygame.K_RETURN, pygame.K_KP_ENTER))
                )

        elif self.mode == "rom_browser":
            if key == pygame.K_ESCAPE:
                return {"action": "quit"}
            elif key == pygame.K_TAB:
                self.return_mode = "rom_browser"
                self.mode = "settings"
                self.cursor = 0
            elif self.roms:
                if key == pygame.K_UP:
                    self.cursor = (self.cursor - 1) % len(self.roms)
                elif key == pygame.K_DOWN:
                    self.cursor = (self.cursor + 1) % len(self.roms)
                elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_z):
                    _, _, path, _ = self.roms[self.cursor]
                    self.close()
                    return {"action": "load_rom", "path": path}

        return None

    def _trigger_pause_item(self, tag: str, rom_dir: str):
        if tag == "resume":
            self.close()
            return {"action": "resume"}
        elif tag == "load_rom":
            self.open_rom_browser(rom_dir)
        elif tag == "save":
            self.close()
            return {"action": "save"}
        elif tag == "load":
            self.close()
            return {"action": "load"}
        elif tag == "reset":
            self.close()
            return {"action": "reset"}
        elif tag == "screenshot":
            self.close()
            return {"action": "screenshot"}
        elif tag == "settings":
            self.return_mode = "pause"
            self.mode = "settings"
            self.cursor = 0
        elif tag == "quit":
            return {"action": "quit"}
        return None

    def _pause_items(self):
        slot = self.cfg["emulation"]["save_slot"]
        return [
            ("resume",     "Resume Game"),
            ("load_rom",   "Switch Game / ROM Library"),
            ("save",       f"Save State  (Slot {slot})"),
            ("load",       f"Load State  (Slot {slot})"),
            ("slot",       f"Active Save Slot:  < {slot} >"),
            ("reset",      "Reset Console"),
            ("screenshot", "Take Screenshot  (F12)"),
            ("settings",   "Emulator Settings..."),
            ("quit",       "Quit Emulator"),
        ]

    def _settings_items(self):
        v = self.cfg["video"]
        a = self.cfg["audio"]
        e = self.cfg["emulation"]
        c = self.cfg["controls_p1"]

        if self.settings_tab == 0:
            scan_lbl = ("Off", "Light", "Classic")[v["scanlines"]]
            theme_lbl = "Retro Light" if v.get("theme", "light") == "light" else "Midnight Dark"
            return [
                ("theme",          "Color Theme",       theme_lbl),
                ("fullscreen",     "Display Mode",      "Fullscreen" if v["fullscreen"] else "Windowed"),
                ("aspect_mode",    "Aspect Ratio",      v["aspect_mode"]),
                ("scale",          "Window Scale",      f"{v['scale']}x"),
                ("smooth_scaling", "Scaling Filter",    "Smooth (Bilinear)" if v["smooth_scaling"] else "Sharp (Nearest)"),
                ("scanlines",      "CRT Scanlines",     scan_lbl),
                ("overscan_crop",  "Overscan Crop 8px", "On" if v.get("overscan_crop", False) else "Off"),
                ("show_fps",       "FPS & Slot HUD",    "On" if v["show_fps"] else "Off"),
                ("frameskip",      "Manual Frame Skip", f"{v['frameskip']}"),
            ]
        elif self.settings_tab == 1:
            return [
                ("enabled",  "Audio Enabled",    "On" if a["enabled"] else "Off"),
                ("volume",   "Master Volume",    f"{a['volume']}%"),
                ("lowpass",  "Low-Pass Filter",  "On" if a["lowpass"] else "Off"),
                ("pulse1",   "Pulse 1 Channel",  "On" if a["pulse1"] else "Off"),
                ("pulse2",   "Pulse 2 Channel",  "On" if a["pulse2"] else "Off"),
                ("triangle", "Triangle Channel", "On" if a["triangle"] else "Off"),
                ("noise",    "Noise Channel",    "On" if a["noise"] else "Off"),
            ]
        elif self.settings_tab == 2:
            return [
                ("fast_forward_speed", "Fast-Forward (Tab)",    f"{e['fast_forward_speed']}x"),
                ("save_slot",          "Default Save Slot",     f"Slot {e['save_slot']}"),
                ("auto_sram",          "Auto-Save SRAM (.sav)", "On" if e.get("auto_sram", True) else "Off"),
            ]
        else:
            rows = [(btn, btn, pygame.key.name(c[btn]).upper()) for btn in BUTTON_NAMES]
            rows.append(("reset_keys", "Reset Controls to Default", ""))
            return rows

    def _activate_setting(self, item, delta, is_enter=False):
        key_id = item[0]
        if self.settings_tab == 0:
            v = self.cfg["video"]
            if key_id == "theme":
                v["theme"] = "dark" if v.get("theme", "light") == "light" else "light"
            elif key_id == "fullscreen":
                v["fullscreen"] = not v["fullscreen"]
            elif key_id == "aspect_mode":
                modes = ["16:9", "4:3", "1:1"]
                v["aspect_mode"] = modes[(modes.index(v["aspect_mode"]) + delta) % len(modes)]
            elif key_id == "scale":
                scales = [2, 3, 4]
                v["scale"] = scales[(scales.index(v["scale"]) + delta) % len(scales)]
            elif key_id == "smooth_scaling":
                v["smooth_scaling"] = not v["smooth_scaling"]
            elif key_id == "scanlines":
                v["scanlines"] = (v["scanlines"] + delta) % 3
            elif key_id == "overscan_crop":
                v["overscan_crop"] = not v.get("overscan_crop", False)
            elif key_id == "show_fps":
                v["show_fps"] = not v["show_fps"]
            elif key_id == "frameskip":
                v["frameskip"] = (v["frameskip"] + delta) % 3
            save_settings(self.cfg)
            return {"action": "apply_video"}

        elif self.settings_tab == 1:
            a = self.cfg["audio"]
            if key_id == "volume":
                a["volume"] = max(0, min(100, a["volume"] + delta * 10))
            else:
                a[key_id] = not a[key_id]
            save_settings(self.cfg)
            return {"action": "apply_audio"}

        elif self.settings_tab == 2:
            e = self.cfg["emulation"]
            if key_id == "fast_forward_speed":
                speeds = [2, 3, 4]
                e["fast_forward_speed"] = speeds[(speeds.index(e["fast_forward_speed"]) + delta) % len(speeds)]
            elif key_id == "save_slot":
                e["save_slot"] = ((e["save_slot"] - 1 + delta) % 9) + 1
            elif key_id == "auto_sram":
                e["auto_sram"] = not e.get("auto_sram", True)
            save_settings(self.cfg)
            return None

        else:
            if key_id == "reset_keys" and is_enter:
                self.cfg["controls_p1"] = dict(DEFAULT_SETTINGS["controls_p1"])
                save_settings(self.cfg)
                self.show_toast("Controls reset to default")
                return {"action": "apply_controls"}
            elif is_enter:
                self.waiting_key_for = key_id
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Drawing Helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _draw_arrow(self, surf, x, y, color):
        pygame.draw.polygon(surf, color, [(x, y), (x, y + 12), (x + 9, y + 6)])

    def _draw_toggle_pill(self, surf, right_x, y, is_on: bool):
        t = self._theme()
        pw, ph = 52, 22
        px = right_x - pw
        bg = t["green_bg"] if is_on else t["toggle_off_bg"]
        knob_col = t["green_ok"] if is_on else t["muted"]
        pygame.draw.rect(surf, bg, (px, y + 2, pw, ph), border_radius=11)
        pygame.draw.circle(surf, knob_col, (px + (pw - 11 if is_on else 11), y + 13), 7)
        lbl = self.font_badge.render("ON" if is_on else "OFF", True, knob_col)
        tx = px + 8 if is_on else px + 22
        surf.blit(lbl, (tx, y + 6))

    def _draw_volume_bar(self, surf, right_x, y, vol: int):
        t = self._theme()
        bw, bh = 120, 10
        bx = right_x - bw - 48
        pygame.draw.rect(surf, t["track_bg"], (bx, y + 8, bw, bh), border_radius=5)
        fill_w = int(bw * (vol / 100.0))
        if fill_w > 0:
            pygame.draw.rect(surf, t["accent"], (bx, y + 8, fill_w, bh), border_radius=5)
        pygame.draw.circle(surf, t["accent_hi"], (bx + fill_w, y + 13), 7)
        lbl = self.font_bold.render(f"{vol}%", True, t["accent_hi"])
        surf.blit(lbl, (right_x - lbl.get_width(), y + 2))

    def _draw_cartridge_art(self, surf, rect: pygame.Rect, display: str, info: dict):
        """Draw a styled NES cartridge preview card when no game screenshot is cached yet."""
        t = self._theme()
        pygame.draw.rect(surf, t["art_frame"], rect, border_radius=8)
        pygame.draw.rect(surf, t["border"], rect, 1, border_radius=8)

        cw, ch = 128, 128
        cx = rect.x + (rect.w - cw) // 2
        cy = rect.y + (rect.h - ch) // 2
        # Classic gray NES cartridge silhouette
        pygame.draw.rect(surf, t["cart_shell"], (cx, cy, cw, ch), border_radius=6)
        pygame.draw.rect(surf, t["cart_groove"], (cx + 12, cy + 8, cw - 24, 14), border_radius=2)
        for g in range(4):
            pygame.draw.line(surf, t["cart_groove"], (cx + 16, cy + 28 + g * 5), (cx + cw - 16, cy + 28 + g * 5), 2)

        # Colored game label sticker
        accent = t["mapper_colors"].get(info.get("mapper_id", 0), t["accent"])
        lbl_rect = pygame.Rect(cx + 18, cy + 48, cw - 36, 72)
        pygame.draw.rect(surf, (24, 27, 38), lbl_rect, border_radius=4)
        pygame.draw.rect(surf, accent, (lbl_rect.x, lbl_rect.y, lbl_rect.w, 18), border_top_left_radius=4, border_top_right_radius=4)
        m_txt = self.font_badge.render(info.get("mapper_short", "NES"), True, (255, 255, 255))
        surf.blit(m_txt, (lbl_rect.x + (lbl_rect.w - m_txt.get_width()) // 2, lbl_rect.y + 2))

        short_name = display[:12]
        t_surf = self.font_badge.render(short_name, True, self.GOLD)
        surf.blit(t_surf, (lbl_rect.x + (lbl_rect.w - t_surf.get_width()) // 2, lbl_rect.y + 34))

    def _draw_backdrop(self, screen: pygame.Surface):
        t = self._theme()
        w, h = screen.get_size()
        if self.mode == "rom_browser":
            screen.fill(t["bg"])
            for gx in range(0, w, 48):
                pygame.draw.line(screen, t["bg_grid"], (gx, 0), (gx, h))
            for gy in range(0, h, 48):
                pygame.draw.line(screen, t["bg_grid"], (0, gy), (w, gy))
        else:
            overlay = pygame.Surface((w, h), pygame.SRCALPHA)
            overlay.fill(t["overlay"])
            screen.blit(overlay, (0, 0))

    def _draw_header(self, screen: pygame.Surface, gamepads=None):
        t = self._theme()
        w, _ = screen.get_size()
        pygame.draw.rect(screen, t["header_bg"], (0, 0, w, 62))
        pygame.draw.line(screen, t["red_nes"], (0, 60), (w, 60), 2)
        pygame.draw.line(screen, t["border"], (0, 62), (w, 62), 1)

        pygame.draw.rect(screen, t["red_nes"], (24, 18, 46, 26), border_radius=6)
        nes_lbl = self.font_badge.render("NES", True, (255, 255, 255))
        screen.blit(nes_lbl, (24 + (46 - nes_lbl.get_width()) // 2, 24))

        brand = self.font_brand.render("RETROPLAY", True, t["text"])
        screen.blit(brand, (82, 14))

        has_gp = bool(gamepads and gamepads.devices)
        gp_txt = f"Gamepad: {gamepads.devices[0]['name'][:22]}" if has_gp else "Keyboard Ready"
        gp_col = t["green_ok"] if has_gp else t["muted"]
        pill_bg = t["green_bg"] if has_gp else t["card_inner"]
        pill_w = max(160, len(gp_txt) * 8 + 26)
        px = w - pill_w - 224
        if px > 240:
            pygame.draw.rect(screen, pill_bg, (px, 17, pill_w, 28), border_radius=14)
            pygame.draw.circle(screen, gp_col, (px + 14, 31), 4)
            screen.blit(self.font_badge.render(gp_txt, True, gp_col), (px + 24, 24))

        set_hover = (self._hover_zone == ("open_settings", None))
        quit_hover = (self._hover_zone == ("quit_app", None))
        set_rect = pygame.Rect(w - 208, 16, 100, 30)
        quit_rect = pygame.Rect(w - 100, 16, 76, 30)
        pygame.draw.rect(screen, t["set_btn_hover"] if set_hover else t["set_btn_bg"], set_rect, border_radius=6)
        pygame.draw.rect(screen, t["quit_btn_hover"] if quit_hover else t["quit_btn_bg"], quit_rect, border_radius=6)
        s_lbl = self.font_badge.render("SETTINGS", True, t["set_btn_fg"])
        q_lbl = self.font_badge.render("QUIT", True, t["quit_btn_fg"])
        screen.blit(s_lbl, (set_rect.x + (100 - s_lbl.get_width()) // 2, 24))
        screen.blit(q_lbl, (quit_rect.x + (76 - q_lbl.get_width()) // 2, 24))
        self._click_zones.append((set_rect, "open_settings", None))
        self._click_zones.append((quit_rect, "quit_app", None))

    def draw(self, screen: pygame.Surface, gamepads=None):
        t = self._theme()
        self._click_zones.clear()
        self._draw_backdrop(screen)

        if self.mode == "rom_browser":
            self._draw_header(screen, gamepads)
            self._draw_rom_browser(screen)
            return

        w, h = screen.get_size()
        box_w = min(720, w - 40)
        box_h = min(536, h - 40)
        bx = (w - box_w) // 2
        by = (h - box_h) // 2

        # Subtle drop shadow behind modal
        shadow = pygame.Surface((box_w + 16, box_h + 16), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 95), (0, 0, box_w + 16, box_h + 16), border_radius=16)
        screen.blit(shadow, (bx - 8, by - 4))

        panel = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        pygame.draw.rect(panel, t["card_bg"], (0, 0, box_w, box_h), border_radius=12)
        pygame.draw.rect(panel, t["accent"], (0, 0, box_w, box_h), 2, border_radius=12)

        if self.mode == "pause":
            self._draw_pause(panel, bx, by, box_w, box_h)
        elif self.mode == "settings":
            self._draw_settings(panel, bx, by, box_w, box_h, gamepads)

        screen.blit(panel, (bx, by))

    def _draw_pause(self, surf, bx, by, w, h):
        t = self._theme()
        pygame.draw.rect(surf, t["red_nes"], (26, 22, 6, 26), border_radius=3)
        surf.blit(self.font_title.render("GAME PAUSED", True, t["text"]), (42, 21))

        y = 70
        for idx, (_, label) in enumerate(self._pause_items()):
            selected = (idx == self.cursor)
            row_rect = pygame.Rect(22, y - 3, w - 44, 34)
            if selected:
                pygame.draw.rect(surf, t["accent_soft"], row_rect, border_radius=7)
                pygame.draw.rect(surf, t["accent"], row_rect, 1, border_radius=7)
                self._draw_arrow(surf, 34, y + 7, t["sel_text"])
            color = t["sel_text"] if selected else t["text"]
            surf.blit(self.font_bold.render(label, True, color) if selected else self.font_item.render(label, True, color), (54, y + 3))
            self._click_zones.append((pygame.Rect(bx + 22, by + y - 3, w - 44, 34), "pause_row", idx))
            y += 42

        hint = self.font_small.render("Up/Down or Mouse: Navigate  |  Enter/Click: Select  |  Esc: Resume", True, t["muted"])
        surf.blit(hint, (26, h - 30))

    def _draw_settings(self, surf, bx, by, w, h, gamepads=None):
        t = self._theme()
        pygame.draw.rect(surf, t["red_nes"], (26, 20, 6, 26), border_radius=3)
        surf.blit(self.font_title.render("EMULATOR SETTINGS", True, t["text"]), (42, 19))

        tabs = ["Video", "Audio", "Emulation", "Controls"]
        tx = 26
        for idx, name in enumerate(tabs):
            active = (idx == self.settings_tab)
            bg = t["accent"] if active else t["card_inner"]
            fg = t["tab_fg"] if active else t["muted"]
            pill = pygame.Rect(tx, 58, 132, 30)
            pygame.draw.rect(surf, bg, pill, border_radius=7)
            lbl = self.font_badge.render(name.upper(), True, fg)
            surf.blit(lbl, (tx + (132 - lbl.get_width()) // 2, 66))
            self._click_zones.append((pygame.Rect(bx + tx, by + 58, 132, 30), "settings_tab", idx))
            tx += 142

        y = 104
        for idx, item in enumerate(self._settings_items()):
            selected = (idx == self.cursor)
            row_rect = pygame.Rect(22, y - 3, w - 44, 32)
            if selected:
                pygame.draw.rect(surf, t["accent_soft"], row_rect, border_radius=7)
                pygame.draw.rect(surf, t["accent"], row_rect, 1, border_radius=7)
            key_id, name, val = item[0], item[1], item[2]
            if self.waiting_key_for == key_id:
                val = "< PRESS ANY KEY >"
            c_name = t["sel_text"] if selected else t["text"]
            surf.blit(self.font_bold.render(name, True, c_name) if selected else self.font_item.render(name, True, c_name), (36, y + 2))

            right_x = w - 36
            if val in ("On", "Off"):
                self._draw_toggle_pill(surf, right_x, y, val == "On")
            elif key_id == "volume":
                self._draw_volume_bar(surf, right_x, y, self.cfg["audio"]["volume"])
            elif val:
                c_val = t["accent_hi"] if selected else t["muted"]
                v_surf = self.font_bold.render(val, True, c_val)
                surf.blit(v_surf, (right_x - v_surf.get_width(), y + 2))

            self._click_zones.append((pygame.Rect(bx + 22, by + y - 3, w - 44, 32), "settings_row", idx))
            y += 37

        if self.settings_tab == 3 and gamepads and gamepads.devices:
            gp_info = self.font_small.render(f"Active Gamepad: {gamepads.devices[0]['name'][:40]}", True, t["green_ok"])
            surf.blit(gp_info, (26, h - 54))

        hint = self.font_small.render("Tab / Click Tab: Switch  |  Left/Right/Click: Change  |  Esc: Back", True, t["muted"])
        surf.blit(hint, (26, h - 30))

    def _draw_rom_browser(self, screen: pygame.Surface):
        t = self._theme()
        w, h = screen.get_size()
        margin = 28
        top_y = 82
        content_h = h - top_y - 44

        two_col = (w >= 860)
        side_w = 320 if two_col else 0
        list_w = w - margin * 2 - (side_w + 20 if two_col else 0)

        # Left Panel: ROM Library
        list_panel = pygame.Rect(margin, top_y, list_w, content_h)
        pygame.draw.rect(screen, t["panel_bg"], list_panel, border_radius=12)
        pygame.draw.rect(screen, t["border"], list_panel, 1, border_radius=12)

        hdr_lbl = self.font_title.render("GAME LIBRARY", True, t["accent_hi"])
        screen.blit(hdr_lbl, (margin + 20, top_y + 15))
        cnt_lbl = self.font_small.render(f"{len(self.roms)} ROMs ready", True, t["muted"])
        screen.blit(cnt_lbl, (margin + list_w - 20 - cnt_lbl.get_width(), top_y + 20))

        if not self.roms:
            msg = self.font_item.render("No .nes files found in ./roms/ — add .nes ROMs and relaunch.", True, t["sel_text"])
            screen.blit(msg, (margin + 20, top_y + 68))
            return

        row_h = 56
        max_vis = max(1, (content_h - 68) // row_h)
        if self.cursor < self.scroll_offset:
            self.scroll_offset = self.cursor
        elif self.cursor >= self.scroll_offset + max_vis:
            self.scroll_offset = self.cursor - max_vis + 1

        y = top_y + 54
        for idx in range(self.scroll_offset, min(len(self.roms), self.scroll_offset + max_vis)):
            display, _, _, info = self.roms[idx]
            selected = (idx == self.cursor)
            row_rect = pygame.Rect(margin + 14, y, list_w - 28, 48)
            if selected:
                pygame.draw.rect(screen, t["accent_soft"], row_rect, border_radius=8)
                pygame.draw.rect(screen, t["accent"], row_rect, 1, border_radius=8)
                self._draw_arrow(screen, row_rect.x + 12, row_rect.y + 17, t["sel_text"])
            else:
                pygame.draw.rect(screen, t["card_inner"], row_rect, border_radius=8)

            badge_txt = info.get("mapper_short", "NES")
            badge_col = t["mapper_colors"].get(info.get("mapper_id", 0), t["accent"])
            badge_rect = pygame.Rect(row_rect.right - 78, row_rect.y + 12, 66, 24)
            pygame.draw.rect(screen, t["badge_bg"], badge_rect, border_radius=6)
            pygame.draw.rect(screen, badge_col, badge_rect, 1, border_radius=6)
            b_surf = self.font_badge.render(badge_txt, True, badge_col)
            screen.blit(b_surf, (badge_rect.x + (66 - b_surf.get_width()) // 2, badge_rect.y + 5))

            title_col = t["sel_text"] if selected else t["text"]
            max_title_w = row_rect.w - 122
            title_surf = self.font_bold.render(display, True, title_col)
            if title_surf.get_width() > max_title_w:
                title_surf = self.font_bold.render(display[:40] + "...", True, title_col)
            screen.blit(title_surf, (row_rect.x + 32, row_rect.y + 5))

            sub = f"PRG {info['prg']}  |  CHR {info['chr']}  |  {info['mirror']}"
            screen.blit(self.font_small.render(sub, True, t["muted"]), (row_rect.x + 32, row_rect.y + 26))

            self._click_zones.append((row_rect, "rom_row", idx))
            y += row_h

        # Right Panel: Cover Preview, Specs & Quick Launch
        if two_col:
            sx = margin + list_w + 20
            side_rect = pygame.Rect(sx, top_y, side_w, content_h)
            pygame.draw.rect(screen, t["panel_bg"], side_rect, border_radius=12)
            pygame.draw.rect(screen, t["border"], side_rect, 1, border_radius=12)

            display, _, path, info = self.roms[self.cursor]
            screen.blit(self.font_title.render("SELECTED GAME", True, t["accent_hi"]), (sx + 18, top_y + 15))

            # Cover preview (cached real game screenshot if played, else custom NES cartridge art)
            art_rect = pygame.Rect(sx + 18, top_y + 48, side_w - 36, 168)
            thumb = self._get_thumb(path, info)
            if thumb is not None:
                scaled_thumb = pygame.transform.smoothscale(thumb, (art_rect.w, art_rect.h))
                screen.blit(scaled_thumb, art_rect.topleft)
                pygame.draw.rect(screen, t["accent"], art_rect, 1, border_radius=8)
            else:
                self._draw_cartridge_art(screen, art_rect, display, info)

            title_y = art_rect.bottom + 12
            short_title = display if len(display) <= 28 else display[:26] + "..."
            screen.blit(self.font_bold.render(short_title, True, t["sel_text"]), (sx + 18, title_y))

            specs = [
                ("Mapper", info["mapper"]),
                ("PRG / CHR", f"{info['prg']} / {info['chr']}"),
                ("Mirroring", info["mirror"]),
                ("Battery Save", "Yes" if info["battery"] else "No"),
                ("Save Slot", f"Slot {self.cfg['emulation']['save_slot']}"),
            ]
            sy = title_y + 30
            for k, v in specs:
                if sy + 18 > top_y + content_h - 115:
                    break
                screen.blit(self.font_small.render(k, True, t["muted"]), (sx + 18, sy))
                vs = self.font_small.render(v, True, t["text"])
                screen.blit(vs, (sx + side_w - 18 - vs.get_width(), sy))
                sy += 22

            play_hover = (self._hover_zone == ("play_selected", None))
            play_rect = pygame.Rect(sx + 18, top_y + content_h - 106, side_w - 36, 44)
            pygame.draw.rect(screen, t["red_hi"] if play_hover else t["red_nes"], play_rect, border_radius=9)
            p_lbl = self.font_bold.render("PLAY GAME", True, (255, 255, 255))
            screen.blit(p_lbl, (play_rect.x + (play_rect.w - p_lbl.get_width()) // 2, play_rect.y + 11))
            self._click_zones.append((play_rect, "play_selected", None))

            ctrl_y = top_y + content_h - 52
            screen.blit(self.font_small.render("Z/J: A  X/K: B  Arrows: Move", True, t["muted"]), (sx + 18, ctrl_y))
            screen.blit(self.font_small.render("ESC/F1: Menu  |  Tab: Settings", True, t["muted"]), (sx + 18, ctrl_y + 18))

        footer = self.font_small.render("RetroPlay NES  |  Open Source MIT  |  Gamepad + Keyboard Supported  |  ESC: Quit", True, t["muted"])
        screen.blit(footer, (margin, h - 28))

    def draw_hud(self, screen: pygame.Surface, fps: float, ff_active: bool):
        slot = self.cfg["emulation"]["save_slot"]
        if self.cfg["video"]["show_fps"]:
            tag = f"FPS: {fps:4.1f}  |  Slot {slot}"
            if ff_active:
                tag += f"  |  FF {self.cfg['emulation']['fast_forward_speed']}x"
            hud = self.font_hud.render(tag, True, (180, 255, 190), (0, 0, 0))
            screen.blit(hud, (8, 8))

        if self.toast_text and time.perf_counter() < self.toast_until:
            t_surf = self.font_hud.render(f" {self.toast_text} ", True, self.GOLD, (18, 22, 36))
            screen.blit(t_surf, (8, screen.get_height() - 28))

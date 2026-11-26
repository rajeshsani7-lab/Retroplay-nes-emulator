"""Persistent emulator settings (video, audio, emulation, input)."""

import json
import os
import pygame

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "video": {
        "theme": "light",          # "light" = Retro Light, "dark" = Midnight Dark
        "fullscreen": False,
        "aspect_mode": "4:3",      # "4:3" (CRT), "1:1" (Pixel), "16:9" (Stretch)
        "scale": 3,                # 2, 3, 4
        "smooth_scaling": False,   # False = crisp nearest-neighbor, True = smooth bilinear
        "scanlines": 0,            # 0 = Off, 1 = Light, 2 = Classic
        "overscan_crop": True,     # Crop 8px top/bottom TV overscan border
        "show_fps": True,
        "frameskip": 0,            # 0, 1, 2
    },
    "audio": {
        "enabled": True,
        "volume": 80,              # 0..100
        "lowpass": True,
        "pulse1": True,
        "pulse2": True,
        "triangle": True,
        "noise": True,
    },
    "emulation": {
        "fast_forward_speed": 2,   # 2x, 3x, 4x when holding Tab
        "save_slot": 1,            # 1..9
        "auto_sram": True,         # Auto-save battery-backed .sav SRAM
    },
    "controls_p1": {
        "Right":  pygame.K_RIGHT,
        "Left":   pygame.K_LEFT,
        "Down":   pygame.K_DOWN,
        "Up":     pygame.K_UP,
        "Start":  pygame.K_RETURN,
        "Select": pygame.K_RSHIFT,
        "B":      pygame.K_x,
        "A":      pygame.K_z,
    },
}


def load_settings() -> dict:
    """Load settings from settings.json, filling missing keys from DEFAULT_SETTINGS."""
    cfg = json.loads(json.dumps(DEFAULT_SETTINGS))
    if os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for section, values in saved.items():
                if section in cfg and isinstance(values, dict):
                    cfg[section].update(values)
        except Exception:
            pass
    return cfg


def save_settings(cfg: dict) -> None:
    """Persist settings dictionary to settings.json."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

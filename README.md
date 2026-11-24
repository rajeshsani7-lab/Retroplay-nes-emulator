# 🎮 RetroPlay — NES Emulator

A fully open-source **NES (Nintendo Entertainment System) emulator** written from scratch in Python.
Play your classic NES games — Contra, Mega Man, Castlevania, Duck Tales, and more!

---

## ✨ Features

- ✅ **Full MOS 6502 CPU** — all official opcodes implemented
- ✅ **PPU (graphics)** — background tiles, sprites, scrolling, palette
- ✅ **APU (audio)** — Pulse, Triangle, and Noise channels
- ✅ **Mapper support** — Mapper 0 (NROM), 1 (MMC1), 2 (UxROM/Contra!), 3 (CNROM)
- ✅ **Two-player support** — keyboard-mapped controllers
- ✅ **Save states** — F7 to save, F8 to load
- ✅ **Debug overlay** — CPU registers, scanline counter
- ✅ **Fullscreen mode** — press F11

## 🕹️ Games Supported (by Mapper)

| Mapper | Games |
|--------|-------|
| 0 (NROM) | Donkey Kong, Excitebike, Balloon Fight, Ice Climber |
| 1 (MMC1) | Mega Man 2, Legend of Zelda, Metroid, Tetris |
| **2 (UxROM)** | **Contra ✅**, Duck Tales, Mega Man, Castlevania |
| 3 (CNROM) | Donkey Kong Jr, Popeye |

> **Note on Mario**: Super Mario Bros uses Mapper 0 (NROM). It is fully supported.

---

## 🚀 Quick Start

### 1. Install dependencies

```bash
pip install pygame numpy
```

### 2. Add your ROMs

Put your legally-owned `.nes` ROM files in the `roms/` folder:
```
nes-emulator/
  roms/
    contra.nes
    mario.nes
    megaman.nes
```

### 3. Run!

```bash
# Auto-detect ROMs in the roms/ folder
python main.py

# Load a specific ROM
python main.py roms/contra.nes

# 4× zoom (1024×960 window)
python main.py roms/contra.nes --scale 4

# Debug overlay (CPU registers visible)
python main.py roms/contra.nes --debug
```

---

## 🎮 Controls

### Player 1
| Key | NES Button |
|-----|-----------|
| ← → ↑ ↓ | D-Pad |
| Z | A |
| X | B |
| Enter | Start |
| Right Shift | Select |

### Player 2
| Key | NES Button |
|-----|-----------|
| A S D W | D-Pad |
| , (Comma) | A |
| . (Period) | B |
| 1 | Start |
| 2 | Select |

### Emulator
| Key | Action |
|-----|--------|
| ESC | Quit |
| F5 | Reset game |
| F7 | Save state |
| F8 | Load state |
| F11 | Toggle fullscreen |
| P | Pause / Resume |

---

## 🗂️ Project Structure

```
nes-emulator/
├── main.py               ← Entry point, window & game loop
├── requirements.txt
├── roms/                 ← Put your .nes files here
└── nes_core/
    ├── __init__.py
    ├── cpu.py            ← MOS 6502 CPU (all opcodes)
    ├── ppu.py            ← Picture Processing Unit (graphics)
    ├── apu.py            ← Audio Processing Unit (sound)
    ├── bus.py            ← System bus (connects everything)
    └── cartridge.py      ← ROM loader + Mappers 0,1,2,3
```

---

## 🔧 Adding More Mappers

Extending mapper support is easy! Open `nes_core/cartridge.py` and add a new class:

```python
@register_mapper(4)   # Mapper 4 = MMC3 (Super Mario Bros 3, Contra Force...)
class Mapper4:
    def __init__(self, cart): ...
    def cpu_read(self, addr): ...
    def cpu_write(self, addr, data): ...
    def ppu_read(self, addr): ...
    def ppu_write(self, addr, data): ...
    def get_mirroring(self): ...
```

---

## ⚖️ Legal Notice

This emulator is **100% open source and legal** to build, use, and distribute.

- The emulator **does not include any copyrighted game ROMs**.
- You must supply your own legally-obtained `.nes` ROM files.
- Dumping a cartridge you own is legal in most jurisdictions.

---

## 🤝 Contributing

Pull requests are welcome! Areas to improve:
- Mapper 4 (MMC3) — Super Mario Bros 3, Contra Force
- APU DMC channel (sample playback)
- Save RAM persistence (.sav files)
- Rewind feature
- Netplay (two players over the internet)

---

## 📜 License

MIT License — free to use, modify, and distribute.

---

Made with ❤️ and nostalgia

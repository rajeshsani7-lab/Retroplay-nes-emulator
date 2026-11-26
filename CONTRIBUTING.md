# Contributing to RetroPlay NES Emulator

Thank you for your interest in **RetroPlay**!

RetroPlay is open source under the **MIT License** and maintained exclusively by **Rajesh Sani ([@rajeshsani7-lab](https://github.com/rajeshsani7-lab))**.

---

## Ways to Customize & Play

### 1. Add Your Own `.nes` Games Locally
1. Place legally-obtained `.nes` files inside the local `roms/` folder.
2. Launch `python main.py` (or `play.bat` on Windows).
3. Your games appear automatically in the **Game Library**.  
   *(All `.nes` files are excluded by `.gitignore` and must never be committed.)*

### 2. Customize Look, Feel & Features on Your Own Fork
- **Custom Color Themes**: Add or customize palettes in the `THEMES` dictionary in `nes_core/ui.py` (selectable under **Settings → Video → Color Theme**).
- **Mapper Support**: Add mappers using the `@register_mapper(<id>)` decorator in `nes_core/cartridge.py`.
- **Controls & Video/Audio Defaults**: Edit `DEFAULT_SETTINGS` in `nes_core/settings.py` or configure live in the in-app **Settings** menu (`ESC` / `F1`).

---

## Submitting a Pull Request

1. **Fork** `https://github.com/rajeshsani7-lab/Retroplay-nes-emulator` to your own GitHub account.
2. Create a feature branch (`git checkout -b feat/my-theme`).
3. Run the automated test suite and verify everything passes:
   ```bash
   python -m unittest discover -s tests -v
   ```
4. Ensure `git status` does **not** include any `.nes` ROMs, `.sav`/`.state` files, `settings.json`, or `screenshots/`.
5. Open a **Pull Request** against `main`.

> **Ownership & Merge Policy:** Direct pushes to `main` are disabled. Every Pull Request is reviewed and merged solely at the discretion of repository owner **[@rajeshsani7-lab](https://github.com/rajeshsani7-lab)**.

"""
NES Picture Processing Unit (PPU)
Handles all graphics: background tiles, sprites (OAM), palettes, scrolling.

The PPU produces a 256x240 pixel frame at ~60 FPS.
"""

import numpy as np

# NES system palette — 64 colors (RGB)
NES_PALETTE = [
    (84,  84,  84), (0,   30, 116), (8,   16, 144), (48,   0, 136),
    (68,   0, 100), (92,   0,  48), (84,   4,   0), (60,  24,   0),
    (32,  42,   0), (8,   58,   0), (0,   64,   0), (0,   60,   0),
    (0,   50,  60), (0,    0,   0), (0,   0,   0),  (0,   0,   0),
    (152,150, 152), (8,   76, 196), (48,  50, 236), (92,  30, 228),
    (136,  20,176), (160,  20,100), (152, 34,  32), (120, 60,   0),
    (84,  90,   0), (40, 114,   0), (8,  124,   0), (0,  118,  40),
    (0,  102, 120), (0,    0,   0), (0,   0,   0),  (0,   0,   0),
    (236,238, 236), (76, 154, 236), (120,124, 236), (176, 98, 236),
    (228, 84, 236), (236, 88, 180), (236,106, 100), (212,136,  32),
    (160,170,   0), (116,196,   0), (76, 208,  32), (56, 204, 108),
    (56, 180, 204), (60,  60,  60), (0,   0,   0),  (0,   0,   0),
    (236,238, 236), (168,204, 236), (188,188, 236), (212,178, 236),
    (236,174, 236), (236,174, 212), (236,180, 176), (228,196, 144),
    (204,210, 120), (180,222, 120), (168,226, 144), (152,226, 180),
    (160,214, 228), (160,162, 160), (0,   0,   0),  (0,   0,   0),
]


class PPU:
    SCREEN_W = 256
    SCREEN_H = 240

    def __init__(self, bus):
        self.bus = bus

        # Internal memory
        self.vram   = bytearray(2048)   # Nametable RAM (2 KB, mirrored to 4)
        self.palette = bytearray(32)    # Palette RAM
        self.oam    = bytearray(256)    # Object Attribute Memory (64 sprites)
        self.oam_addr = 0

        # Registers (mapped at $2000-$2007)
        self.ctrl   = 0x00  # PPUCTRL
        self.mask   = 0x00  # PPUMASK
        self.status = 0x00  # PPUSTATUS
        self.scroll_x = 0
        self.scroll_y = 0

        # Internal VRAM address registers (Loopy)
        self.v = 0      # Current VRAM address
        self.t = 0      # Temporary VRAM address
        self.x = 0      # Fine X scroll
        self.w = 0      # Write latch

        # Timing
        self.scanline  = 0
        self.dot       = 0
        self.odd_frame = False

        # Frame buffer — RGB pixels
        self.frame = np.zeros((self.SCREEN_H, self.SCREEN_W, 3), dtype=np.uint8)
        self.frame_complete = False
        self.nmi_triggered  = False

        # Background shift registers
        self.bg_next_tile_id    = 0
        self.bg_next_tile_attr  = 0
        self.bg_next_tile_lsb   = 0
        self.bg_next_tile_msb   = 0
        self.bg_shifter_pattern_lo = 0
        self.bg_shifter_pattern_hi = 0
        self.bg_shifter_attrib_lo  = 0
        self.bg_shifter_attrib_hi  = 0

        # Sprite rendering
        self.sprite_scanline      = [{'y':0xFF,'id':0,'attr':0,'x':0} for _ in range(8)]
        self.sprite_count         = 0
        self.sprite_shifter_lo    = [0]*8
        self.sprite_shifter_hi    = [0]*8
        self.sprite_zero_hit_possible  = False
        self.sprite_zero_being_rendered = False

    # -------------------------------------------------------------------------
    # Register R/W (called by the CPU bus at $2000-$2007, $4014)
    # -------------------------------------------------------------------------
    def reg_read(self, reg):
        """CPU reads from PPU mapped register."""
        if reg == 2:    # PPUSTATUS
            data = (self.status & 0xE0) | (self.data_buffer & 0x1F)
            self.status &= ~0x80    # clear VBlank
            self.w = 0
            return data
        elif reg == 4:  # OAMDATA
            return self.oam[self.oam_addr]
        elif reg == 7:  # PPUDATA
            data = self.data_buffer
            self.data_buffer = self.ppu_read(self.v)
            if self.v >= 0x3F00:
                data = self.data_buffer
            self.v = (self.v + (32 if self.ctrl & 0x04 else 1)) & 0x7FFF
            return data
        return 0

    def reg_write(self, reg, data):
        """CPU writes to PPU mapped register."""
        if reg == 0:    # PPUCTRL
            self.ctrl = data
            self.t = (self.t & 0xF3FF) | ((data & 0x03) << 10)
        elif reg == 1:  # PPUMASK
            self.mask = data
        elif reg == 3:  # OAMADDR
            self.oam_addr = data
        elif reg == 4:  # OAMDATA
            self.oam[self.oam_addr] = data
            self.oam_addr = (self.oam_addr + 1) & 0xFF
        elif reg == 5:  # PPUSCROLL
            if self.w == 0:
                self.x = data & 0x07
                self.t = (self.t & 0xFFE0) | (data >> 3)
                self.w = 1
            else:
                self.t = (self.t & 0x8C1F) | ((data & 0xF8) << 2) | ((data & 0x07) << 12)
                self.w = 0
        elif reg == 6:  # PPUADDR
            if self.w == 0:
                self.t = ((data & 0x3F) << 8) | (self.t & 0x00FF)
                self.w = 1
            else:
                self.t = (self.t & 0xFF00) | data
                self.v = self.t
                self.w = 0
        elif reg == 7:  # PPUDATA
            self.ppu_write(self.v, data)
            self.v = (self.v + (32 if self.ctrl & 0x04 else 1)) & 0x7FFF

    def oam_dma(self, page_data):
        """Handle OAM DMA ($4014 write)."""
        for i in range(256):
            self.oam[i] = page_data[i]

    # -------------------------------------------------------------------------
    # Internal PPU memory R/W
    # -------------------------------------------------------------------------
    def ppu_read(self, addr):
        addr &= 0x3FFF
        if addr < 0x2000:
            return self.bus.cart.ppu_read(addr)
        elif addr < 0x3F00:
            return self.vram[self._mirror_nt(addr)]
        else:
            addr = addr & 0x1F
            if addr in (0x10, 0x14, 0x18, 0x1C):
                addr -= 0x10
            return self.palette[addr]

    def ppu_write(self, addr, data):
        addr &= 0x3FFF
        if addr < 0x2000:
            self.bus.cart.ppu_write(addr, data)
        elif addr < 0x3F00:
            self.vram[self._mirror_nt(addr)] = data
        else:
            addr = addr & 0x1F
            if addr in (0x10, 0x14, 0x18, 0x1C):
                addr -= 0x10
            self.palette[addr] = data

    def _mirror_nt(self, addr):
        """Apply nametable mirroring."""
        addr = (addr - 0x2000) & 0x0FFF
        mirror = self.bus.cart.mirroring
        if mirror == 'horizontal':
            table = (addr >> 11) & 0x01
            return table * 1024 + (addr & 0x03FF)
        elif mirror == 'vertical':
            table = (addr >> 10) & 0x01
            return table * 1024 + (addr & 0x03FF)
        else:  # four-screen / single
            return addr & 0x07FF

    def _get_color(self, palette_idx, pixel):
        idx = self.ppu_read(0x3F00 + (palette_idx << 2) + pixel) & 0x3F
        r, g, b = NES_PALETTE[idx]
        return r, g, b

    # -------------------------------------------------------------------------
    # Fast Scanline Renderer (10-30x faster than dot-by-dot simulation)
    # -------------------------------------------------------------------------
    def render_scanline(self, scanline):
        if scanline < 0 or scanline >= 240:
            return

        show_bg = bool(self.mask & 0x08)
        show_sp = bool(self.mask & 0x10)

        # Pre-resolve active 32 palette colors
        pal_rgb = [NES_PALETTE[self.palette[i] & 0x3F] for i in range(32)]
        bg_col = pal_rgb[0]

        bg_pixel_line = bytearray(256)
        bg_pal_line = bytearray(256)

        # 1. Background
        if show_bg:
            bg_table = (self.ctrl >> 4) & 1
            fine_x = self.x
            base_nt_x = self.ctrl & 1
            base_nt_y = (self.ctrl >> 1) & 1

            total_y = (base_nt_y * 240 + self.scroll_y + scanline) % 480
            nt_y = total_y // 240
            fine_y = total_y % 8
            coarse_y = (total_y % 240) // 8

            total_base_x = base_nt_x * 256 + self.scroll_x
            vram = self.vram
            mirror = self.bus.cart.mirroring
            chr_rom = self.bus.cart.chr_rom

            for tile_col in range(33):
                cur_x = total_base_x + tile_col * 8
                nt_x = (cur_x // 256) & 1
                coarse_x = (cur_x % 256) // 8

                nt_id = (nt_y << 1) | nt_x
                if mirror == 'horizontal':
                    table_idx = (nt_id >> 1) & 1
                else:  # vertical
                    table_idx = nt_id & 1
                vram_base = table_idx * 1024

                tile_id = vram[vram_base + coarse_y * 32 + coarse_x]
                attr_byte = vram[vram_base + 960 + (coarse_y // 4) * 8 + (coarse_x // 4)]
                shift = ((coarse_y & 2) << 1) | (coarse_x & 2)
                pal_idx = (attr_byte >> shift) & 3

                p_addr = (bg_table << 12) | (tile_id << 4) | fine_y
                p0 = chr_rom[p_addr] if p_addr < len(chr_rom) else 0
                p1 = chr_rom[p_addr + 8] if (p_addr + 8) < len(chr_rom) else 0

                sx_start = tile_col * 8 - fine_x
                for px in range(8):
                    sx = sx_start + px
                    if 0 <= sx < 256:
                        bit = 7 - px
                        pixel = (((p1 >> bit) & 1) << 1) | ((p0 >> bit) & 1)
                        bg_pixel_line[sx] = pixel
                        bg_pal_line[sx] = pal_idx

        # 2. Sprites & Sprite 0 hit
        sp_pixel_line = bytearray(256)
        sp_pal_line = bytearray(256)
        sp_prio_line = bytearray(256)
        sp0_line = bytearray(256)

        if show_sp:
            sprite_size = 16 if (self.ctrl & 0x20) else 8
            chr_rom = self.bus.cart.chr_rom

            # Evaluate OAM backwards (lowest index sprite wins priority)
            for i in range(63, -1, -1):
                sy = self.oam[i * 4 + 0] + 1
                diff = scanline - sy
                if 0 <= diff < sprite_size:
                    tile = self.oam[i * 4 + 1]
                    attr = self.oam[i * 4 + 2]
                    sx = self.oam[i * 4 + 3]

                    flip_v = bool(attr & 0x80)
                    flip_h = bool(attr & 0x40)
                    priority = bool(attr & 0x20)
                    pal_idx = (attr & 3) + 4

                    row = sprite_size - 1 - diff if flip_v else diff
                    if sprite_size == 8:
                        table = (self.ctrl >> 3) & 1
                        addr = (table << 12) | (tile << 4) | row
                    else:
                        table = tile & 1
                        t_id = tile & ~1
                        if row >= 8:
                            t_id += 1
                            row -= 8
                        addr = (table << 12) | (t_id << 4) | row

                    p0 = chr_rom[addr] if addr < len(chr_rom) else 0
                    p1 = chr_rom[addr + 8] if (addr + 8) < len(chr_rom) else 0

                    for px in range(8):
                        col_x = sx + (7 - px if flip_h else px)
                        if 0 <= col_x < 256:
                            bit = 7 - px
                            pixel = (((p1 >> bit) & 1) << 1) | ((p0 >> bit) & 1)
                            if pixel != 0:
                                sp_pixel_line[col_x] = pixel
                                sp_pal_line[col_x] = pal_idx
                                sp_prio_line[col_x] = 1 if priority else 0
                                if i == 0:
                                    sp0_line[col_x] = 1

        # 3. Composite line into RGB frame buffer
        frame_line = self.frame[scanline]
        for x in range(256):
            bg_p = bg_pixel_line[x]
            sp_p = sp_pixel_line[x]

            if sp0_line[x] and bg_p != 0 and x < 255:
                self.status |= 0x40  # Sprite 0 hit!

            if bg_p == 0 and sp_p == 0:
                frame_line[x] = bg_col
            elif bg_p == 0:
                frame_line[x] = pal_rgb[(sp_pal_line[x] << 2) | sp_p]
            elif sp_p == 0:
                frame_line[x] = pal_rgb[(bg_pal_line[x] << 2) | bg_p]
            else:
                if sp_prio_line[x] == 0:
                    frame_line[x] = pal_rgb[(sp_pal_line[x] << 2) | sp_p]
                else:
                    frame_line[x] = pal_rgb[(bg_pal_line[x] << 2) | bg_p]

    # -------------------------------------------------------------------------
    # Clock — called once per PPU cycle
    # -------------------------------------------------------------------------
    def clock(self):
        self.nmi_triggered = False

        rendering = bool(self.mask & 0x18)  # show BG or sprites

        # --- Visible + pre-render scanlines ---
        if self.scanline < 240 or self.scanline == 261:
            # Pre-render scanline: clear VBlank at dot 1
            if self.scanline == 261 and self.dot == 1:
                self.status &= ~0xE0  # clear VBlank, sprite0, overflow
                for i in range(8):
                    self.sprite_shifter_lo[i] = 0
                    self.sprite_shifter_hi[i] = 0

            if rendering and self.dot in range(2, 258):
                self._update_shifters()

                cycle_phase = (self.dot - 1) % 8
                if cycle_phase == 0:
                    self._load_shifters()
                    nt_addr = 0x2000 | (self.v & 0x0FFF)
                    self.bg_next_tile_id = self.ppu_read(nt_addr)
                elif cycle_phase == 2:
                    attr_addr = (0x23C0 | (self.v & 0x0C00) |
                                 ((self.v >> 4) & 0x38) | ((self.v >> 2) & 0x07))
                    attr = self.ppu_read(attr_addr)
                    if self.v & 0x0040: attr >>= 4
                    if self.v & 0x0002: attr >>= 2
                    self.bg_next_tile_attr = attr & 0x03
                elif cycle_phase == 4:
                    fine_y = (self.v >> 12) & 0x07
                    table = (self.ctrl >> 4) & 1
                    self.bg_next_tile_lsb = self.ppu_read(
                        (table << 12) | (self.bg_next_tile_id << 4) | fine_y)
                elif cycle_phase == 6:
                    fine_y = (self.v >> 12) & 0x07
                    table = (self.ctrl >> 4) & 1
                    self.bg_next_tile_msb = self.ppu_read(
                        (table << 12) | (self.bg_next_tile_id << 4) | 8 | fine_y)
                elif cycle_phase == 7:
                    self._increment_scroll_x()

            if rendering and self.dot == 256:
                self._increment_scroll_y()

            if rendering and self.dot == 257:
                self._load_shifters()
                self._transfer_address_x()

            if rendering and self.scanline == 261 and 280 <= self.dot <= 304:
                self._transfer_address_y()

            # Sprite evaluation at dot 257
            if self.dot == 257 and self.scanline >= 0:
                self._evaluate_sprites()

            # Fetch sprite patterns at dots 321-340
            if self.dot == 340:
                self._fetch_sprite_patterns()

            # Render pixel
            if self.scanline < 240 and 1 <= self.dot <= 256:
                self._render_pixel()

        # --- VBlank ---
        if self.scanline == 241 and self.dot == 1:
            self.status |= 0x80  # set VBlank
            if self.ctrl & 0x80:  # NMI enable
                self.nmi_triggered = True
            self.frame_complete = True

        # --- Advance dot / scanline ---
        self.dot += 1
        if self.dot > 340:
            self.dot = 0
            self.scanline += 1
            if self.scanline > 261:
                self.scanline = 0
                self.frame_complete = False
                self.odd_frame = not self.odd_frame

    def _render_pixel(self):
        col = self.dot - 1
        row = self.scanline

        # Background pixel
        bg_pixel   = 0
        bg_palette = 0
        if self.mask & 0x08:
            bit_mux = 0x8000 >> self.x
            p0 = 1 if (self.bg_shifter_pattern_lo & bit_mux) else 0
            p1 = 1 if (self.bg_shifter_pattern_hi & bit_mux) else 0
            bg_pixel = (p1 << 1) | p0
            a0 = 1 if (self.bg_shifter_attrib_lo & bit_mux) else 0
            a1 = 1 if (self.bg_shifter_attrib_hi & bit_mux) else 0
            bg_palette = (a1 << 1) | a0

        # Sprite pixel
        fg_pixel   = 0
        fg_palette = 0
        fg_priority = 0
        if self.mask & 0x10:
            self.sprite_zero_being_rendered = False
            for i in range(self.sprite_count):
                if self.sprite_scanline[i]['x'] == 0:
                    p0 = (self.sprite_shifter_lo[i] >> 7) & 1
                    p1 = (self.sprite_shifter_hi[i] >> 7) & 1
                    fg_pixel = (p1 << 1) | p0
                    fg_palette = (self.sprite_scanline[i]['attr'] & 0x03) + 4
                    fg_priority = 0 if (self.sprite_scanline[i]['attr'] & 0x20) else 1
                    if fg_pixel != 0:
                        if i == 0:
                            self.sprite_zero_being_rendered = True
                        break

        # Combine BG + Sprite
        pixel   = 0
        palette = 0
        if bg_pixel == 0 and fg_pixel == 0:
            pixel, palette = 0, 0
        elif bg_pixel == 0:
            pixel, palette = fg_pixel, fg_palette
        elif fg_pixel == 0:
            pixel, palette = bg_pixel, bg_palette
        else:
            if fg_priority:
                pixel, palette = fg_pixel, fg_palette
            else:
                pixel, palette = bg_pixel, bg_palette
            # Sprite 0 hit
            if self.sprite_zero_hit_possible and self.sprite_zero_being_rendered:
                if (self.mask & 0x18) == 0x18:
                    if not (self.mask & 0x02) or col >= 8:
                        if 1 <= col < 258:
                            self.status |= 0x40

        r, g, b = self._get_color(palette, pixel)
        if 0 <= row < self.SCREEN_H and 0 <= col < self.SCREEN_W:
            self.frame[row, col] = (r, g, b)

    def _evaluate_sprites(self):
        self.sprite_count = 0
        self.sprite_zero_hit_possible = False
        for i in range(8):
            self.sprite_scanline[i] = {'y': 0xFF, 'id': 0, 'attr': 0, 'x': 0}

        oam_entry = 0
        sprite_size = 16 if (self.ctrl & 0x20) else 8

        while oam_entry < 64 and self.sprite_count < 9:
            diff = self.scanline - self.oam[oam_entry * 4 + 0]
            if 0 <= diff < sprite_size:
                if self.sprite_count < 8:
                    if oam_entry == 0:
                        self.sprite_zero_hit_possible = True
                    self.sprite_scanline[self.sprite_count] = {
                        'y':    self.oam[oam_entry * 4 + 0],
                        'id':   self.oam[oam_entry * 4 + 1],
                        'attr': self.oam[oam_entry * 4 + 2],
                        'x':    self.oam[oam_entry * 4 + 3],
                    }
                    self.sprite_count += 1
            oam_entry += 1

        if self.sprite_count > 8:
            self.status |= 0x20  # sprite overflow

    def _fetch_sprite_patterns(self):
        for i in range(self.sprite_count):
            sp = self.sprite_scanline[i]
            sprite_size = 16 if (self.ctrl & 0x20) else 8
            row = self.scanline - sp['y']
            if sp['attr'] & 0x80:   # flip vertical
                row = sprite_size - 1 - row

            if sprite_size == 8:
                table = (self.ctrl >> 3) & 1
                tile  = sp['id']
            else:
                table = sp['id'] & 1
                tile  = (sp['id'] & ~1)
                if row >= 8:
                    tile += 1
                    row -= 8

            addr = (table << 12) | (tile << 4) | row
            lo = self.ppu_read(addr)
            hi = self.ppu_read(addr + 8)

            if sp['attr'] & 0x40:  # flip horizontal
                lo = self._flip_byte(lo)
                hi = self._flip_byte(hi)

            self.sprite_shifter_lo[i] = lo
            self.sprite_shifter_hi[i] = hi

    def _flip_byte(self, b):
        b = ((b & 0xF0) >> 4) | ((b & 0x0F) << 4)
        b = ((b & 0xCC) >> 2) | ((b & 0x33) << 2)
        b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1)
        return b

    def _update_shifters(self):
        if self.mask & 0x08:
            self.bg_shifter_pattern_lo = (self.bg_shifter_pattern_lo << 1) & 0xFFFF
            self.bg_shifter_pattern_hi = (self.bg_shifter_pattern_hi << 1) & 0xFFFF
            self.bg_shifter_attrib_lo  = (self.bg_shifter_attrib_lo  << 1) & 0xFFFF
            self.bg_shifter_attrib_hi  = (self.bg_shifter_attrib_hi  << 1) & 0xFFFF
        if self.mask & 0x10:
            for i in range(self.sprite_count):
                if self.sprite_scanline[i]['x'] > 0:
                    self.sprite_scanline[i]['x'] -= 1
                else:
                    self.sprite_shifter_lo[i] = (self.sprite_shifter_lo[i] << 1) & 0xFF
                    self.sprite_shifter_hi[i] = (self.sprite_shifter_hi[i] << 1) & 0xFF

    def _load_shifters(self):
        self.bg_shifter_pattern_lo = (self.bg_shifter_pattern_lo & 0xFF00) | self.bg_next_tile_lsb
        self.bg_shifter_pattern_hi = (self.bg_shifter_pattern_hi & 0xFF00) | self.bg_next_tile_msb
        self.bg_shifter_attrib_lo  = (self.bg_shifter_attrib_lo & 0xFF00) | (0xFF if (self.bg_next_tile_attr & 1) else 0)
        self.bg_shifter_attrib_hi  = (self.bg_shifter_attrib_hi & 0xFF00) | (0xFF if (self.bg_next_tile_attr & 2) else 0)

    def _increment_scroll_x(self):
        if self.v & 0x001F == 31:
            self.v &= ~0x001F
            self.v ^= 0x0400
        else:
            self.v += 1

    def _increment_scroll_y(self):
        if (self.v & 0x7000) != 0x7000:
            self.v += 0x1000
        else:
            self.v &= ~0x7000
            y = (self.v & 0x03E0) >> 5
            if y == 29:
                y = 0
                self.v ^= 0x0800
            elif y == 31:
                y = 0
            else:
                y += 1
            self.v = (self.v & ~0x03E0) | (y << 5)

    def _transfer_address_x(self):
        self.v = (self.v & ~0x041F) | (self.t & 0x041F)

    def _transfer_address_y(self):
        self.v = (self.v & ~0x7BE0) | (self.t & 0x7BE0)

    # Data buffer for PPUDATA reads
    @property
    def data_buffer(self):
        return getattr(self, '_data_buffer', 0)

    @data_buffer.setter
    def data_buffer(self, val):
        self._data_buffer = val

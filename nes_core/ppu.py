"""
NES Picture Processing Unit (PPU)
Handles all graphics: background tiles, sprites (OAM), palettes, scrolling.

The PPU produces a 256x240 pixel frame at ~60 FPS.
"""

import numpy as np

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False


if HAS_NUMBA:
    @njit(cache=True)
    def _jit_render_scanline(scanline, render, ctrl, mask, status, v, t, fine_x,
                             horiz_mirror, vram, oam, chr_rom, chr_off, idx_frame):
        rendering = mask & 0x18
        if rendering:
            v = (v & ~0x041F) | (t & 0x041F)

        if not render:
            if (status & 0x40) or not rendering:
                if rendering:
                    if (v & 0x7000) != 0x7000:
                        v += 0x1000
                    else:
                        v &= ~0x7000
                        y = (v & 0x03E0) >> 5
                        if y == 29:
                            y = 0
                            v ^= 0x0800
                        elif y == 31:
                            y = 0
                        else:
                            y += 1
                        v = (v & ~0x03E0) | (y << 5)
                return status, v
            sy = oam[0] + 1
            sp_size = 16 if (ctrl & 0x20) else 8
            if scanline < sy or scanline >= sy + sp_size:
                if (v & 0x7000) != 0x7000:
                    v += 0x1000
                else:
                    v &= ~0x7000
                    y = (v & 0x03E0) >> 5
                    if y == 29:
                        y = 0
                        v ^= 0x0800
                    elif y == 31:
                        y = 0
                    else:
                        y += 1
                    v = (v & ~0x03E0) | (y << 5)
                return status, v

        row_out = idx_frame[scanline]
        for x_i in range(256):
            row_out[x_i] = 0

        chr_len = len(chr_rom)

        # 1. Background
        if mask & 0x08:
            bg_table = (ctrl >> 4) & 1
            fine_y = (v >> 12) & 0x07
            coarse_y = (v >> 5) & 0x1F
            if coarse_y >= 30:
                coarse_y %= 30
            coarse_y_32 = coarse_y * 32
            attr_row = 960 + (coarse_y >> 2) * 8
            shift_y = (coarse_y & 2) << 1
            bg_base_addr = (bg_table << 12) | fine_y
            nt_y = (v >> 11) & 1
            nt_x = (v >> 10) & 1
            coarse_x = v & 0x1F

            sx_pos = -fine_x
            for _ in range(33):
                table_idx = nt_y if horiz_mirror else nt_x
                vram_base = table_idx * 1024
                tile_id = vram[vram_base + coarse_y_32 + coarse_x]
                attr_byte = vram[vram_base + attr_row + (coarse_x >> 2)]
                pal_idx = (attr_byte >> (shift_y | (coarse_x & 2))) & 3
                pal_base = pal_idx << 2

                p_addr = bg_base_addr | (tile_id << 4)
                c_addr = chr_off[p_addr >> 10] + (p_addr & 0x3FF)
                p0 = chr_rom[c_addr] if c_addr < chr_len else 0
                p1 = chr_rom[c_addr + 8] if (c_addr + 8) < chr_len else 0

                for bit_i in range(8):
                    px_x = sx_pos + bit_i
                    if 0 <= px_x < 256:
                        bit = 7 - bit_i
                        px = (((p1 >> bit) & 1) << 1) | ((p0 >> bit) & 1)
                        if px != 0:
                            row_out[px_x] = pal_base | px

                sx_pos += 8
                if coarse_x == 31:
                    coarse_x = 0
                    nt_x ^= 1
                else:
                    coarse_x += 1

            if not (mask & 0x02):
                for x_i in range(8):
                    row_out[x_i] = 0

        # 2. Sprites & Sprite 0 hit
        if mask & 0x10:
            sprite_size = 16 if (ctrl & 0x20) else 8
            sp_clip_left = not (mask & 0x04)
            sp_table_8 = (ctrl >> 3) & 1

            for i in range(63, -1, -1):
                base = i * 4
                sy = oam[base] + 1
                diff = scanline - sy
                if 0 <= diff < sprite_size:
                    tile = oam[base + 1]
                    attr = oam[base + 2]
                    sx = oam[base + 3]

                    flip_v = attr & 0x80
                    flip_h = attr & 0x40
                    priority = attr & 0x20
                    pal_base = ((attr & 3) + 4) << 2

                    row = sprite_size - 1 - diff if flip_v else diff
                    if sprite_size == 8:
                        addr = (sp_table_8 << 12) | (tile << 4) | row
                    else:
                        table = tile & 1
                        t_id = tile & ~1
                        if row >= 8:
                            t_id += 1
                            row -= 8
                        addr = (table << 12) | (t_id << 4) | row

                    c_addr = chr_off[addr >> 10] + (addr & 0x3FF)
                    p0 = chr_rom[c_addr] if c_addr < chr_len else 0
                    p1 = chr_rom[c_addr + 8] if (c_addr + 8) < chr_len else 0

                    for px in range(8):
                        col_x = sx + px
                        if col_x < 256:
                            if sp_clip_left and col_x < 8:
                                continue
                            bit = px if flip_h else (7 - px)
                            p_val = (((p1 >> bit) & 1) << 1) | ((p0 >> bit) & 1)
                            if p_val != 0:
                                bg_val = row_out[col_x]
                                if i == 0 and bg_val != 0 and col_x < 255:
                                    status |= 0x40
                                if render and (not priority or bg_val == 0):
                                    row_out[col_x] = pal_base | p_val

        # Increment vertical scroll
        if rendering:
            if (v & 0x7000) != 0x7000:
                v += 0x1000
            else:
                v &= ~0x7000
                y = (v & 0x03E0) >> 5
                if y == 29:
                    y = 0
                    v ^= 0x0800
                elif y == 31:
                    y = 0
                else:
                    y += 1
                v = (v & ~0x03E0) | (y << 5)

        return status, v

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

# Precomputed Lookup Tables for instantaneous scanline unpacking (built via NumPy)
def _build_luts():
    p0 = np.arange(256, dtype=np.uint8)[:, None, None]
    p1 = np.arange(256, dtype=np.uint8)[None, :, None]
    bits = np.arange(7, -1, -1, dtype=np.uint8)[None, None, :]
    px = (((p1 >> bits) & 1) << 1) | ((p0 >> bits) & 1)  # shape (256, 256, 8)
    px_flat = px.reshape(65536, 8)

    bg_lut = [None] * (4 * 65536)
    nz = px_flat != 0
    for pal in range(4):
        arr = np.where(nz, px_flat | (pal << 2), 0).astype(np.uint8)
        raw = arr.tobytes()
        base = pal << 16
        for k in range(65536):
            bg_lut[base + k] = raw[k * 8 : (k + 1) * 8]

    raw_sp = px_flat.astype(np.uint8).tobytes()
    raw_spf = px_flat[:, ::-1].astype(np.uint8).tobytes()
    sp_lut = [raw_sp[k * 8 : (k + 1) * 8] for k in range(65536)]
    sp_lut_flip = [raw_spf[k * 8 : (k + 1) * 8] for k in range(65536)]
    return bg_lut, sp_lut, sp_lut_flip

BG_LOOKUP, SPRITE_LOOKUP, SPRITE_LOOKUP_FLIP = _build_luts()


class PPU:
    SCREEN_W = 256
    SCREEN_H = 240

    def __init__(self, bus):
        self.bus = bus

        # Internal memory
        self.vram   = bytearray(2048)   # Nametable RAM (2 KB, mirrored to 4)
        self.palette = bytearray([0x0F] * 32)  # Palette RAM (default black backdrop)
        self.pal_rgb_arr = np.zeros((32, 3), dtype=np.uint8)
        self.pal_dirty = True
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
        self._idx_frame = np.zeros((self.SCREEN_H, self.SCREEN_W), dtype=np.uint8)
        self.frame_complete = False
        self.nmi_triggered  = False

        # Reusable scanline buffer (272 bytes: 8-byte left pad + 256 visible + 8-byte right pad)
        self._line_buf = bytearray(272)
        self._line_np  = np.frombuffer(self._line_buf, dtype=np.uint8)[8:264]
        self._zero_256 = b'\x00' * 256
        self._zero_8   = b'\x00' * 8
        self._oam_bins = [[] for _ in range(240)]
        self._oam_bins_key = None
        self._vram_np = np.frombuffer(self.vram, dtype=np.uint8)
        self._oam_np  = np.frombuffer(self.oam, dtype=np.uint8)
        self._chr_np  = None
        self._chr_off_np = np.arange(8, dtype=np.int32) * 1024

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
                self.scroll_x = data
                self.x = data & 0x07
                self.t = (self.t & 0xFFE0) | (data >> 3)
                self.w = 1
            else:
                self.scroll_y = data
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
            self.pal_dirty = True

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
    # Fast Scanline Renderer (Loopy-accurate + Precomputed LUT)
    # -------------------------------------------------------------------------
    def render_scanline(self, scanline, render=True):
        if scanline < 0 or scanline >= 240:
            return

        if HAS_NUMBA:
            cart = self.bus.cart
            if self._chr_np is None:
                self._chr_np = np.frombuffer(cart.chr_rom, dtype=np.uint8)
            off = cart.mapper.chr_offsets
            off_np = self._chr_off_np
            for k in range(8):
                off_np[k] = off[k]
            self.status, self.v = _jit_render_scanline(
                scanline, render, self.ctrl, self.mask, self.status, self.v, self.t, self.x,
                cart.mirroring == 'horizontal', self._vram_np, self._oam_np, self._chr_np, off_np, self._idx_frame
            )
            if render and scanline == 239:
                if self.pal_dirty:
                    self.pal_rgb_arr[:] = [NES_PALETTE[self.palette[i] & 0x3F] for i in range(32)]
                    self.pal_dirty = False
                np.take(self.pal_rgb_arr, self._idx_frame, axis=0, out=self.frame)
            return

        mask = self.mask
        rendering = mask & 0x18

        if not render:
            if rendering:
                self.v = (self.v & ~0x041F) | (self.t & 0x041F)
            # Only evaluate Sprite 0 hit if it hasn't fired yet
            if (self.status & 0x40) or not rendering:
                if rendering:
                    self._increment_scroll_y()
                return
            sy = self.oam[0] + 1
            sp_size = 16 if (self.ctrl & 0x20) else 8
            if not (0 <= scanline - sy < sp_size):
                self._increment_scroll_y()
                return

        if rendering:
            # Copy horizontal scroll bits from t -> v at start of scanline
            self.v = (self.v & ~0x041F) | (self.t & 0x041F)

        show_bg = bool(mask & 0x08)
        show_sp = bool(mask & 0x10)

        line_buf = self._line_buf
        line_buf[8:264] = self._zero_256

        # 1. Background (using Loopy v register for accurate mid-frame splits)
        if show_bg:
            v = self.v
            bg_table = (self.ctrl >> 4) & 1
            fine_x = self.x
            fine_y = (v >> 12) & 0x07
            coarse_y = (v >> 5) & 0x1F
            if coarse_y >= 30:
                coarse_y %= 30
            coarse_y_32 = coarse_y * 32
            attr_row = 960 + (coarse_y >> 2) * 8
            shift_y = (coarse_y & 2) << 1
            bg_base_addr = (bg_table << 12) | fine_y

            nt_y = (v >> 11) & 1
            nt_x = (v >> 10) & 1
            coarse_x = v & 0x1F

            vram = self.vram
            horiz_mirror = (self.bus.cart.mirroring == 'horizontal')
            cart = self.bus.cart
            chr_rom = cart.chr_rom
            chr_len = len(chr_rom)
            chr_off = cart.mapper.chr_offsets
            bg_lut = BG_LOOKUP

            sx_pos = 8 - fine_x
            for _ in range(33):
                if horiz_mirror:
                    table_idx = nt_y
                else:
                    table_idx = nt_x
                vram_base = table_idx * 1024

                tile_id = vram[vram_base + coarse_y_32 + coarse_x]
                attr_byte = vram[vram_base + attr_row + (coarse_x >> 2)]
                pal_idx = (attr_byte >> (shift_y | (coarse_x & 2))) & 3

                p_addr = bg_base_addr | (tile_id << 4)
                c_addr = chr_off[p_addr >> 10] + (p_addr & 0x3FF)
                if c_addr + 8 < chr_len:
                    p0 = chr_rom[c_addr]
                    p1 = chr_rom[c_addr + 8]
                else:
                    p0 = chr_rom[c_addr] if c_addr < chr_len else 0
                    p1 = chr_rom[c_addr + 8] if (c_addr + 8) < chr_len else 0

                line_buf[sx_pos : sx_pos + 8] = bg_lut[(pal_idx << 16) | (p0 << 8) | p1]
                sx_pos += 8

                if coarse_x == 31:
                    coarse_x = 0
                    nt_x ^= 1
                else:
                    coarse_x += 1

            # PPUMASK left 8 pixels clipping
            if not (mask & 0x02):
                line_buf[8:16] = self._zero_8

        # 2. Sprites & Sprite 0 hit (pre-binned by scanline)
        if show_sp:
            sprite_size = 16 if (self.ctrl & 0x20) else 8
            oam = self.oam
            if scanline == 0 or self._oam_bins_key != sprite_size:
                bins = self._oam_bins
                for b in bins:
                    b.clear()
                for i in range(63, -1, -1):
                    sy = oam[i * 4] + 1
                    if sy < 240:
                        end_y = min(240, sy + sprite_size)
                        for y_line in range(sy, end_y):
                            bins[y_line].append(i)
                self._oam_bins_key = sprite_size

            line_sprites = self._oam_bins[scanline]
            if line_sprites:
                cart = self.bus.cart
                chr_rom = cart.chr_rom
                chr_len = len(chr_rom)
                chr_off = cart.mapper.chr_offsets
                sp_clip_left = not (mask & 0x04)
                sp_table_8 = (self.ctrl >> 3) & 1

                for i in line_sprites:
                    base = i * 4
                    sy = oam[base] + 1
                    diff = scanline - sy
                    tile = oam[base + 1]
                    attr = oam[base + 2]
                    sx = oam[base + 3]

                    flip_v = attr & 0x80
                    flip_h = attr & 0x40
                    priority = attr & 0x20
                    pal_base = ((attr & 3) + 4) << 2

                    row = sprite_size - 1 - diff if flip_v else diff
                    if sprite_size == 8:
                        addr = (sp_table_8 << 12) | (tile << 4) | row
                    else:
                        table = tile & 1
                        t_id = tile & ~1
                        if row >= 8:
                            t_id += 1
                            row -= 8
                        addr = (table << 12) | (t_id << 4) | row

                    c_addr = chr_off[addr >> 10] + (addr & 0x3FF)
                    if c_addr + 8 < chr_len:
                        p0 = chr_rom[c_addr]
                        p1 = chr_rom[c_addr + 8]
                    else:
                        p0 = chr_rom[c_addr] if c_addr < chr_len else 0
                        p1 = chr_rom[c_addr + 8] if (c_addr + 8) < chr_len else 0

                    key = (p0 << 8) | p1
                    pixels = SPRITE_LOOKUP_FLIP[key] if flip_h else SPRITE_LOOKUP[key]

                    for px in range(8):
                        col_x = sx + px
                        if col_x < 256:
                            if sp_clip_left and col_x < 8:
                                continue
                            p_val = pixels[px]
                            if p_val != 0:
                                buf_idx = 8 + col_x
                                bg_val = line_buf[buf_idx]
                                if i == 0 and bg_val != 0 and col_x < 255:
                                    self.status |= 0x40  # Sprite 0 hit!
                                if render and (not priority or bg_val == 0):
                                    line_buf[buf_idx] = pal_base | p_val

        # Increment vertical scroll for next scanline
        if rendering:
            self._increment_scroll_y()

        # 3. Store palette indices for scanline; convert whole frame at scanline 239
        if render:
            self._idx_frame[scanline] = self._line_np
            if scanline == 239:
                if self.pal_dirty:
                    self.pal_rgb_arr[:] = [NES_PALETTE[self.palette[i] & 0x3F] for i in range(32)]
                    self.pal_dirty = False
                np.take(self.pal_rgb_arr, self._idx_frame, axis=0, out=self.frame)

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

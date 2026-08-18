"""ST7735S SPI display — raw driver for CPython on Raspberry Pi.

Uses only adafruit-blinka (board, digitalio, busio) and Pillow.
No displayio / CircuitPython shim required.

Pin mapping (from the user's wiring table):

    ST7735S       Pi Zero 2W        GPIO
    ─────────     ──────────────    ─────
    VCC           Pin 1 / 17        3.3V
    GND           Pin 6 / 9 / …     GND
    CS            Pin 24            GPIO 8   (SPI0 CE0)
    RESET         Pin 22            GPIO 25
    DC / RS       Pin 18            GPIO 24
    SCK / CLK     Pin 23            GPIO 11  (SPI0 SCLK)
    MOSI / DIN    Pin 19            GPIO 10  (SPI0 MOSI)
    LED / BLK     Pin 1 / 17        3.3V (directly wired; no GPIO needed)
"""
from __future__ import annotations

import time
import struct

import board
import busio
import digitalio
from PIL import Image, ImageDraw, ImageFont

# ── display geometry (1.8″ 128×160 module) ───────────────────────
_WIDTH = 128
_HEIGHT = 160

# ── ST7735S commands ─────────────────────────────────────────────
_NOP = 0x00
_SWRESET = 0x01
_SLPOUT = 0x11
_NORON = 0x13
_INVON = 0x21
_DISPON = 0x29
_CASET = 0x2A
_RASET = 0x2B
_RAMWR = 0x2C
_MADCTL = 0x36
_COLMOD = 0x3A

# MADCTL bits
_MADCTL_MY = 0x80
_MADCTL_MX = 0x40
_MADCTL_MV = 0x20
_MADCTL_ML = 0x10
_MADCTL_RGB = 0x00
_MADCTL_BGR = 0x08

# Rotation lookup: angle -> MADCTL flags
_ROTATION_MAP = {
    0: 0x00,
    90: _MADCTL_MX | _MADCTL_MV,
    180: _MADCTL_MX | _MADCTL_MY,
    270: _MADCTL_MY | _MADCTL_MV,
}


def _color565(r: int, g: int, b: int) -> int:
    """Pack 8-bit RGB into 16-bit RGB565."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


class ST7735S:
    """Minimal raw SPI driver for the ST7735S 128×160 TFT.

    Usage::

        display = ST7735S()
        display.update(12.3, 4.56, 7.89, 0.12)
        ...
        display.close()
    """

    def __init__(
        self,
        *,
        width: int = _WIDTH,
        height: int = _HEIGHT,
        rotation: int = 270,
        cs_pin: int = 8,
        dc_pin: int = 24,
        rst_pin: int = 25,
        bl_pin: int | None = None,
        x_offset: int = 0,
        y_offset: int = 0,
    ) -> None:
        self._width = width
        self._height = height
        self._x_offset = x_offset
        self._y_offset = y_offset

        # ── SPI bus ──────────────────────────────────────────────
        self._spi = busio.SPI(board.SCK, MOSI=board.MOSI)

        # ── GPIO lines ──────────────────────────────────────────
        self._cs = digitalio.DigitalInOut(getattr(board, f"D{cs_pin}"))
        self._cs.direction = digitalio.Direction.OUTPUT
        self._cs.value = True  # deselect

        self._dc = digitalio.DigitalInOut(getattr(board, f"D{dc_pin}"))
        self._dc.direction = digitalio.Direction.OUTPUT

        self._rst = digitalio.DigitalInOut(getattr(board, f"D{rst_pin}"))
        self._rst.direction = digitalio.Direction.OUTPUT

        self._backlight: digitalio.DigitalInOut | None = None
        if bl_pin is not None:
            self._backlight = digitalio.DigitalInOut(getattr(board, f"D{bl_pin}"))
            self._backlight.direction = digitalio.Direction.OUTPUT
            self._backlight.value = True

        # ── initialise the display ──────────────────────────────
        self._init_display(rotation)

        # ── Pillow image buffer ──────────────────────────────────
        # Effective dimensions depend on rotation
        if rotation in (90, 270):
            self._eff_w = height  # 160
            self._eff_h = width   # 128
        else:
            self._eff_w = width
            self._eff_h = height

        self._image = Image.new("RGB", (self._eff_w, self._eff_h), (0, 0, 0))
        self._draw = ImageDraw.Draw(self._image)

        # Font: use a readable monospace font
        try:
            self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 16)
        except Exception:
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            except Exception:
                self._font = ImageFont.load_default()

    # ── low-level SPI helpers ────────────────────────────────────

    def _write_cmd(self, cmd: int) -> None:
        """Send a single command byte."""
        self._dc.value = False
        self._cs.value = False
        self._spi.write(bytes([cmd]))
        self._cs.value = True

    def _write_data(self, data: bytes | bytearray) -> None:
        """Send data bytes."""
        self._dc.value = True
        self._cs.value = False
        self._spi.write(data)
        self._cs.value = True

    def _write_data_words(self, data: bytes) -> None:
        """Send raw data (e.g. framebuffer) — same as _write_data but named for clarity."""
        self._write_data(data)

    # ── display init sequence ────────────────────────────────────

    def _init_display(self, rotation: int) -> None:
        """Hardware-reset and configure the ST7735S controller."""
        # Hardware reset: LOW 10 ms → HIGH 10 ms → LOW 10 ms → HIGH 120 ms
        self._rst.value = True
        time.sleep(0.01)
        self._rst.value = False
        time.sleep(0.01)
        self._rst.value = True
        time.sleep(0.12)

        self._write_cmd(_SWRESET)
        time.sleep(0.15)

        self._write_cmd(_SLPOUT)
        time.sleep(0.25)

        # Interface pixel format: 16-bit/pixel (RGB565)
        self._write_cmd(_COLMOD)
        self._write_data(bytes([0x05]))

        # MADCTL — rotation
        madctl = _ROTATION_MAP.get(rotation, 0x00) | _MADCTL_BGR
        self._write_cmd(_MADCTL)
        self._write_data(bytes([madctl]))

        # Column address set
        self._write_cmd(_CASET)
        self._write_data(struct.pack(">HH", 0, self._width - 1))

        # Row address set
        self._write_cmd(_RASET)
        self._write_data(struct.pack(">HH", 0, self._height - 1))

        # Normal display mode on
        self._write_cmd(_NORON)
        time.sleep(0.01)

        # Display inversion on (most ST7735S modules need this)
        self._write_cmd(_INVON)

        # Display on
        self._write_cmd(_DISPON)
        time.sleep(0.1)

    # ── framebuffer push ─────────────────────────────────────────

    def _push_image(self) -> None:
        """Convert the Pillow image to RGB565 and push it over SPI."""
        # Convert to raw RGB565 bytes
        rgb_img = self._image.convert("RGB")
        raw = rgb_img.tobytes()  # 3 bytes per pixel (RGB)

        # Pack into RGB565
        buf = bytearray(len(raw) // 2)
        for i in range(0, len(raw), 2):
            # Pixel in the raw buffer is RGB, but we need to pack as RGB565
            pass

        # Faster approach: use numpy-like manual packing
        n_pixels = self._eff_w * self._eff_h
        buf = bytearray(n_pixels * 2)
        pixel_data = raw
        idx = 0
        buf_idx = 0
        while idx < len(pixel_data) - 2:
            r = pixel_data[idx]
            g = pixel_data[idx + 1]
            b = pixel_data[idx + 2]
            rgb565 = _color565(r, g, b)
            buf[buf_idx] = (rgb565 >> 8) & 0xFF
            buf[buf_idx + 1] = rgb565 & 0xFF
            idx += 3
            buf_idx += 2

        # Set the drawing window to the full display
        self._write_cmd(_CASET)
        self._write_data(struct.pack(">HH", self._x_offset, self._x_offset + self._eff_w - 1))

        self._write_cmd(_RASET)
        self._write_data(struct.pack(">HH", self._y_offset, self._y_offset + self._eff_h - 1))

        self._write_cmd(_RAMWR)
        self._write_data_words(buf)

    # ── public API ───────────────────────────────────────────────

    def update(self, t: float, voltage: float, current: float, power: float) -> None:
        """Redraw the four live readings on screen.

        Parameters
        ----------
        t : float
            Seconds since acquisition started.
        voltage : float
            Bus voltage in V.
        current : float
            Current in A.
        power : float
            Power in W.
        """
        try:
            draw = self._draw

            # Clear to black
            draw.rectangle([0, 0, self._eff_w - 1, self._eff_h - 1], fill=(0, 0, 0))

            # Header
            draw.text((4, 4), "RADWI LIVE", fill=(0, 200, 255), font=self._font)

            # Separator line
            draw.line([(4, 28), (self._eff_w - 5, 28)], fill=(100, 100, 100), width=1)

            # Data lines
            y = 38
            draw.text((4, y),      f" V   {voltage:6.3f} V", fill=(255, 255, 255), font=self._font)
            draw.text((4, y + 22), f" I   {current:6.3f} A", fill=(255, 255, 255), font=self._font)
            draw.text((4, y + 44), f" P   {power:6.3f} W",   fill=(255, 255, 255), font=self._font)
            draw.text((4, y + 66), f" T   {t:6.1f} s",       fill=(255, 255, 255), font=self._font)

            # Push to display
            self._push_image()
        except Exception:
            # Display is best-effort; never crash the acquisition
            pass

    def close(self) -> None:
        """Turn off backlight, send sleep command, and release GPIO."""
        try:
            self._write_cmd(0x10)  # Sleep in
            time.sleep(0.05)
            if self._backlight is not None:
                self._backlight.value = False
            self._cs.deinit()
            self._dc.deinit()
            self._rst.deinit()
            if self._backlight is not None:
                self._backlight.deinit()
            self._spi.deinit()
        except Exception:
            pass

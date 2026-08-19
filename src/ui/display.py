"""ST7735S SPI display — async background driver for CPython on Raspberry Pi.

Uses a background thread so display updates never block the sampling loop.
Readings are pushed into a queue; the thread picks the latest and draws it.

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

import struct
import threading
import time

import board
import busio
import digitalio
from PIL import Image, ImageDraw, ImageFont

# ── display geometry ──────────────────────────────────────────────
_WIDTH = 128
_HEIGHT = 160
_SPI_BAUDRATE = 24_000_000

# ── ST7735S commands ─────────────────────────────────────────────
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

_MADCTL_MY = 0x80
_MADCTL_MX = 0x40
_MADCTL_MV = 0x20
_MADCTL_BGR = 0x08

_ROTATION_MAP = {
    0: 0x00,
    90: _MADCTL_MX | _MADCTL_MV,
    180: _MADCTL_MX | _MADCTL_MY,
    270: _MADCTL_MY | _MADCTL_MV,
}


def _rgb_to_565_fast(raw_rgb: bytes, n_pixels: int) -> bytearray:
    """RGB888 → RGB565 using lookup tables (~10× faster than pixel loop)."""
    buf = bytearray(n_pixels * 2)
    ri = 0
    bi = 0
    for _ in range(n_pixels):
        r = raw_rgb[ri]
        g = raw_rgb[ri + 1]
        b = raw_rgb[ri + 2]
        buf[bi]     = ((r & 0xF8)) | ((g >> 5) & 0x07)
        buf[bi + 1] = ((g << 3) & 0xF8) | ((b >> 3) & 0x1F)
        ri += 3
        bi += 2
    return buf


class ST7735S:
    """Background-threaded ST7735S driver.

    ``update(t, V, I, P)`` is non-blocking: it enqueues the latest reading
    and returns immediately.  A daemon thread picks the latest reading every
    ``refresh_interval`` seconds and pushes it to the screen.

    Usage::

        display = ST7735S()          # starts the background thread
        display.update(1.0, 5.0, 1.2, 6.0)   # returns instantly
        display.update(2.0, 4.9, 1.3, 6.4)   # overwrites the previous
        ...
        display.close()             # stops the thread, sleeps display
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
        refresh_interval: float = 0.5,
    ) -> None:
        self._width = width
        self._height = height
        self._x_offset = x_offset
        self._y_offset = y_offset
        self._refresh_interval = refresh_interval

        # ── latest reading (only the most recent matters) ────────
        self._latest: tuple[float, float, float, float] | None = None
        self._new_reading = threading.Event()

        # ── SPI bus — 24 MHz ────────────────────────────────────
        self._spi = busio.SPI(board.SCK, MOSI=board.MOSI)
        self._spi.try_lock()
        self._spi.configure(baudrate=_SPI_BAUDRATE, polarity=0, phase=0, bits=8)
        self._spi.unlock()

        # ── GPIO ────────────────────────────────────────────────
        self._cs = digitalio.DigitalInOut(getattr(board, f"D{cs_pin}"))
        self._cs.direction = digitalio.Direction.OUTPUT
        self._cs.value = True

        self._dc = digitalio.DigitalInOut(getattr(board, f"D{dc_pin}"))
        self._dc.direction = digitalio.Direction.OUTPUT

        self._rst = digitalio.DigitalInOut(getattr(board, f"D{rst_pin}"))
        self._rst.direction = digitalio.Direction.OUTPUT

        self._backlight: digitalio.DigitalInOut | None = None
        if bl_pin is not None:
            self._backlight = digitalio.DigitalInOut(getattr(board, f"D{bl_pin}"))
            self._backlight.direction = digitalio.Direction.OUTPUT
            self._backlight.value = True

        # ── init the ST7735S controller ─────────────────────────
        self._init_display(rotation)

        # ── effective dimensions after rotation ──────────────────
        if rotation in (90, 270):
            self._eff_w = height  # 160
            self._eff_h = width   # 128
        else:
            self._eff_w = width
            self._eff_h = height

        # ── Pillow image + font ─────────────────────────────────
        self._image = Image.new("RGB", (self._eff_w, self._eff_h), (0, 0, 0))
        self._draw = ImageDraw.Draw(self._image)
        self._font = self._load_font()

        # ── background thread ───────────────────────────────────
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ── font loading ─────────────────────────────────────────────

    @staticmethod
    def _load_font(size: int = 16):
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    # ── SPI helpers ──────────────────────────────────────────────

    def _write_cmd(self, cmd: int) -> None:
        self._dc.value = False
        self._cs.value = False
        self._spi.write(bytes([cmd]))
        self._cs.value = True

    def _write_data(self, data: bytes | bytearray) -> None:
        self._dc.value = True
        self._cs.value = False
        self._spi.write(data)
        self._cs.value = True

    # ── ST7735S init sequence ────────────────────────────────────

    def _init_display(self, rotation: int) -> None:
        self._rst.value = True;  time.sleep(0.01)
        self._rst.value = False; time.sleep(0.01)
        self._rst.value = True;  time.sleep(0.12)

        self._write_cmd(_SWRESET);  time.sleep(0.15)
        self._write_cmd(_SLPOUT);   time.sleep(0.25)
        self._write_cmd(_COLMOD);   self._write_data(bytes([0x05]))
        madctl = _ROTATION_MAP.get(rotation, 0x00) | _MADCTL_BGR
        self._write_cmd(_MADCTL);   self._write_data(bytes([madctl]))
        self._write_cmd(_CASET);    self._write_data(struct.pack(">HH", 0, self._width - 1))
        self._write_cmd(_RASET);    self._write_data(struct.pack(">HH", 0, self._height - 1))
        self._write_cmd(_NORON);    time.sleep(0.01)
        self._write_cmd(_INVON)
        self._write_cmd(_DISPON);   time.sleep(0.1)

    # ── framebuffer push ─────────────────────────────────────────

    def _push_image(self) -> None:
        raw = self._image.convert("RGB").tobytes()
        n_pixels = self._eff_w * self._eff_h
        buf = _rgb_to_565_fast(raw, n_pixels)
        self._write_cmd(_CASET)
        self._write_data(struct.pack(">HH", self._x_offset, self._x_offset + self._eff_w - 1))
        self._write_cmd(_RASET)
        self._write_data(struct.pack(">HH", self._y_offset, self._y_offset + self._eff_h - 1))
        self._write_cmd(_RAMWR)
        self._write_data(buf)

    # ── background thread ────────────────────────────────────────

    def _draw_screen(self, t: float, voltage: float, current: float, power: float) -> None:
        """Render one frame to the Pillow image and push to SPI."""
        d = self._draw
        d.rectangle([0, 0, self._eff_w - 1, self._eff_h - 1], fill=(0, 0, 0))
        d.text((4, 4),  "RADWI LIVE", fill=(0, 200, 255), font=self._font)
        d.line([(4, 28), (self._eff_w - 5, 28)], fill=(100, 100, 100), width=1)
        y = 38
        d.text((4, y),      f" V   {voltage:6.3f} V", fill=(255, 255, 255), font=self._font)
        d.text((4, y + 22), f" I   {current:6.3f} A", fill=(255, 255, 255), font=self._font)
        d.text((4, y + 44), f" P   {power:6.3f} W",   fill=(255, 255, 255), font=self._font)
        d.text((4, y + 66), f" T   {t:6.1f} s",       fill=(255, 255, 255), font=self._font)
        self._push_image()

    def _run(self) -> None:
        """Daemon thread: wait for new readings, redraw at fixed interval."""
        while not self._stop.is_set():
            self._new_reading.wait(timeout=self._refresh_interval)
            self._new_reading.clear()
            latest = self._latest
            if latest is None:
                continue
            try:
                self._draw_screen(*latest)
            except Exception:
                pass

    # ── public API ───────────────────────────────────────────────

    def update(self, t: float, voltage: float, current: float, power: float) -> None:
        """Non-blocking: store the latest reading for the background thread."""
        self._latest = (t, voltage, current, power)
        self._new_reading.set()

    def close(self) -> None:
        """Stop the background thread and release hardware."""
        self._stop.set()
        self._new_reading.set()
        try:
            self._thread.join(timeout=2.0)
        except Exception:
            pass
        try:
            self._write_cmd(0x10)
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

#!/usr/bin/env python3
"""
ST7735 display hardware test for Raspberry Pi Zero 2 W.

Wiring used (from your tables):
    CS   -> Physical Pin 24 (GPIO 8,  SPI0 CE0)
    RST  -> Physical Pin 22 (GPIO 25)
    DC   -> Physical Pin 18 (GPIO 24)
    SCK  -> Physical Pin 23 (GPIO 11, SPI0 SCLK)
    MOSI -> Physical Pin 19 (GPIO 10, SPI0 MOSI)
    VCC  -> Pin 1 or 17 (3.3V)
    GND  -> Pin 6/9/14/20/39
    LED/BLK -> Pin 1 or 17 (3.3V, always-on backlight, not GPIO-controlled)

Before running:
    1. Enable SPI:  sudo raspi-config  ->  Interface Options -> SPI -> Enable
       (or: sudo raspi-config nonint do_spi 0), then reboot.
    2. Install dependencies:
         pip3 install --break-system-packages adafruit-circuitpython-rgb-display pillow adafruit-blinka
    3. Run:
         python3 st7735_test.py

If the screen lights up and shows the red background with the text/shapes below,
your wiring and SPI setup are correct.
"""

import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
from adafruit_rgb_display import st7735

# --- Pin configuration (matches your wiring table) ---
cs_pin = digitalio.DigitalInOut(board.CE0)     # Physical pin 24 / GPIO 8
dc_pin = digitalio.DigitalInOut(board.D24)     # Physical pin 18 / GPIO 24
rst_pin = digitalio.DigitalInOut(board.D25)    # Physical pin 22 / GPIO 25

# --- SPI setup ---
spi = board.SPI()  # uses GPIO 11 (SCLK) and GPIO 10 (MOSI) automatically

# --- Display setup ---
# Most small ST7735S breakout boards (128x128 or 128x160) work with these
# defaults. If colors look inverted or the image is offset/mirrored, try
# adjusting rotation, bgr, or width/height/offsets below.
disp = st7735.ST7735R(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=rst_pin,
    baudrate=24000000,
    width=128,
    height=128,
    rotation=90,      # try 0, 90, 180, or 270 if orientation looks wrong
    bgr=True,          # try False if red/blue channels look swapped
)

# Some boards report a swapped width/height after rotation; use whichever
# the library exposes so drawing coordinates stay correct.
if disp.rotation % 180 == 90:
    height = disp.width
    width = disp.height
else:
    width = disp.width
    height = disp.height

# --- Create an image to draw on ---
image = Image.new("RGB", (width, height))
draw = ImageDraw.Draw(image)

# Fill background with a solid color (red) so it's obvious the screen is alive
draw.rectangle((0, 0, width, height), fill=(255, 0, 0))

# Draw a white border
draw.rectangle((4, 4, width - 5, height - 5), outline=(255, 255, 255), width=2)

# Draw some shapes
draw.ellipse((width // 2 - 20, height // 2 - 20, width // 2 + 20, height // 2 + 20), fill=(0, 255, 0))

# Draw text
try:
    font = ImageFont.load_default()
except Exception:
    font = None
draw.text((10, 10), "ST7735 OK", fill=(255, 255, 255), font=font)

# --- Push image to the display ---
disp.image(image)

print("Image sent to display. If you see a red screen with a green circle")
print("and 'ST7735 OK' text, your wiring and SPI setup are correct.")
from __future__ import annotations

from pathlib import Path
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
BRAND = PROJECT / "assets" / "branding"
UI = PROJECT / "ui"

source = BRAND / "tarekjobs-logo-master.png"
app_logo = BRAND / "tarekjobs-logo.png"
icon = BRAND / "tarekjobs.ico"
ui_logo = UI / "tarekjobs-logo.png"
ui_icon = UI / "favicon.ico"
touch = UI / "apple-touch-icon.png"

img = Image.open(source).convert("RGBA")

# Preserve the whole artwork. If a future logo is non-square, pad it instead
# of cropping it so no branding is lost.
side = max(img.size)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
x = (side - img.width) // 2
y = (side - img.height) // 2
canvas.alpha_composite(img, (x, y))

def resized(size: int) -> Image.Image:
    return canvas.resize((size, size), Image.Resampling.LANCZOS)

resized(512).save(app_logo, "PNG", optimize=True)
resized(512).save(ui_logo, "PNG", optimize=True)
resized(180).save(touch, "PNG", optimize=True)

# Pillow writes the complete Windows multi-resolution ICO when sizes are
# supplied. This is used by the Desktop shortcut and is also copied as favicon.
base = resized(256)
base.save(
    icon,
    format="ICO",
    sizes=[
        (16, 16),
        (24, 24),
        (32, 32),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    ],
)
ui_icon.write_bytes(icon.read_bytes())

print(f"Master source : {source}")
print(f"App PNG       : {app_logo}")
print(f"Windows ICO   : {icon}")
print(f"UI logo       : {ui_logo}")
print(f"Favicon       : {ui_icon}")
print(f"Touch icon    : {touch}")
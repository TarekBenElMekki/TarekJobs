
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
BRANDING = ROOT / "assets" / "branding"
UI = ROOT / "ui"

candidates = [
    BRANDING / "ttea-logo-master.png",
    BRANDING / "ttea-logo.png",
    UI / "ttea-logo.png",
]
source = next((p for p in candidates if p.exists()), None)
if source is None:
    raise SystemExit("No TTEA source logo was found.")

img = Image.open(source).convert("RGBA")
bbox = img.getchannel("A").getbbox()
if bbox:
    img = img.crop(bbox)

w, h = img.size
side = max(w, h)
margin = max(12, round(side * 0.035))
canvas_side = side + margin * 2
canvas = Image.new("RGBA", (canvas_side, canvas_side), (0,0,0,0))
canvas.alpha_composite(img, ((canvas_side-w)//2, (canvas_side-h)//2))

hd = canvas.resize((1024,1024), Image.Resampling.LANCZOS)
hd = hd.filter(ImageFilter.UnsharpMask(radius=0.7, percent=115, threshold=2))

BRANDING.mkdir(parents=True, exist_ok=True)
UI.mkdir(parents=True, exist_ok=True)
hd.save(BRANDING / "ttea-logo-hd.png", optimize=True)
hd.save(UI / "ttea-logo-hd.png", optimize=True)

icon256 = canvas.resize((256,256), Image.Resampling.LANCZOS)
icon256 = ImageEnhance.Contrast(icon256).enhance(1.04)
icon256 = icon256.filter(ImageFilter.UnsharpMask(radius=0.65, percent=150, threshold=1))

icon64 = icon256.resize((64,64), Image.Resampling.LANCZOS)
icon64 = icon64.filter(ImageFilter.UnsharpMask(radius=0.45, percent=165, threshold=1))
icon64.save(UI / "ttea-icon-64.png", optimize=True)

ico_brand = BRANDING / "ttea-v0451.ico"
ico_ui = UI / "ttea-v0451.ico"
icon256.save(
    ico_brand,
    format="ICO",
    sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(128,128),(256,256)]
)
ico_ui.write_bytes(ico_brand.read_bytes())
(UI / "favicon.ico").write_bytes(ico_brand.read_bytes())
(BRANDING / "ttea.ico").write_bytes(ico_brand.read_bytes())

print(f"Logo source : {source}")
print(f"Source size : {Image.open(source).size}")
print("HD UI logo  : 1024 x 1024")
print("ICO frames  : 16,20,24,32,40,48,64,128,256")

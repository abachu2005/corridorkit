"""Generate the application's original geometric icon (no third-party assets)."""
from pathlib import Path

def make_icon(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (1024, 1024), "#101821")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((64, 64, 960, 960), radius=192, fill="#192b3b")
    draw.ellipse((250, 190, 774, 714), outline="#d8e5eb", width=28)
    draw.polygon([(260, 850), (500, 470), (550, 510)], fill="#44d9c1")
    draw.polygon([(790, 850), (550, 470), (500, 510)], fill="#67aaff")
    draw.ellipse((470, 440, 578, 548), fill="#f0bd69")
    image.save(path, format="PNG" if path.suffix.lower() == ".png" else "ICNS")


if __name__ == "__main__":
    make_icon(Path(__file__).with_name("corridor.icns"))

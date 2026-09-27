"""Render image-heavy pages -- a photo gallery, a video, a chart -- for vision tests.

The article fixture is almost all text; these are the opposite, the pages the
prose check refuses today. Photos come from the Windows wallpapers
already on this machine and are only read, at test time: nothing is downloaded
and no photo is committed. Captions say nothing about what a photo shows, so an
answer about it has to come from looking.

    python make_visual_pages.py OUT_DIR
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1600, 1000

#: Local photos, first found wins. Windows ships these; nothing is fetched.
PHOTOS = (
    r"C:\Windows\Web\Wallpaper\Theme1\img1.jpg",
    r"C:\Windows\Web\Wallpaper\Windows\img0_1366x768.jpg",
    r"C:\Windows\Web\Wallpaper\Theme2\img10.jpg",
)
#: The chart's values, readable only from the bars' heights.
CHART = {"Mon": 180, "Tue": 420, "Wed": 260, "Thu": 120}


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def photo_path() -> Path | None:
    for candidate in PHOTOS:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


def _page(tabs: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 30), tabs, font=font(17), fill="#3366cc")
    draw.line((60, 65, WIDTH - 60, 65), fill="#c8ccd1")
    return image, draw


def _fit(photo: Image.Image, box: tuple[int, int]) -> Image.Image:
    copy = photo.copy()
    copy.thumbnail(box, Image.Resampling.LANCZOS)
    return copy


def gallery(out: Path, photo: Path) -> None:
    image, draw = _page("Home    Explore    Upload    Albums                    Sign in")
    shot = _fit(Image.open(photo).convert("RGB"), (1300, 760))
    image.paste(shot, ((WIDTH - shot.width) // 2, 100))
    draw.text((150, 900), "IMG_2041.jpg  ·  4.2 MB  ·  Added yesterday", font=font(20), fill="#54595d")
    draw.text((1250, 900), "♡ 12    Share", font=font(20), fill="#54595d")
    image.save(out)


def video(out: Path, photo: Path) -> None:
    image, draw = _page("Home    Subscriptions    Library                    Search")
    frame = Image.open(photo).convert("RGB").resize((1280, 720), Image.Resampling.LANCZOS)
    image.paste(frame, (60, 90))
    draw.rectangle((60, 770, 1340, 800), fill="#202020")
    draw.rectangle((60, 780, 420, 790), fill="#e62117")
    draw.text((75, 773), "▶   3:41 / 12:08", font=font(18), fill="white")
    draw.text((60, 830), "Weekend trip — 4K", font=font(30), fill="black")
    draw.text((60, 880), "Subscribe    Share    Save", font=font(20), fill="#54595d")
    image.save(out)


def chart(out: Path) -> None:
    image, draw = _page("Dashboard    Reports    Settings")
    draw.text((100, 90), "Weekly visitors", font=font(34), fill="black")
    left, bottom, top, scale = 180, 880, 200, 1.5
    for value in range(0, 501, 100):
        y = bottom - value * scale
        draw.line((left, y, 1450, y), fill="#e3e5e8")
        draw.text((100, y - 12), str(value), font=font(20), fill="#54595d")
    for i, (day, value) in enumerate(CHART.items()):
        x = left + 60 + i * 300
        draw.rectangle((x, bottom - value * scale, x + 170, bottom), fill="#4472c4")
        draw.text((x + 60, bottom + 15), day, font=font(22), fill="black")
    image.save(out)


def make_all(out_dir: Path) -> dict[str, Path]:
    """Write the pages; returns name -> path. Photo pages need a local photo."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = {"chart": out_dir / "chart.png"}
    chart(pages["chart"])
    photo = photo_path()
    if photo is not None:
        pages["gallery"] = out_dir / "gallery.png"
        pages["video"] = out_dir / "video.png"
        gallery(pages["gallery"], photo)
        video(pages["video"], photo)
    return pages


if __name__ == "__main__":
    for name, path in make_all(Path(sys.argv[1])).items():
        print(f"wrote {name}: {path}")

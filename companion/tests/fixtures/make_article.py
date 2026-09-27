"""Render a Wikipedia-ish article to a PNG so the pipeline can be tested
deterministically, without depending on what is actually on screen."""

import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

TITLE = "Antikythera mechanism"
BODY = """
The Antikythera mechanism is an Ancient Greek hand-wound orrery, described as
the oldest known example of an analogue computer. It was used to predict
astronomical positions and eclipses decades in advance, and to track the
four-year cycle of athletic games similar to an Olympiad.

The artefact was among wreckage retrieved from a shipwreck off the coast of the
Greek island Antikythera in 1901. On 17 May 1902, it was identified as
containing a gear by archaeologist Valerios Stais. The device, housed in a
wooden case, was found as one lump, later separated into three main fragments
which are now divided into 82 separate fragments after conservation efforts.

The mechanism was a complex clockwork device composed of at least 30 meshing
bronze gears. Its remains were found as 82 separate fragments, of which only
seven contain any gears or significant inscriptions. The largest gear is
approximately 13 centimetres in diameter and originally had 223 teeth.
"""

FOOTER = "Article  Talk        Read  Edit  View history        Tools"


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def main(out: str) -> None:
    image = Image.new("RGB", (1600, 1000), "white")
    draw = ImageDraw.Draw(image)

    draw.text((60, 40), FOOTER, font=font(17), fill="#3366cc")
    draw.line((60, 75, 1540, 75), fill="#c8ccd1")

    draw.text((60, 100), TITLE, font=font(42), fill="black")
    draw.text((60, 160), "From Wikipedia, the free encyclopedia", font=font(18),
              fill="#54595d")
    draw.line((60, 195, 1540, 195), fill="#c8ccd1")

    y = 225
    body = font(21)
    for paragraph in BODY.strip().split("\n\n"):
        flat = " ".join(paragraph.split())
        for line in textwrap.wrap(flat, width=92):
            draw.text((60, y), line, font=body, fill="#202122")
            y += 32
        y += 20

    image.save(out)
    print(f"wrote {out} ({image.width}x{image.height})")


if __name__ == "__main__":
    main(sys.argv[1])

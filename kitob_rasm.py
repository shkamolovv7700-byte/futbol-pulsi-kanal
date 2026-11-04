"""
"Kitob Pulsi 📖" kanali uchun rasmlar: logo va iqtibos kartochkalari (1080x1350).
Shriftlar shriftlar/ papkasida (Lora, Oswald, Inter - OFL litsenziya).
"""

import io
import math
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = Path(__file__).resolve().parent
FONTS = BASE / "shriftlar"
ORANGE = (232, 120, 40)
CHANNEL = "@kitobpulsi_uz"
BRAND = "KITOB PULSI"

# (fon yuqori, fon past, asosiy matn, ikkinchi darajali matn, urg'u rangi)
PALETTES = [
    ((247, 240, 226), (232, 220, 196), (40, 34, 28), (110, 96, 80), (190, 92, 30)),     # krem qog'oz
    ((20, 28, 48), (8, 12, 24), (240, 234, 220), (170, 176, 196), (232, 150, 60)),      # to'q ko'k
    ((18, 46, 38), (6, 20, 16), (238, 234, 218), (160, 190, 172), (226, 170, 80)),      # to'q yashil
]


def font(name: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except (OSError, ValueError):
            pass
    return f


def clean(text: str) -> str:
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _wrap(d: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, line = [], ""
    for w in clean(text).split():
        t = f"{line} {w}".strip()
        if d.textlength(t, font=f) <= width:
            line = t
        else:
            lines.append(line)
            line = w
    return lines + ([line] if line else [])


def _background(W: int, H: int, pal) -> Image.Image:
    top, bottom = pal[0], pal[1]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    # qog'oz donadorligi (juda yengil)
    rnd = random.Random(7)
    noise = Image.new("L", (W // 4, H // 4))
    noise.putdata([rnd.randint(118, 138) for _ in range(noise.width * noise.height)])
    noise = noise.resize((W, H)).filter(ImageFilter.GaussianBlur(1.2))
    grain = Image.merge("RGB", (noise, noise, noise))
    img = Image.blend(img, Image.composite(img, grain, Image.new("L", (W, H), 235)), 0.35)
    # chetlari qoraytirilgan (vinyetka)
    vig = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vig)
    for i in range(60):
        vd.rectangle((i * 4, i * 4, W - i * 4, H - i * 4), outline=int(70 * (1 - i / 60)))
    vig = vig.filter(ImageFilter.GaussianBlur(40))
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, vig)


def quote_card(quote: str, author: str, source: str = "", style: int = 0, label: str = BRAND) -> io.BytesIO:
    """Iqtibos kartochkasi: katta qo'shtirnoq, iqtibos (kursiv), muallif va asar nomi."""
    W, H = 1080, 1350
    pal = PALETTES[style % len(PALETTES)]
    img = _background(W, H, pal)
    d = ImageDraw.Draw(img, "RGBA")
    # yuqorida kanal belgisi
    fb = font("Oswald.ttf", 30, "SemiBold")
    d.text(((W - d.textlength(label, font=fb)) / 2, 70), label, font=fb, fill=pal[4])
    d.line((W / 2 - 60, 120, W / 2 + 60, 120), fill=pal[4] + (180,), width=2)
    # katta qo'shtirnoq
    fq = font("Lora.ttf", 260, "Bold")
    d.text((90, 150), "“", font=fq, fill=pal[4] + (90,))
    # iqtibos: uzunligiga qarab shrift o'lchami tanlanadi
    n = len(quote)
    size = 64 if n < 90 else 56 if n < 160 else 48 if n < 260 else 40 if n < 380 else 34
    while True:  # uzun parchalar ham sig'ishi uchun shrift kichraytiriladi
        fqt = font("Lora-Italic.ttf", size, "Medium")
        lines = _wrap(d, quote, fqt, W - 220)
        line_h = int(size * 1.42)
        if len(lines) * line_h <= 720 or size <= 24:
            break
        size -= 2
    block = len(lines) * line_h
    y = max(330, (H - block) / 2 - 40)
    for line in lines:
        d.text(((W - d.textlength(line, font=fqt)) / 2, y), line, font=fqt, fill=pal[2])
        y += line_h
    # ajratuvchi va muallif
    y += 40
    d.line((W / 2 - 40, y, W / 2 + 40, y), fill=pal[4], width=3)
    y += 36
    fa = font("Oswald.ttf", 44, "Medium")
    a = clean(author).upper()
    d.text(((W - d.textlength(a, font=fa)) / 2, y), a, font=fa, fill=pal[2])
    if source:
        fs = font("Lora-Italic.ttf", 32)
        s = f"«{clean(source)}»"
        d.text(((W - d.textlength(s, font=fs)) / 2, y + 66), s, font=fs, fill=pal[3])
    # pastda kanal manzili
    fc = font("Inter.ttf", 26, "SemiBold")
    d.text(((W - d.textlength(CHANNEL, font=fc)) / 2, H - 90), CHANNEL, font=fc, fill=pal[3])
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.name = "kitobpulsi.png"
    buf.seek(0)
    return buf


def avatar(path: Path) -> None:
    """Logo: ochiq kitob, sahifalari ustidan puls chizig'i - Futbol Pulsi bilan bir oila."""
    S = 1024
    img = Image.new("RGB", (S, S))
    px = img.load()
    for y in range(S):
        for x in range(S):
            dd = min(1.0, math.hypot(x - S / 2, y - S / 2) / (S * 0.72))
            px[x, y] = (int(34 - 22 * dd), int(28 - 18 * dd), int(60 - 40 * dd))
    d = ImageDraw.Draw(img)
    cx, cy = S // 2, 430
    page = (246, 238, 222)
    # ikki sahifa (biroz egilgan)
    d.polygon([(cx, cy - 170), (cx - 330, cy - 210), (cx - 330, cy + 150), (cx, cy + 190)], fill=page)
    d.polygon([(cx, cy - 170), (cx + 330, cy - 210), (cx + 330, cy + 150), (cx, cy + 190)], fill=(236, 226, 206))
    d.line((cx, cy - 170, cx, cy + 190), fill=(180, 160, 130), width=6)
    for i in range(5):  # sahifadagi qatorlar
        yy = cy - 130 + i * 52
        d.line((cx - 290, yy - 6, cx - 40, yy + 4), fill=(200, 188, 166), width=8)
        d.line((cx + 40, yy + 4, cx + 290, yy - 6), fill=(196, 182, 160), width=8)
    # puls chizig'i (porlaydigan)
    pts = [(80, cy), (300, cy), (340, cy - 40), (380, cy), (410, cy), (450, cy + 70), (505, cy - 230),
           (560, cy + 120), (600, cy), (650, cy), (690, cy - 35), (730, cy), (944, cy)]
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=ORANGE + (255,), width=40, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(16))
    img.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(img)
    d.line(pts, fill=ORANGE, width=20, joint="curve")
    d.line(pts, fill=(255, 220, 160), width=7, joint="curve")
    for text, ty, size, color in (("KITOB", 700, 160, (246, 238, 222)), ("PULSI", 870, 104, ORANGE)):
        f = font("Oswald.ttf", size, "Bold")
        w = d.textlength(text, font=f)
        d.text(((S - w) / 2 + 5, ty + 5), text, font=f, fill=(0, 0, 0))
        d.text(((S - w) / 2, ty), text, font=f, fill=color)
    img.save(path)


if __name__ == "__main__":
    avatar(BASE / "dizayn" / "kitob_avatar.png")
    print("logo saqlandi")

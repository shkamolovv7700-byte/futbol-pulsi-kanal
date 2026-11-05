"""
"USMLE Pulsi 🩺" kanali uchun rasmlar: logo va high-yield fakt kartochkalari (1080x1350).
Shriftlar shriftlar/ papkasida (Lora, Oswald, Inter - OFL litsenziya). Umumiy yordamchilar - kitob_rasm.py.
"""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from kitob_rasm import _background, _wrap, clean, font

BASE = Path(__file__).resolve().parent
CHANNEL = "@usmlepulsi_uz"
BRAND = "USMLE PULSI"
TEAL = (38, 190, 170)

# (fon yuqori, fon past, asosiy matn, ikkinchi darajali matn, urg'u rangi)
PALETTES = [
    ((14, 30, 52), (6, 12, 26), (238, 242, 248), (150, 170, 196), TEAL),               # tungi ko'k
    ((244, 248, 250), (222, 232, 238), (24, 36, 52), (96, 112, 132), (12, 140, 130)),  # oq-klinik
    ((40, 16, 30), (16, 6, 14), (246, 236, 240), (196, 160, 176), (240, 110, 120)),    # to'q bordo
]


def fact_card(title: str, points: list[str], subject: str, style: int = 0, label: str = "HIGH-YIELD") -> io.BytesIO:
    """Fakt kartochkasi: yuqorida fan nomi, katta sarlavha, pastda 2-5 ta qisqa band (inglizcha)."""
    W, H = 1080, 1350
    pal = PALETTES[style % len(PALETTES)]
    img = _background(W, H, pal)
    d = ImageDraw.Draw(img, "RGBA")
    fb = font("Oswald.ttf", 30, "SemiBold")
    top = f"{BRAND}  •  {label}"
    d.text(((W - d.textlength(top, font=fb)) / 2, 70), top, font=fb, fill=pal[4])
    d.line((W / 2 - 60, 120, W / 2 + 60, 120), fill=pal[4] + (180,), width=2)
    # fan nomi - "tabletka" ichida
    fs = font("Inter.ttf", 30, "SemiBold")
    s = clean(subject).upper()
    sw = d.textlength(s, font=fs)
    d.rounded_rectangle(((W - sw) / 2 - 28, 160, (W + sw) / 2 + 28, 214), radius=27, fill=pal[4] + (40,),
                        outline=pal[4], width=2)
    d.text(((W - sw) / 2, 168), s, font=fs, fill=pal[4])

    size = 70
    while True:  # sarlavha + bandlar sig'guncha shrift kichrayadi
        ft = font("Oswald.ttf", size, "Bold")
        fp = font("Inter.ttf", int(size * 0.56), "Medium")
        tl = _wrap(d, title, ft, W - 180)
        pls = [_wrap(d, p, fp, W - 250) for p in points]
        th, ph = int(size * 1.2), int(size * 0.56 * 1.45)
        block = len(tl) * th + 50 + sum(len(p) * ph + 30 for p in pls)
        if block <= 900 or size <= 36:
            break
        size -= 4
    y = max(270, (H - block) / 2 + 20)
    for line in tl:
        d.text(((W - d.textlength(line, font=ft)) / 2, y), line, font=ft, fill=pal[2])
        y += th
    y += 45
    d.line((W / 2 - 40, y, W / 2 + 40, y), fill=pal[4], width=3)
    y += 45
    for p in pls:
        d.ellipse((110, y + ph / 2 - 8, 126, y + ph / 2 + 8), fill=pal[4])
        for line in p:
            d.text((150, y), line, font=fp, fill=pal[2])
            y += ph
        y += 30
    fc = font("Inter.ttf", 26, "SemiBold")
    d.text(((W - d.textlength(CHANNEL, font=fc)) / 2, H - 90), CHANNEL, font=fc, fill=pal[3])
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.name = "usmlepulsi.png"
    buf.seek(0)
    return buf


def avatar(path: Path) -> None:
    """Logo: qizil xoch (tibbiyot) ustidan puls chizig'i - Pulsi oilasi uslubida."""
    S = 1024
    img = Image.new("RGB", (S, S))
    px = img.load()
    for y in range(S):
        for x in range(S):
            dd = min(1.0, math.hypot(x - S / 2, y - S / 2) / (S * 0.72))
            px[x, y] = (int(16 - 10 * dd), int(40 - 26 * dd), int(70 - 46 * dd))
    d = ImageDraw.Draw(img)
    cx, cy = S // 2, 360  # Telegram logoni doira qilib kesadi - hammasi markazga yaqin
    arm, half = 220, 76
    cross = (236, 244, 250)
    d.rounded_rectangle((cx - half, cy - arm, cx + half, cy + arm), radius=40, fill=cross)
    d.rounded_rectangle((cx - arm, cy - half, cx + arm, cy + half), radius=40, fill=cross)
    pts = [(150, cy), (300, cy), (340, cy - 40), (380, cy), (410, cy), (450, cy + 70), (505, cy - 200),
           (560, cy + 110), (600, cy), (650, cy), (690, cy - 35), (730, cy), (874, cy)]
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=TEAL + (255,), width=40, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(16))
    img.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(img)
    d.line(pts, fill=TEAL, width=20, joint="curve")
    d.line(pts, fill=(200, 255, 245), width=7, joint="curve")
    for text, ty, size, color in (("USMLE", 600, 150, (236, 244, 250)), ("PULSI", 790, 90, TEAL)):
        f = font("Oswald.ttf", size, "Bold")
        w = d.textlength(text, font=f)
        d.text(((S - w) / 2 + 5, ty + 5), text, font=f, fill=(0, 0, 0))
        d.text(((S - w) / 2, ty), text, font=f, fill=color)
    img.save(path)


if __name__ == "__main__":
    avatar(BASE / "dizayn" / "usmle_avatar.png")
    print("logo saqlandi")

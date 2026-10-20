"""
Kanal postlari uchun brendlangan rasmlar (Futbol Pulsi uslubi): yangilik, gol, yakuniy hisob,
jadval, to'purarlar, natijalar va bugungi o'yinlar. Shriftlar shriftlar/ papkasida (OFL litsenziya).
"""

import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
FONT_HEAD = BASE / "shriftlar" / "Oswald.ttf"
FONT_BODY = BASE / "shriftlar" / "Inter.ttf"
CHANNEL = "@futbolpulsi_uz"
BRAND = "FUTBOL PULSI"

GREEN_TOP, GREEN_BOTTOM = (14, 74, 46), (6, 20, 14)


def configure(brand: str, channel: str, top: tuple, bottom: tuple) -> None:
    """Boshqa kanal (masalan CS2 Pulsi) o'z nomi va ranglari bilan ishlatishi uchun."""
    global BRAND, CHANNEL, GREEN_TOP, GREEN_BOTTOM
    BRAND, CHANNEL, GREEN_TOP, GREEN_BOTTOM = brand, channel, top, bottom
ORANGE = (255, 106, 32)
RED = (235, 64, 52)
WHITE, GREY, DIM = (255, 255, 255), (205, 215, 210), (140, 160, 150)
ROW_ALT = (255, 255, 255, 14)


def font(kind: str, size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT_HEAD if kind == "head" else FONT_BODY), size)
    try:
        f.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass
    return f


def clean(text: str) -> str:
    """Emoji va boshqa shriftda yo'q belgilarni olib tashlaydi (rasmda kvadrat chiqmasin)."""
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int, max_lines: int) -> list[str]:
    words, lines, line = clean(text).split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=f) <= width:
            line = test
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;:") + "…"
    return lines


def canvas(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(GREEN_TOP, GREEN_BOTTOM)))
    # maydon chiziqlari (xira)
    lines = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    ld.ellipse((w - 330, h // 2 - 230, w + 130, h // 2 + 230), outline=(255, 255, 255, 18), width=4)
    ld.line((w - 100, 0, w - 100, h), fill=(255, 255, 255, 18), width=4)
    img.paste(lines, (0, 0), lines)
    return img, ImageDraw.Draw(img, "RGBA")


def header(d: ImageDraw.ImageDraw, w: int, label: str) -> int:
    """Yuqori chiziq: to'q sariq yorliq + kanal nomi. Keyingi y ni qaytaradi."""
    d.rectangle((0, 0, w, 10), fill=ORANGE)
    f = font("head", 34)
    tw = d.textlength(label, font=f)
    d.rounded_rectangle((48, 44, 48 + tw + 40, 100), radius=10, fill=ORANGE)
    d.text((68, 46), label, font=f, fill=WHITE)
    brand = font("head", 34)
    d.text((w - 48 - d.textlength(BRAND, font=brand), 46), BRAND, font=brand, fill=WHITE)
    return 130


def footer(d: ImageDraw.ImageDraw, w: int, h: int, note: str = "") -> None:
    f = font("body", 26, "SemiBold")
    d.line((48, h - 76, w - 48, h - 76), fill=(255, 255, 255, 40), width=2)
    d.text((48, h - 58), CHANNEL, font=f, fill=ORANGE)
    if note:
        d.text((w - 48 - d.textlength(note, font=f), h - 58), note, font=f, fill=DIM)


def to_file(img: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.name = "futbolpulsi.png"
    buf.seek(0)
    return buf


# ---------- Turlari ----------

def news_card(title: str, desc: str, source: str, tag: str = "YANGILIK") -> io.BytesIO:
    W, H = 1280, 720
    img, d = canvas(W, H)
    y = header(d, W, tag)
    ft = font("head", 64)
    lines = wrap(d, title, ft, W - 96, 4)
    y += 20
    for line in lines:
        d.text((48, y), line, font=ft, fill=WHITE)
        y += 84
    if desc and y < H - 230:
        fd = font("body", 30, "Regular")
        y += 24
        for line in wrap(d, desc, fd, W - 96, (H - 120 - y) // 44):
            d.text((48, y), line, font=fd, fill=GREY)
            y += 44
    footer(d, W, H, f"Manba: {source}")
    return to_file(img)


def video_card(title: str, source: str, count: int = 0) -> io.BytesIO:
    W, H = 1280, 720
    img, d = canvas(W, H)
    header(d, W, "VIDEO" if not count else f"{count} TA VIDEO")
    # o'ynatish tugmasi
    cx, cy, r = W // 2, 290, 110
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ORANGE)
    d.polygon([(cx - 36, cy - 56), (cx - 36, cy + 56), (cx + 62, cy)], fill=WHITE)
    ft = font("head", 52)
    y = 440
    for line in wrap(d, title, ft, W - 160, 2):
        d.text(((W - d.textlength(line, font=ft)) / 2, y), line, font=ft, fill=WHITE)
        y += 68
    footer(d, W, H, source)
    return to_file(img)


def goal_card(league: str, home: str, away: str, hs, as_, minute: str, scorer: str,
              word: str = "GOOOL!", color: tuple = ORANGE) -> io.BytesIO:
    W, H = 1280, 720
    img, d = canvas(W, H)
    header(d, W, clean(league).upper())
    fg = font("head", 150)
    d.text(((W - d.textlength(word, font=fg)) / 2, 120), word, font=fg, fill=color)
    fm = font("head", 44, "Medium")
    d.text(((W - d.textlength(minute, font=fm)) / 2, 300), minute, font=fm, fill=GREY)
    score_row(d, W, 380, home, away, hs, as_)
    if scorer:
        fs = font("body", 36, "SemiBold")
        s = clean(scorer)
        d.text(((W - d.textlength(s, font=fs)) / 2, 540), s, font=fs, fill=WHITE)
    footer(d, W, H, "JONLI")
    return to_file(img)


def score_row(d, W: int, y: int, home: str, away: str, hs, as_) -> None:
    fs, ft = font("head", 110), font("head", 52, "SemiBold")
    score = f"{hs} : {as_}"
    sw = d.textlength(score, font=fs)
    d.rounded_rectangle(((W - sw) / 2 - 30, y - 6, (W + sw) / 2 + 30, y + 140), radius=18, fill=(0, 0, 0, 90))
    d.text(((W - sw) / 2, y - 4), score, font=fs, fill=WHITE)
    left_end, right_start = (W - sw) / 2 - 60, (W + sw) / 2 + 60
    h_ = fit(d, clean(home), ft, left_end - 48)
    a_ = fit(d, clean(away), ft, W - 48 - right_start)
    d.text((left_end - d.textlength(h_, font=ft), y + 36), h_, font=ft, fill=WHITE)
    d.text((right_start, y + 36), a_, font=ft, fill=WHITE)


def fit(d, text: str, f, width: float) -> str:
    while text and d.textlength(text, font=f) > width:
        text = text[:-2] + "…" if len(text) > 2 else ""
    return text


def final_card(league: str, home: str, away: str, hs, as_, scorers: list[str]) -> io.BytesIO:
    W, H = 1280, 720
    img, d = canvas(W, H)
    header(d, W, "O'YIN YAKUNLANDI")
    fl = font("head", 40, "Medium")
    d.text(((W - d.textlength(clean(league), font=fl)) / 2, 150), clean(league), font=fl, fill=GREY)
    score_row(d, W, 230, home, away, hs, as_)
    fs = font("body", 28, "Medium")
    y, col = 420, 0
    for i, s in enumerate(scorers[:10]):
        x = 48 + (i // 5) * (W // 2)
        d.text((x, y + (i % 5) * 42), clean(s), font=fs, fill=GREY)
    footer(d, W, H, league and "Yakuniy hisob")
    return to_file(img)


def table_card(title: str, subtitle: str, rows: list[tuple]) -> io.BytesIO:
    """rows: (o'rin, jamoa, o'yin, g, d, m, farq, ochko)"""
    W = 1080
    H = 300 + 78 * len(rows) + 100
    img, d = canvas(W, H)
    y = header(d, W, "JADVAL")
    d.text((48, y), clean(title), font=font("head", 56), fill=WHITE)
    d.text((48, y + 76), clean(subtitle), font=font("body", 26, "Regular"), fill=DIM)
    y += 140
    cols = [("#", 48), ("Jamoa", 110), ("O", 640), ("G", 710), ("D", 770), ("M", 830), ("+/-", 890), ("Och", 975)]
    fh = font("body", 24, "SemiBold")
    for name, x in cols:
        d.text((x, y), name, font=fh, fill=DIM)
    y += 44
    fr, fb = font("body", 32, "Medium"), font("body", 34, "Bold")
    for i, r in enumerate(rows):
        if i % 2 == 0:
            d.rectangle((32, y - 10, W - 32, y + 58), fill=ROW_ALT)
        if i < 4:
            d.rectangle((32, y - 10, 40, y + 58), fill=ORANGE if i == 0 else (60, 200, 120))
        values = [str(r[0]), fit(d, clean(r[1]), fr, 510), *map(str, r[2:7])]
        for (name, x), v in zip(cols, values):
            d.text((x, y + 4), v, font=fr, fill=WHITE if name in ("#", "Jamoa") else GREY)
        d.text((975, y + 2), str(r[7]), font=fb, fill=ORANGE)
        y += 78
    footer(d, W, H, "O: o'yin  G-D-M: g'alaba, durang, mag'lubiyat")
    return to_file(img)


def scorers_card(title: str, rows: list[tuple], note: str = "gollar soni", label: str = "TO'PURARLAR") -> io.BytesIO:
    """rows: (o'rin, nom, izoh qatori, son) - to'purarlar yoki jamoalar reytingi uchun"""
    W = 1080
    H = 250 + 92 * len(rows) + 100
    img, d = canvas(W, H)
    y = header(d, W, label)
    d.text((48, y), clean(title), font=font("head", 56), fill=WHITE)
    y += 110
    fn, ft, fg = font("body", 34, "Bold"), font("body", 24, "Regular"), font("head", 52)
    for i, (n, player, team, goals) in enumerate(rows):
        if i % 2 == 0:
            d.rectangle((32, y - 8, W - 32, y + 80), fill=ROW_ALT)
        d.text((48, y + 12), str(n), font=font("head", 44), fill=ORANGE if i < 3 else DIM)
        d.text((130, y + 2), fit(d, clean(player), fn, 760), font=fn, fill=WHITE)
        d.text((130, y + 46), fit(d, clean(team), ft, 760), font=ft, fill=DIM)
        gs = str(goals)
        d.text((W - 60 - d.textlength(gs, font=fg), y + 6), gs, font=fg, fill=ORANGE)
        y += 92
    footer(d, W, H, note)
    return to_file(img)


def two_column_card(title: str, subtitle: str, rows: list[tuple[str, str]], label: str, note: str = "") -> io.BytesIO:
    """Ko'p qatorli ro'yxat ikki ustunda: (nom, izoh). Turnir qatnashchilari uchun (32 tagacha)."""
    W = 1280
    per_col = (len(rows) + 1) // 2
    H = 250 + 76 * per_col + 110
    img, d = canvas(W, H)
    y0 = header(d, W, label)
    d.text((48, y0), clean(title), font=font("head", 52), fill=WHITE)
    d.text((48, y0 + 70), clean(subtitle), font=font("body", 26, "Regular"), fill=DIM)
    y0 += 120
    fn, fs = font("body", 28, "Bold"), font("body", 20, "Regular")
    col_w = (W - 96 - 24) // 2
    for i, (name, sub) in enumerate(rows):
        x = 48 + (i // per_col) * (col_w + 24)
        y = y0 + (i % per_col) * 76
        if (i % per_col) % 2 == 0:
            d.rectangle((x - 12, y - 6, x + col_w, y + 66), fill=ROW_ALT)
        d.text((x, y), fit(d, clean(name), fn, col_w - 20), font=fn, fill=WHITE)
        d.text((x, y + 36), fit(d, clean(sub), fs, col_w - 20), font=fs, fill=ORANGE if sub.startswith("Saralash") else DIM)
    footer(d, W, H, note)
    return to_file(img)


def matches_card(title: str, blocks: list[tuple[str, list[tuple[str, str, str]]]], note: str) -> io.BytesIO:
    """blocks: (liga, [(chap, o'rta, o'ng)]) - natijalar uchun o'rtada hisob, o'yinlar uchun vaqt."""
    W = 1080
    n_rows = sum(len(r) for _, r in blocks)
    H = 220 + 80 * len(blocks) + 64 * n_rows + 110
    img, d = canvas(W, H)
    y = header(d, W, note.upper())
    d.text((48, y), clean(title), font=font("head", 56), fill=WHITE)
    y += 100
    fl, ft, fm = font("head", 36, "SemiBold"), font("body", 30, "Medium"), font("head", 36)
    for league, rows in blocks:
        d.text((48, y + 10), clean(league).upper(), font=fl, fill=ORANGE)
        y += 70
        for i, (left, mid, right) in enumerate(rows):
            if i % 2 == 0:
                d.rectangle((32, y - 6, W - 32, y + 54), fill=ROW_ALT)
            mw = d.textlength(mid, font=fm)
            d.text(((W - mw) / 2, y), mid, font=fm, fill=WHITE)
            l = fit(d, clean(left), ft, (W - mw) / 2 - 90)
            d.text(((W - mw) / 2 - 30 - d.textlength(l, font=ft), y + 6), l, font=ft, fill=GREY)
            d.text(((W + mw) / 2 + 30, y + 6), fit(d, clean(right), ft, (W - mw) / 2 - 90), font=ft, fill=GREY)
            y += 64
        y += 10
    footer(d, W, H, "Toshkent vaqti" if note == "Bugungi o'yinlar" else "")
    return to_file(img)

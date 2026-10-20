"""
Turnir setkalari (bracket) - Liquipedia sahifasidan o'qish va rasm qilish.

Liquipedia setkasi daraxt ko'rinishida: har bir "brkts-round-body" ichida o'sha o'yin (brkts-round-center)
va unga olib keluvchi oldingi o'yinlar (brkts-round-lower) joylashgan. Shu daraxtni yurib, har bir o'yinning
raundini (ustun) va balandligini (qator) hisoblaymiz.
"""

import html
import re
from html.parser import HTMLParser

import rasm
from PIL import ImageDraw  # noqa: F401  (rasm orqali ishlatiladi)


class Node:
    def __init__(self, cls: str, attrs: dict, parent=None):
        self.cls, self.attrs, self.parent, self.children, self.text = cls, attrs, parent, [], []


class _Tree(HTMLParser):
    VOID = {"img", "br", "hr", "input", "meta", "link", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        a = dict(attrs)
        node = Node(a.get("class", "") or "", a, self.cur)
        node.tag = tag
        self.cur.children.append(node)
        self.cur = node

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and getattr(n, "tag", None) != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.text.append(data)


def _find(node: Node, cls: str, stop: str | None = None) -> list[Node]:
    """Pastdagi tugunlardan klassida `cls` bo'lganlarini topadi (`stop` klassli tugun ichiga kirmaydi)."""
    out = []
    for ch in node.children:
        classes = ch.cls.split()
        if cls in classes:
            out.append(ch)
        elif not (stop and stop in classes):
            out.extend(_find(ch, cls, stop))
    return out


def _text(node: Node) -> str:
    return "".join(node.text + [_text(c) for c in node.children]).strip()


def _match(center: Node) -> dict:
    opps = []
    for entry in _find(center, "brkts-opponent-entry"):
        short = _find(entry, "team-bracketname") or _find(entry, "team-shortname")
        name = _text(short[0]) if short else entry.attrs.get("aria-label", "")
        score = _find(entry, "brkts-opponent-score-inner")
        win = bool(_find(entry, "brkts-opponent-win"))
        opps.append({"team": html.unescape(name) or "TBD", "score": _text(score[0]) if score else "", "win": win})
    while len(opps) < 2:
        opps.append({"team": "TBD", "score": "", "win": False})
    return {"a": opps[0], "b": opps[1]}


def _walk(body: Node, out: list) -> tuple[int, float]:
    """round-body tugunini yuradi: (raund indeksi, y-holati) qaytaradi va o'yinni `out` ga qo'shadi."""
    lowers = [b for low in _find(body, "brkts-round-lower", stop="brkts-round-body")
              for b in _find(low, "brkts-round-body", stop="brkts-round-body")]
    kids = [_walk(b, out) for b in lowers]
    centers = _find(body, "brkts-round-center", stop="brkts-round-body")
    rnd = max((k[0] for k in kids), default=-1) + 1
    y = sum(k[1] for k in kids) / len(kids) if kids else float(sum(1 for m in out if not m["has_kids"]))
    if centers:
        m = _match(centers[0])
        m.update(round=rnd, y=y, has_kids=bool(kids), kids_y=[k[1] for k in kids])
        out.append(m)
    return rnd, y


def brackets(page_html: str) -> list[dict]:
    """Sahifadagi barcha setkalar: [{nom, raundlar, o'yinlar}]."""
    result = []
    parts = page_html.split('class="brkts-bracket-wrapper"')
    for i, part in enumerate(parts[1:], 1):
        # setka nomi - undan oldingi eng yaqin sarlavha (Upper Bracket, Playoffs, Group A ...)
        heads = re.findall(r"<h[2-4][^>]*>(.*?)</h[2-4]>", parts[i - 1], re.S)
        title = re.sub(r"<[^>]+>", "", heads[-1]).strip() if heads else "Setka"
        end = part.find('class="brkts-bracket-wrapper"')
        tree = _Tree()
        tree.feed('<div class="brkts-bracket-wrapper"' + (part if end < 0 else part[:end]))
        bracket = _find(tree.root, "brkts-bracket")
        if not bracket:
            continue
        header = _find(bracket[0], "brkts-round-header")
        rounds = [(_text(h).split("\n")[0] if h.text else (h.text or [""])[0]).strip() for h in
                  (_find(header[0], "brkts-header") if header else [])]
        rounds = [re.sub(r"(Semifinals|Grand Final|Quarterfinals|Round of \d+).*", r"\1", r) for r in rounds]
        matches: list[dict] = []
        for body in _find(bracket[0], "brkts-round-body", stop="brkts-round-body"):
            _walk(body, matches)
        if matches:
            result.append({"title": html.unescape(title), "rounds": rounds, "matches": matches})
    return result


def groups(page_html: str) -> list[dict]:
    """Guruh bosqichi (GSL: ochilish o'yinlari -> g'oliblar / quyi setka -> hal qiluvchi o'yin) ro'yxatlari."""
    result = []
    for block in page_html.split("brkts-matchlist brkts-matchlist-attached")[1:]:
        title = re.search(r'brkts-matchlist-title[^>]*>(.*?)<div', block, re.S)
        title = re.sub(r"<[^>]+>|\s+Matches", "", title.group(1)).strip() if title else "Guruh"
        sections, current = [], None
        for tok in re.finditer(r'brkts-matchlist-header"[^>]*>(.*?)</div>|brkts-matchlist-match ', block, re.S):
            if tok.group(1) is not None:
                current = (re.sub(r"<[^>]+>", "", tok.group(1)).strip(), [])
                sections.append(current)
                continue
            seg = block[tok.end(): tok.end() + 4000].split("brkts-matchlist-match ")[0]
            # jamoa hali aniq bo'lmasa aria-label bo'lmaydi -> "TBD"
            teams = [t or "TBD" for t in re.findall(r'brkts-matchlist-opponent[^"]*"(?: aria-label="([^"]*)")?', seg)]
            scores = [re.sub(r"<[^>]+>", "", s).strip() for s in re.findall(
                r'brkts-matchlist-score[^"]*"[^>]*><div class="brkts-matchlist-cell-content">(.*?)</div>', seg, re.S)]
            if len(teams) >= 2 and current is not None:
                current[1].append((html.unescape(teams[0]), (scores + ["", ""])[0], (scores + ["", ""])[1],
                                   html.unescape(teams[1])))
        if sections:
            result.append({"title": html.unescape(title), "sections": sections})
    return result


def group_card(tournament: str, g: dict):
    blocks = [(uz(name), [(t1, f"{s1} : {s2}" if s1 or s2 else "vs", t2) for t1, s1, s2, t2 in rows])
              for name, rows in g["sections"] if rows]
    return rasm.matches_card(f"{tournament} — {uz(g['title'])}", blocks, "Guruh bosqichi")


ROUND_UZ = {"Opening Matches": "Ochilish o'yinlari", "Winners' Match": "G'oliblar o'yini (yuqori setka)",
            "Winners Match": "G'oliblar o'yini (yuqori setka)", "Elimination Match": "Quyi setka o'yini",
            "Decider Match": "Hal qiluvchi o'yin", "Group": "Guruh","Grand Final": "Final", "Semifinals": "Yarim final", "Quarterfinals": "Chorak final",
            "Upper Bracket": "Yuqori setka", "Lower Bracket": "Quyi setka", "Playoffs": "Pley-off"}


def uz(text: str) -> str:
    for en, uz_ in ROUND_UZ.items():
        text = text.replace(en, uz_)
    return re.sub(r"Round of (\d+)", r"1/\1", text)


def card(tournament: str, b: dict):
    """Setka rasmi: raundlar ustun bo'lib, o'yinlar bir-biriga chiziq bilan ulanadi."""
    matches = b["matches"]
    n_rounds = max(m["round"] for m in matches) + 1
    leaves = max(1, sum(1 for m in matches if not m["has_kids"]))
    W = max(1280, 80 + n_rounds * 340)
    box_w, box_h, row_h = 280, 92, 120
    top = 250
    H = top + int(leaves * row_h) + 110
    img, d = rasm.canvas(W, H)
    rasm.header(d, W, uz(b["title"]).upper()[:40])
    d.text((48, 130), rasm.clean(tournament), font=rasm.font("head", 50), fill=rasm.WHITE)
    fr, ft, fs = rasm.font("head", 28, "SemiBold"), rasm.font("body", 24, "SemiBold"), rasm.font("head", 30)
    col_x = lambda r: 48 + r * ((W - 96 - box_w) / max(1, n_rounds - 1)) if n_rounds > 1 else (W - box_w) / 2
    ypos = lambda y: top + y * row_h + (row_h - box_h) / 2
    for r in range(n_rounds):
        name = uz(b["rounds"][r]) if r < len(b["rounds"]) and b["rounds"][r] else f"{r + 1}-raund"
        d.text((col_x(r), 206), name.upper(), font=fr, fill=rasm.ORANGE)
    for m in matches:  # ulovchi chiziqlar
        x, y = col_x(m["round"]), ypos(m["y"]) + box_h / 2
        for ky in m["kids_y"]:
            px, py = col_x(m["round"] - 1) + box_w, ypos(ky) + box_h / 2
            mid = (px + x) / 2
            d.line([(px, py), (mid, py), (mid, y), (x, y)], fill=(255, 255, 255, 70), width=3)
    for m in matches:
        x, y = col_x(m["round"]), ypos(m["y"])
        d.rounded_rectangle((x, y, x + box_w, y + box_h), radius=10, fill=(0, 0, 0, 120), outline=(255, 255, 255, 40))
        for i, o in enumerate((m["a"], m["b"])):
            oy = y + 8 + i * 42
            color = rasm.ORANGE if o["win"] else (rasm.WHITE if o["team"] != "TBD" else rasm.DIM)
            d.text((x + 14, oy + 4), rasm.fit(d, rasm.clean(o["team"]), ft, box_w - 70), font=ft, fill=color)
            if o["score"]:
                d.text((x + box_w - 14 - d.textlength(o["score"], font=fs), oy), o["score"], font=fs, fill=color)
    rasm.footer(d, W, H, "Manba: Liquipedia")
    return rasm.to_file(img)

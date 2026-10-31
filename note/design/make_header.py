"""Note 見出し画像（1280x670）とアカウントアイコンを作るスクリプト。

使い方:
  python make_header.py                      # 今日の日付で3案すべて + アイコンを作る
  python make_header.py --date 2026-11-01    # 日付を指定
  python make_header.py --style shinbun --date 2026-11-01 --sub "本日の注目 12レース"
  python make_header.py --icons              # アイコンだけ作る

必要なもの: Pillow（pip install pillow）と、同じフォルダの fonts/ にあるフォント。
"""
import argparse
import datetime as dt
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
OUT = HERE / "out"

W, H = 1280, 670
S = 2  # 2倍で描いて縮小（線をなめらかにする）

# ボートレースの枠の色（1白 2黒 3赤 4青 5黄 6緑）
WAKU = ["#f4f1ea", "#1f1f1f", "#d23b32", "#2f62b0", "#e9c53a", "#3a8f4f"]
WAKU_TEXT = ["#1f1f1f", "#f4f1ea", "#f4f1ea", "#f4f1ea", "#1f1f1f", "#f4f1ea"]
YOUBI = "月火水木金土日"

DEFAULT_TITLE = "55万レースのデータベースからみる\n統計学の予想"


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size * S)


def canvas(w, h, color):
    img = Image.new("RGB", (w * S, h * S), color)
    return img, ImageDraw.Draw(img)


def finish(img, w, h, grain=0.0, seed=0):
    img = img.resize((w, h), Image.LANCZOS)
    if grain:
        img = add_grain(img, grain, seed)
    return img


def add_grain(img, amount, seed=0):
    """紙っぽいざらつきを足す（均一なノイズ＋うっすらムラ）。"""
    rnd = random.Random(seed)
    w, h = img.size
    noise = Image.effect_noise((w, h), 40).convert("L")
    blotch = Image.new("L", (w // 40 + 1, h // 40 + 1))
    blotch.putdata([rnd.randint(100, 156) for _ in range(blotch.size[0] * blotch.size[1])])
    blotch = blotch.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(20))
    mix = Image.blend(noise, blotch, 0.5)
    overlay = Image.merge("RGB", [mix] * 3)
    return Image.blend(img, Image.composite(overlay, img, Image.new("L", (w, h), 255)), amount)


def wobble_line(d, pts, fill, width, seed=0, jitter=1.6, step=6):
    """手で引いたような、少し揺れる線。"""
    rnd = random.Random(seed)
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / (step * S)))
        for i in range(n):
            t = i / n
            out.append((x0 + (x1 - x0) * t + rnd.uniform(-jitter, jitter) * S,
                        y0 + (y1 - y0) * t + rnd.uniform(-jitter, jitter) * S))
    out.append(pts[-1])
    d.line(out, fill=fill, width=int(width * S), joint="curve")


def wobble_ellipse(d, box, fill, width, seed=0, turns=1.08):
    rnd = random.Random(seed)
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    pts = []
    start = rnd.uniform(0, math.tau)
    n = 70
    for i in range(int(n * turns) + 1):
        a = start + math.tau * i / n
        k = 1 + 0.035 * math.sin(a * 2 + rnd.uniform(0, 0.4)) + i * 0.0006
        pts.append((cx + rx * k * math.cos(a), cy + ry * k * math.sin(a)))
    d.line(pts, fill=fill, width=int(width * S), joint="curve")


def text(d, xy, s, f, fill, anchor="la", spacing=0):
    d.multiline_text((xy[0] * S, xy[1] * S), s, font=f, fill=fill, anchor=anchor,
                     spacing=spacing * S)


def date_parts(day):
    return day.month, day.day, YOUBI[day.weekday()]


# ---------------------------------------------------------------- 案A 方眼ノート
def style_note(day, title, sub):
    paper, ink, grid, red = "#f5f0e3", "#2b2a28", "#c9dbe6", "#c8392f"
    img, d = canvas(W, H, paper)
    for x in range(0, W, 24):
        d.line([(x * S, 0), (x * S, H * S)], fill=grid, width=S if x % 120 else 2 * S)
    for y in range(0, H, 24):
        d.line([(0, y * S), (W * S, y * S)], fill=grid, width=S if y % 120 else 2 * S)
    # 左の赤いマージン線
    d.line([(96 * S, 0), (96 * S, H * S)], fill="#e3a49d", width=2 * S)

    m, dd, yb = date_parts(day)
    text(d, (130, 70), f"{m}/{dd} ({yb})", font("KleeOne-SemiBold.ttf", 56), ink)
    wobble_line(d, [(128 * S, 146 * S), (400 * S, 142 * S)], red, 3, seed=dd)

    text(d, (130, 200), title, font("KleeOne-SemiBold.ttf", 54), ink, spacing=22)
    if sub:
        text(d, (130, 380), sub, font("KleeOne-SemiBold.ttf", 34), "#555048")

    # 右下: 6艇を手描きの丸で並べ、1号艇に赤ペンで印
    bx, by, r = 760, 470, 34
    for i in range(6):
        cx = bx + i * 78
        cy = by + (i % 2) * 6
        d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=WAKU[i])
        wobble_ellipse(d, [(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], ink, 2.2, seed=i + 3)
        text(d, (cx, cy + 2), str(i + 1), font("ZenKakuGothicNew-Black.ttf", 36), WAKU_TEXT[i], anchor="mm")
    wobble_ellipse(d, [(bx - 52) * S, (by - 50) * S, (bx + 52) * S, (by + 52) * S], red, 3.4, seed=9, turns=1.2)
    # ゆるいカーブの矢印（文字 → 1号艇）
    ax0, ay0, ax1, ay1 = bx + 70, by - 150, bx + 18, by - 62
    curve = [((ax0 + (ax1 - ax0) * t - 30 * math.sin(math.pi * t)) * S, (ay0 + (ay1 - ay0) * t) * S)
             for t in [i / 12 for i in range(13)]]
    wobble_line(d, curve, red, 3, seed=4, jitter=0.6, step=4)
    wobble_line(d, [((ax1 - 14) * S, (ay1 - 22) * S), (ax1 * S, ay1 * S), ((ax1 + 20) * S, (ay1 - 16) * S)],
                red, 3, seed=5, jitter=0.5)
    text(d, (bx + 86, by - 210), "1号艇の1着率\n54.5%", font("KleeOne-SemiBold.ttf", 36), red, spacing=6)
    text(d, (W - 60, H - 40), "※1号艇の1着率: 2026年7〜10月 全国17,964レース", font("KleeOne-SemiBold.ttf", 20),
         "#7a746a", anchor="rs")
    return finish(img, W, H, grain=0.06, seed=dd)


# ---------------------------------------------------------------- 案B 競艇新聞風
def style_shinbun(day, title, sub):
    paper, ink, red = "#eee8da", "#1b1a18", "#b8322a"
    img, d = canvas(W, H, paper)
    pad = 56
    # 上の題字欄（二重罫）
    d.rectangle([pad * S, 40 * S, (W - pad) * S, 44 * S], fill=ink)
    d.rectangle([pad * S, 50 * S, (W - pad) * S, 51 * S], fill=ink)
    m, dd, yb = date_parts(day)
    text(d, (pad, 64), "統計で読むボートレース", font("ZenOldMincho-Bold.ttf", 26), ink)
    text(d, (W - pad, 64), f"{day.year}年{m}月{dd}日（{yb}）", font("ZenOldMincho-Bold.ttf", 26), ink, anchor="ra")
    d.rectangle([pad * S, 108 * S, (W - pad) * S, 109 * S], fill=ink)

    # 見出し
    text(d, (pad, 150), title, font("ZenOldMincho-Black.ttf", 66), ink, spacing=22)

    # 赤い角印（日付）
    sx, sy, sw = W - pad - 190, 432, 170
    stamp = Image.new("RGBA", (sw * S, sw * S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.rectangle([4 * S, 4 * S, (sw - 4) * S, (sw - 4) * S], outline=red, width=7 * S)
    sd.text((sw / 2 * S, 58 * S), f"{m}/{dd}", font=font("ZenOldMincho-Black.ttf", 58), fill=red, anchor="mm")
    sd.text((sw / 2 * S, 122 * S), f"{yb}曜 予想", font=font("ZenOldMincho-Black.ttf", 32), fill=red, anchor="mm")
    # かすれ
    rnd = random.Random(dd)
    mask = Image.new("L", stamp.size, 255)
    md = ImageDraw.Draw(mask)
    for _ in range(260):
        x, y = rnd.uniform(0, stamp.size[0]), rnd.uniform(0, stamp.size[1])
        rr = rnd.uniform(1, 5) * S
        md.ellipse([x - rr, y - rr, x + rr, y + rr], fill=rnd.randint(0, 120))
    stamp.putalpha(Image.composite(stamp.getchannel("A"), Image.new("L", stamp.size, 0), mask))
    stamp = stamp.rotate(-4, resample=Image.BICUBIC, expand=False)
    img.paste(stamp, (sx * S, sy * S), stamp)

    # 下段: 罫の下に枠番の色を並べ、小見出し
    y0 = 400
    d.rectangle([pad * S, y0 * S, (W - pad) * S, (y0 + 2) * S], fill=ink)
    for i in range(6):
        x = pad + i * 60
        d.rectangle([x * S, (y0 + 34) * S, (x + 48) * S, (y0 + 82) * S], fill=WAKU[i], outline=ink, width=2 * S)
        text(d, (x + 24, y0 + 59), str(i + 1), font("ZenKakuGothicNew-Black.ttf", 30), WAKU_TEXT[i], anchor="mm")
    note = sub or "1号艇の1着率は54.5%。残りの45.5%をどう読むか。"
    text(d, (pad, y0 + 120), note, font("ZenOldMincho-Bold.ttf", 34), ink)
    text(d, (pad + 380, y0 + 60), "全国24場 55万レースの記録から", font("ZenOldMincho-Bold.ttf", 24), "#4a463f", anchor="lm")
    d.rectangle([pad * S, (H - 40) * S, (W - pad) * S, (H - 36) * S], fill=ink)
    return finish(img, W, H, grain=0.08, seed=dd)


# ---------------------------------------------------------------- 案C 枠色ストライプ
def style_simple(day, title, sub):
    bg, ink = "#f7f5f0", "#16202b"
    img, d = canvas(W, H, bg)
    # 左端に6枠の色帯
    bw = 22
    for i, c in enumerate(WAKU):
        d.rectangle([(i * bw) * S, 0, ((i + 1) * bw) * S, H * S], fill=c)
    d.line([(bw * S, 0), (bw * S, H * S)], fill="#d8d3c8", width=S)  # 白枠の境目
    x = 6 * bw + 80
    m, dd, yb = date_parts(day)
    text(d, (x, 70), f"{m:02d}.{dd:02d}", font("IBMPlexMono-Bold.ttf", 150), ink)
    text(d, (x + 474, 100), f"{yb}曜日", font("ZenKakuGothicNew-Bold.ttf", 40), "#d23b32")
    text(d, (x + 476, 168), str(day.year), font("IBMPlexMono-Regular.ttf", 30), "#6b7178")
    d.rectangle([x * S, 262 * S, (x + 90) * S, 268 * S], fill=ink)
    text(d, (x, 300), title, font("ZenKakuGothicNew-Bold.ttf", 52), ink, spacing=18)
    if sub:
        text(d, (x, 480), sub, font("ZenKakuGothicNew-Regular.ttf", 32), "#4a525c")
    text(d, (W - 60, H - 46), "DATA  550,000 RACES", font("IBMPlexMono-Regular.ttf", 22), "#8a8f96", anchor="rs")
    return finish(img, W, H)


STYLES = {"note": style_note, "shinbun": style_shinbun, "simple": style_simple}


# ---------------------------------------------------------------- アイコン（Noteは丸く切り抜かれる）
def icons():
    N = 800
    res = {}

    # 1. 枠色の6本ストライプ（いちばんシンプル。小さくても見分けがつく）
    img, d = canvas(N, N, WAKU[0])
    bw = N / 6
    for i, c in enumerate(WAKU):
        d.rectangle([i * bw * S, 0, (i + 1) * bw * S, N * S], fill=c)
    res["icon_stripe"] = finish(img, N, N)

    # 2. 朱色の判子「統」
    img, d = canvas(N, N, "#efe8d8")
    red = "#b8322a"
    wobble_ellipse(d, [110 * S, 110 * S, 690 * S, 690 * S], red, 26, seed=2, turns=1.02)
    text(d, (N / 2, N / 2 + 10), "統", font("ZenOldMincho-Black.ttf", 400), red, anchor="mm")
    img = finish(img, N, N, grain=0.10, seed=5)
    res["icon_hanko"] = img

    # 3. 方眼紙に手描きの棒グラフ（1号艇が高い）
    img, d = canvas(N, N, "#f5f0e3")
    for v in range(0, N, 40):
        d.line([(v * S, 0), (v * S, N * S)], fill="#c9dbe6", width=2 * S)
        d.line([(0, v * S), (N * S, v * S)], fill="#c9dbe6", width=2 * S)
    heights = [440, 120, 100, 90, 60, 40]  # 形だけ（1号艇が抜けて高い、というイメージ）
    base, bwid, gap, x0 = 620, 70, 16, 160
    for i, h in enumerate(heights):
        x = x0 + i * (bwid + gap)
        d.rectangle([x * S, (base - h) * S, (x + bwid) * S, base * S], fill=WAKU[i])
        wobble_line(d, [(x * S, base * S), (x * S, (base - h) * S), ((x + bwid) * S, (base - h) * S),
                        ((x + bwid) * S, base * S)], "#2b2a28", 4, seed=i, jitter=1.2)
    wobble_line(d, [(120 * S, base * S), (690 * S, base * S)], "#2b2a28", 6, seed=11)
    res["icon_graph"] = finish(img, N, N, grain=0.05, seed=3)

    # 4. 白い1号艇の数字を大きく（青地）
    img, d = canvas(N, N, "#2f62b0")
    d.ellipse([170 * S, 170 * S, 630 * S, 630 * S], fill=WAKU[0])
    text(d, (N / 2, N / 2 + 14), "1", font("ZenKakuGothicNew-Black.ttf", 360), "#1f1f1f", anchor="mm")
    res["icon_one"] = finish(img, N, N)

    return res


def circle_preview(img):
    """Note上での見え方（丸く切り抜き）を確認する用。"""
    n = img.size[0]
    mask = Image.new("L", (n * 2, n * 2), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, n * 2, n * 2], fill=255)
    mask = mask.resize((n, n), Image.LANCZOS)
    bg = Image.new("RGB", (n, n), "#ffffff")
    bg.paste(img, (0, 0), mask)
    return bg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD（省略時は今日）")
    ap.add_argument("--style", choices=list(STYLES) + ["all"], default="all")
    ap.add_argument("--title", default=DEFAULT_TITLE, help="改行は \\n")
    ap.add_argument("--sub", default="", help="小見出し（例: 本日の注目 12レース）")
    ap.add_argument("--icons", action="store_true", help="アイコンだけ作る")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    if not a.icons:
        day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
        title = a.title.replace("\\n", "\n")
        for name in (STYLES if a.style == "all" else [a.style]):
            p = OUT / f"header_{name}_{day:%Y%m%d}.png"
            STYLES[name](day, title, a.sub).save(p, optimize=True)
            print(p)
    if a.icons or a.style == "all":
        for name, img in icons().items():
            img.save(OUT / f"{name}.png", optimize=True)
            circle_preview(img.resize((400, 400), Image.LANCZOS)).save(OUT / f"{name}_maru.png")
            print(OUT / f"{name}.png")


if __name__ == "__main__":
    main()

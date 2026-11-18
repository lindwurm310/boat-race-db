"""あきさんの写真の見出し画像 (base.jpg) の文字を、今の記事の中身に入れ替える。

  python note/design/photo/make_photo_header.py [出力.png]

元の写真の文字 (的中率58%、固く勝つ! / 「必勝法」を徹底検証 / 過去10年 最大回収率120%を目指せ) は
--clean で消して base_clean.png にしてある。その上に、同じような金・赤の太字で書き直す。出力は note の見出し画像の大きさ 1280x670。
数字は「過去5年の検証」と必ず一緒に出す (実績や約束と読めないように)。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(os.path.dirname(HERE), "fonts")
BLACK = os.path.join(FONTS, "ZenKakuGothicNew-Black.ttf")
BOLD = os.path.join(FONTS, "ZenKakuGothicNew-Bold.ttf")

TOP_GOLD, TOP_RED = "全レース予想", "無料公開!"
BANNER = "有料は厳選の単勝3点"
SMALL = "過去5年の検証（締切11時以降・期待値の上位3点）"
BIG = "回収率124.3%"
NOTE = "※過去の検証の数字です。これからの回収率を約束するものではありません"


def font(path, size):
    return ImageFont.truetype(path, size)


def gradient_text(img, xy, s, f, top, bottom, stroke, sw, shadow=True):
    """縦グラデーションの文字に太いふち取り (元の写真の金・赤の文字に寄せる)。"""
    d = ImageDraw.Draw(img)
    x, y = xy
    l, t, r, b = d.textbbox((x, y), s, font=f, stroke_width=sw)
    if shadow:
        sh = Image.new("L", img.size, 0)
        ImageDraw.Draw(sh).text((x + 10, y + 12), s, font=f, fill=200, stroke_width=sw)
        img.paste((0, 0, 0), (0, 0), sh.filter(ImageFilter.GaussianBlur(10)))
        d = ImageDraw.Draw(img)
    d.text((x, y), s, font=f, fill=stroke, stroke_width=sw, stroke_fill=stroke)
    grad = Image.new("RGB", (r - l, b - t))
    gd = ImageDraw.Draw(grad)
    for i in range(b - t):
        k = i / max(1, b - t - 1)
        gd.line([(0, i), (r - l, i)], fill=tuple(int(top[j] + (bottom[j] - top[j]) * k) for j in range(3)))
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).text((x, y), s, font=f, fill=255)
    img.paste(grad, (l, t), m.crop((l, t, r, b)))
    return r


def clean():
    """base.jpg の元の文字を消して base_clean.png を作る (一度だけ。opencv-python-headless が必要)。"""
    import cv2
    import numpy as np
    img = cv2.imread(os.path.join(HERE, "base.jpg"))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = np.zeros(img.shape[:2], np.uint8)

    def mask(x0, y0, x1, y1, warm=True):
        roi = hsv[y0:y1, x0:x1]
        t = roi[..., 2] > 200
        if warm:
            t |= (roi[..., 1] > 90) & (roi[..., 2] > 110) & ((roi[..., 0] < 35) | (roi[..., 0] > 160))
        t = cv2.dilate(t.astype(np.uint8) * 255, np.ones((21, 21), np.uint8))
        m[y0:y1, x0:x1] = np.maximum(m[y0:y1, x0:x1], t)

    mask(100, 60, 2480, 450)            # 的中率58%、固く勝つ!
    mask(160, 1000, 1010, 1118)         # 過去10年 単勝の買い目
    mask(100, 1118, 2480, 1330)         # 最大回収率120%を目指せ
    mask(360, 585, 1175, 705, warm=False)  # 赤い帯の白い文字 (まわりの赤で埋める)
    cv2.imwrite(os.path.join(HERE, "base_clean.png"), cv2.inpaint(img, m, 15, cv2.INPAINT_TELEA))


def band(img, y0, y1, alpha, blur=30):
    """横の帯を、ふちをぼかした黒で暗くする (消した跡を目立たなくし、文字を読みやすくする)。"""
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rectangle((0, y0, img.size[0], y1), fill=alpha)
    return Image.composite(Image.new("RGB", img.size, (4, 3, 10)), img, m.filter(ImageFilter.GaussianBlur(blur)))


def make(out):
    img = Image.open(os.path.join(HERE, "base_clean.png")).convert("RGB")  # 2576x1438
    W, H = img.size
    img = band(img, 70, 440, 170)
    img = band(img, 990, H, 190)
    gold = ((255, 240, 150), (226, 140, 20))
    red = ((255, 70, 50), (170, 0, 0))
    f = font(BLACK, 205)
    x = gradient_text(img, (110, 150), TOP_GOLD, f, *gold, (60, 20, 0), 16)
    gradient_text(img, (x + 30, 150), TOP_RED, f, *red, (250, 210, 120), 14)
    d = ImageDraw.Draw(img)
    d.text((400, 598), BANNER, font=font(BLACK, 88), fill="white", stroke_width=3, stroke_fill=(90, 0, 0))
    d.text((180, 1000), SMALL, font=font(BOLD, 72), fill="white", stroke_width=6, stroke_fill="black")
    gradient_text(img, (160, 1075), BIG, font(BLACK, 250), *gold, (60, 20, 0), 16)
    d = ImageDraw.Draw(img)
    d.text((W - 40, H - 22), NOTE, font=font(BOLD, 36), fill=(225, 225, 225), anchor="rs",
           stroke_width=3, stroke_fill="black")
    # note の見出し (1280x670 = 1.91:1) に合わせて上下を少し切って縮める
    h = round(W / (1280 / 670))
    top = H - h  # 上を切る (下の大きな数字を残す)
    img = img.crop((0, top, W, top + h)).resize((1280, 670), Image.LANCZOS)
    img.save(out, optimize=True)
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["--clean"]:
        clean()
        sys.exit()
    print(make(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "header_photo.png")))

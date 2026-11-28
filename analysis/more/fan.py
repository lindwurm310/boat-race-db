"""公式の「レーサー期別成績」(ファン手帳データ, fanYYMM.lzh) を取得して表にする。

  python analysis/more/fan.py --out fan.csv.gz             (2002年前期〜今ある全部。1ファイル1秒あける)

  - fan2604 = 2026年前期 (算出期間 2025-11-01〜2026-04-30 の成績。2026年7月〜12月の級別に使われる)
  - 1行 = 選手 × 期。列は公式のレイアウト (boatrace.jp/owpc/pc/extra/data/layout.html) の通り
  - 2014年後期より前は「支部」が現住所、出身地の列は無い
  - 予想に使うときは、算出期間の終わり (calc_to) より後のレースにだけ使うこと (先の成績が混ざらないように)
"""
import argparse
import io
import os
import re
import time
import urllib.request

import lhafile
import pandas as pd

LIST = "https://www.boatrace.jp/owpc/pc/extra/data/download.html"
BASE = "https://www.boatrace.jp/static_extra/pc_static/download/data/kibetsu/"
UA = "Mozilla/5.0 (personal research; low-rate)"

# (列名, バイト数, 小数の桁)  文字の列は桁 None
F = [("racer_no", 4, 0), ("name", 16, None), ("kana", 15, None), ("branch", 4, None), ("class", 2, None),
     ("era", 1, None), ("birth", 6, None), ("sex", 1, 0), ("age", 2, 0), ("height", 3, 0), ("weight", 2, 0),
     ("blood", 2, None), ("win_rate", 4, 2), ("rate2", 4, 1), ("n1", 3, 0), ("n2", 3, 0), ("starts", 3, 0),
     ("finals", 2, 0), ("wins", 2, 0), ("avg_st", 3, 2)]
for c in range(1, 7):
    F += [(f"c{c}_n", 3, 0), (f"c{c}_rate2", 4, 1), (f"c{c}_st", 3, 2), (f"c{c}_st_rank", 3, 2)]
F += [("class_prev", 2, None), ("class_prev2", 2, None), ("class_prev3", 2, None), ("ability_prev", 4, 2),
      ("ability", 4, 2), ("year", 4, 0), ("term", 1, 0), ("calc_from", 8, None), ("calc_to", 8, None),
      ("training", 3, 0)]
for c in range(1, 7):
    F += [(f"c{c}_f{k}", 3, 0) for k in range(1, 7)]
    F += [(f"c{c}_{k}", 2, 0) for k in ("F", "L0", "L1", "K0", "K1", "S0", "S1", "S2")]
F += [(f"nc_{k}", 2, 0) for k in ("L0", "L1", "K0", "K1")] + [("birthplace", 6, None)]


def http(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
        return r.read()


def parse_line(b):
    r, p = {}, 0
    for name, n, dec in F:
        if p >= len(b):
            break
        s = b[p:p + n].decode("cp932", "replace")
        p += n
        if dec is None:
            r[name] = s.replace("　", " ").strip() or None
        else:
            s = s.strip()
            r[name] = (int(s) / 10 ** dec if dec else int(s)) if s.isdigit() else None
    return r


def parse_lzh(data, fname):
    lf = lhafile.Lhafile(io.BytesIO(data))
    out = []
    for info in lf.infolist():
        for line in lf.read(info.filename).split(b"\r\n"):
            if len(line) > 100:
                out.append(dict(file=fname, **parse_line(line)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw", default="raw/fan", help="取った lzh を置くところ")
    a = ap.parse_args()
    os.makedirs(a.raw, exist_ok=True)
    names = sorted(set(re.findall(r"kibetsu/(fan\d{4}\.lzh)", http(LIST).decode("utf-8", "replace"))))
    print(len(names), "ファイル", names[0], "〜", names[-1], flush=True)
    rows = []
    for n in names:
        p = os.path.join(a.raw, n)
        if not os.path.exists(p):
            time.sleep(1)
            try:
                data = http(BASE + n)
            except Exception as e:
                print(n, "取れない", e, flush=True)
                continue
            if data[2:5] != b"-lh":
                print(n, "LZH でない (まだ出ていない?)", flush=True)
                continue
            open(p, "wb").write(data)
        rr = parse_lzh(open(p, "rb").read(), n)
        print(n, len(rr), "人", flush=True)
        rows += rr
    df = pd.DataFrame(rows)
    df.to_csv(a.out, index=False)
    print(len(df), "行", df.shape[1], "列")


if __name__ == "__main__":
    main()

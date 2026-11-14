"""collect_beforeinfo.py で集めたページを表にする。

  python analysis/extra/parse_beforeinfo.py bi/*.jsonl.gz --out beforeinfo.csv.gz

列: race_id, lane, wday (当日体重), adj_weight (調整重量), bi_ex (展示タイム), tilt, prop_new (プロペラ新),
    parts_n (部品交換の点数), parts_ring / parts_piston / parts_carb / parts_cyl / parts_elec / parts_gear (交換あり),
    sx_course (スタート展示の進入コース), sx_st (スタート展示の ST。F は負)
"""
import argparse
import gzip
import json
import re
import unicodedata

import pandas as pd

RE_TBODY = re.compile(r'<tbody class="is-fs12 ?">(.*?)</tbody>', re.S)
RE_START = re.compile(r'table1_boatImage1Number is-type\d">(\d)</span>.*?table1_boatImage1Time[^>]*>([^<]*)</span>', re.S)
PARTS = {"リング": "ring", "ピストン": "piston", "キャブ": "carb", "シリンダ": "cyl", "電気": "elec", "ギヤ": "gear",
         "シャフト": "shaft", "キャリボ": "carrier"}


def fnum(s):
    s = (s or "").replace("&nbsp;", "").replace("kg", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse(h):
    rows = {}
    for body in RE_TBODY.findall(h):
        m = re.search(r'is-boatColor(\d) is-fs14" rowspan="4">', body)
        if not m:
            continue
        lane = int(m.group(1))
        tds = re.findall(r'<td[^>]*>(.*?)</td>', body, re.S)
        # 枠, 写真, 名前, 体重, 展示, チルト, プロペラ, 部品交換, R, ..., 進入, ..., 調整重量, ST, ...
        cells = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        r = dict(lane=lane, wday=fnum(cells[3]), bi_ex=fnum(cells[4]), tilt=fnum(cells[5]),
                 prop_new=int("新" in cells[6]))
        adj = re.search(r'<td>進入</td>.*?</tr>\s*<tr>\s*<td rowspan="2">([^<]*)</td>', body, re.S)
        r["adj_weight"] = fnum(adj.group(1)) if adj else None
        labels = re.findall(r'label4 is-type1">([^<]*)<', tds[7] if len(tds) > 7 else "")
        n = 0
        for k in PARTS.values():
            r[f"parts_{k}"] = 0
        for lb in labels:
            lb = unicodedata.normalize("NFKC", lb.replace("&nbsp;", "")).strip()
            mm = re.match(r"([^×]+)(?:×(\d+))?", lb)
            if not mm:
                continue
            name, cnt = mm.group(1).strip(), int(mm.group(2) or 1)
            key = PARTS.get(name)
            if key:
                r[f"parts_{key}"] = 1
            n += cnt
        r["parts_n"] = n
        rows[lane] = r
    for course, (lane, st) in enumerate(RE_START.findall(h), 1):
        st = st.strip()
        if int(lane) in rows:
            rows[int(lane)]["sx_course"] = course
            v = fnum(st[1:]) if st.startswith("F") else fnum(st)
            rows[int(lane)]["sx_st"] = -v if (st.startswith("F") and v is not None) else v
    return list(rows.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = []
    for f in a.files:
        for line in gzip.open(f, "rt", encoding="utf-8"):
            x = json.loads(line)
            if x["s"] != 200:
                continue
            rid = f"{x['d'].replace('-', '')}-{x['j']:02d}-{x['r']:02d}"
            for r in parse(x["h"]):
                out.append(dict(race_id=rid, **r))
    df = pd.DataFrame(out).drop_duplicates(["race_id", "lane"], keep="last")
    df.to_csv(a.out, index=False)
    print(len(df), "行", df.race_id.nunique(), "レース")
    print(df.describe().T[["count", "mean", "min", "max"]])


if __name__ == "__main__":
    main()

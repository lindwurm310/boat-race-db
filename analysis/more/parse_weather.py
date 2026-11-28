"""集めてある直前情報のページ (Release bi-history の bi_*.jsonl.gz) から、水面気象を表にする。

  python analysis/more/parse_weather.py bi/bi_*.jsonl.gz --out weather_bi.csv.gz

列: race_id, weather (天候の文字), air_c (気温), water_c (水温), wind_ms (風速), wave_cm (波高),
    wind_dir (風向の画像番号 is-windN。1〜16、0 は無風など), weather_img (天候の画像番号), obs_time (観測時刻の表示)
  ※ 気温・水温は DB (競走成績) に無い。風・波は DB にもある (成績のはレース時、こちらは展示のころ)
"""
import argparse
import gzip
import json
import re

import pandas as pd

RE_UNIT = re.compile(r'weather1_bodyUnitLabelTitle">([^<]*)</span>\s*(?:<span class="weather1_bodyUnitLabelData">\s*([^<]*)</span>)?')


def num(s):
    m = re.search(r"-?[\d.]+", s or "")
    return float(m.group(0)) if m else None


def parse(h):
    i = h.find("weather1")
    if i < 0:
        return None
    w = h[i:]
    r = dict(weather=None, air_c=None, water_c=None, wind_ms=None, wave_cm=None)
    for title, data in RE_UNIT.findall(w):
        title = title.strip()
        if title == "気温":
            r["air_c"] = num(data)
        elif title == "水温":
            r["water_c"] = num(data)
        elif title == "風速":
            r["wind_ms"] = num(data)
        elif title == "波高":
            r["wave_cm"] = num(data)
        elif not data and title:
            r["weather"] = title
    m = re.search(r"is-wind(\d+)", w)
    r["wind_dir"] = int(m.group(1)) if m else None
    m = re.search(r"is-weather(\d+)", w)
    r["weather_img"] = int(m.group(1)) if m else None
    m = re.search(r"weather1_title[^>]*>\s*([^<]*)<", w)
    r["obs_time"] = m.group(1).strip() if m else None
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out, first = [], True
    for f in a.files:
        for line in gzip.open(f, "rt", encoding="utf-8"):
            x = json.loads(line)
            if x["s"] != 200:
                continue
            if first:
                i = x["h"].find("weather1")
                print("見本:", x["h"][i:i + 1500], flush=True)
                first = False
            r = parse(x["h"])
            if r:
                out.append(dict(race_id=f"{x['d'].replace('-', '')}-{x['j']:02d}-{x['r']:02d}", **r))
    df = pd.DataFrame(out).drop_duplicates("race_id", keep="last")
    df.to_csv(a.out, index=False)
    print(len(df), "レース")
    print(df.describe(include="all").T)


if __name__ == "__main__":
    main()

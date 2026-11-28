"""気象庁の潮位表 (推算潮位, 毎時と満潮・干潮) を取って、レース場ごとの表にする。

  python analysis/more/tide.py --start 2002 --end 2027 --out tide_hourly.csv.gz --hl tide_hilo.csv.gz

  - 元データ: https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt/{年}/{地点}.txt (1地点1年1ファイル)
  - 潮位は「潮位表基準面」からの cm。満潮・干潮は時刻 (HHMM) と潮位
  - 海水・汽水のレース場だけ。近い観測地点で代用している (VENUE_STATION)。淡水の場 (桐生・戸田・多摩川・びわこ・芦屋) は無し
"""
import argparse
import io
import time
import urllib.error
import urllib.request

import pandas as pd

URL = "https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt/{y}/{st}.txt"
UA = "Mozilla/5.0 (personal research; low-rate)"
# 場コード -> 気象庁の地点記号 (地点名)
VENUE_STATION = {
    3: ("TK", "東京"), 4: ("TK", "東京"), 6: ("MI", "舞阪"), 7: ("G5", "形原"), 8: ("ZD", "鬼崎"),
    9: ("G3", "四日市港"), 10: ("ZG", "三国"), 12: ("OS", "大阪"), 13: ("AM", "尼崎"), 14: ("KM", "小松島"),
    15: ("TX", "多度津"), 16: ("UN", "宇野"), 17: ("Q8", "広島"), 18: ("QA", "徳山"), 19: ("A1", "弟子待"),
    20: ("N0", "砂津"), 22: ("QF", "博多"), 23: ("KA", "唐津"), 24: ("QD", "佐世保"),
}


def get(url):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return r.read().decode("ascii", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
        time.sleep(5 * (k + 1))
    return None


def parse(text, st):
    """1行 = 1日。1〜72桁: 毎時潮位 (3桁×24), 73〜78: 年月日 (YYMMDD), 79〜80: 地点, 81〜108: 満潮 (時刻4+潮位3)×4,
    109〜136: 干潮 ×4。9999/999 は無し。"""
    hourly, hilo = [], []
    for line in text.splitlines():
        if len(line) < 136:
            continue
        ymd = line[72:78].replace(" ", "0")
        date = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
        lv = [int(line[i * 3:i * 3 + 3]) for i in range(24)]
        hourly.append(dict(date=date, station=st, **{f"h{i:02d}": v for i, v in enumerate(lv)}))
        for kind, base in (("high", 80), ("low", 108)):
            for k in range(4):
                s = line[base + k * 7: base + k * 7 + 7]
                hh, mm, v = s[:2].strip(), s[2:4].strip(), s[4:].strip()
                if hh and s[:4] != "9999" and v != "999":
                    hilo.append(dict(date=date, station=st, kind=kind, time=f"{int(hh):02d}:{int(mm or 0):02d}",
                                     level=int(v)))
    return hourly, hilo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2002)
    ap.add_argument("--end", type=int, default=2027)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hl", required=True)
    a = ap.parse_args()
    stations = sorted({s for s, _ in VENUE_STATION.values()})
    H, L = [], []
    for y in range(a.start, a.end + 1):
        for st in stations:
            t = get(URL.format(y=y, st=st))
            time.sleep(1)
            if not t:
                print(y, st, "無し", flush=True)
                continue
            h, l = parse(t, st)
            H += h
            L += l
        print(y, "済", len(H), flush=True)
    vs = pd.DataFrame([dict(jcd=j, station=s, station_name=n) for j, (s, n) in VENUE_STATION.items()])
    pd.DataFrame(H).merge(vs, on="station").to_csv(a.out, index=False)
    pd.DataFrame(L).merge(vs, on="station").to_csv(a.hl, index=False)
    print("毎時", len(H), "日×地点  満干", len(L))


if __name__ == "__main__":
    main()

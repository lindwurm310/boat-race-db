"""過去の直前情報のページ (beforeinfo) を集める (検証用)。ページの必要な部分だけを gzip の jsonl に保存し、解析は後でする。

  python analysis/extra/collect_beforeinfo.py --races analysis/extra/races_2021-10-25.csv.gz \
      --start 2026-09-01 --end 2026-10-24 --out bi.jsonl.gz

  - アクセスを始めるのは 1 秒に 1 回まで (公式サイトは1ページに数秒かかるので、いくつか同時に待つ)
  - 1行 = {"d": 日付, "j": 場, "r": R, "s": HTTP状態, "h": ページの必要部分}
"""
import argparse
import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

BASE = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd:02d}&hd={hd}"
UA = "Mozilla/5.0 (personal research; low-rate)"
INTERVAL = 1.0
_lock = threading.Lock()
_next = [0.0]


def trim(html):
    """script・style・head を落とし、空白を詰め、本文 (出走表〜水面気象) の部分だけを残す。"""
    html = re.sub(r"<(script|style|head|noscript)\b.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"\s+", " ", html)
    i = html.find("contentsFrame1_inner")
    j = html.rfind("weather1")
    if i < 0:
        i = 0
    if j < i:
        j = len(html)
    return html[max(0, i - 50): j + 4000]


def fetch(jcd, rno, d):
    with _lock:
        w = _next[0] - time.time()
        if w > 0:
            time.sleep(w)
        _next[0] = time.time() + INTERVAL
    url = BASE.format(rno=rno, jcd=jcd, hd=d.replace("-", ""))
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
                return 200, trim(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404 or k == 2:
                return e.code, ""
        except Exception as e:
            if k == 2:
                return 0, str(e)
        time.sleep(10 * (k + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--races", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    r = pd.read_csv(a.races)
    r = r[(r.race_date >= a.start) & (r.race_date <= a.end)]
    if a.limit:
        r = r.head(a.limit)
    rows = list(r.itertuples(index=False))
    print(f"{a.start}〜{a.end}: {len(rows)} レース", flush=True)
    t0, n, bad = time.time(), 0, 0
    with gzip.open(a.out, "wt", encoding="utf-8") as f, ThreadPoolExecutor(a.workers) as ex:
        for x, (s, h) in zip(rows, ex.map(lambda x: fetch(int(x.jcd), int(x.rno), x.race_date), rows)):
            f.write(json.dumps(dict(d=x.race_date, j=int(x.jcd), r=int(x.rno), s=s, h=h), ensure_ascii=False) + "\n")
            n += 1
            bad += s != 200 or "is-boatColor1" not in h
            if n % 500 == 0:
                print(f"{n}/{len(rows)}  {x.race_date}  {(time.time() - t0) / n:.2f}秒/レース  取れなかった {bad}",
                      flush=True)
    print(f"完了 {n} レース  取れなかった {bad}  {(time.time() - t0) / 60:.0f}分", flush=True)


if __name__ == "__main__":
    main()

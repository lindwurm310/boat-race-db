"""過去レースの確定オッズ (締切時オッズ) を全部の券種・全部の組み合わせで集める。

  python analysis/more/collect_odds.py --races analysis/extra/races_2021-10-25.csv.gz \
      --start 2026-10-01 --end 2026-10-20 --out odds_2026-10-01.jsonl.gz [--resume 前の同じファイル] [--minutes 330]

  - 1レース = 5ページ (3連単 odds3t・3連複 odds3f・2連単/2連複 odds2tf・拡連複 oddsk・単勝/複勝 oddstf)
  - アクセスを始めるのは 1 秒に 1 回まで (返事が遅いので、いくつか同時に待つ)
  - 1行 = {"d": 日付, "j": 場, "r": R, "s": {ページ: HTTP状態}, "o": {券種: {組み合わせ: オッズ}}, "t": ページの時刻表示}
    3連単は "1-2-3"、3連複は "1=2=3"。拡連複・複勝は "下限-上限" の文字。欠場などは null
  - races の表に無い日 (表より後の日) は、その日の公式トップから開催場を見て 1〜12R を取る
  - --minutes を過ぎたら止める。--resume に前のファイルを渡すと、取れているレースは飛ばして続きから
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from odds_parse import parse  # noqa: E402

BASE = "https://www.boatrace.jp/owpc/pc/race/{page}?rno={rno}&jcd={jcd:02d}&hd={hd}"
PAGES = ["odds3t", "odds3f", "odds2tf", "oddsk", "oddstf"]
UA = "Mozilla/5.0 (personal research; low-rate)"
INTERVAL = 1.0
_lock = threading.Lock()
_next = [0.0]


def get(url):
    with _lock:
        w = _next[0] - time.time()
        if w > 0:
            time.sleep(w)
        _next[0] = time.time() + INTERVAL
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
                return 200, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404 or k == 2:
                return e.code, ""
        except Exception:
            if k == 2:
                return 0, ""
        time.sleep(10 * (k + 1))


def fetch_race(x):
    d, j, r = x
    st, odds, t = {}, {}, None
    for p in PAGES:
        s, h = get(BASE.format(page=p, rno=r, jcd=j, hd=d.replace("-", "")))
        st[p] = s
        if s == 200 and h:
            res = parse(h)
            t = res.pop("time", None) or t
            odds.update(res)
    return dict(d=d, j=j, r=r, s=st, o=odds, t=t)


def venues_on(d):
    s, h = get(f"https://www.boatrace.jp/owpc/pc/race/index?hd={d.replace('-', '')}")
    return sorted({int(j) for j, hd in re.findall(r"raceindex\?jcd=(\d\d)&amp;hd=(\d+)", h) if hd == d.replace("-", "")})


def race_list(path, start, end):
    r = pd.read_csv(path)
    r = r[(r.race_date >= start) & (r.race_date <= end)]
    rows = [(x.race_date, int(x.jcd), int(x.rno)) for x in r.itertuples(index=False)]
    last = pd.read_csv(path).race_date.max()
    d = max(dt.date.fromisoformat(start), dt.date.fromisoformat(last) + dt.timedelta(days=1))
    while d <= dt.date.fromisoformat(end):
        rows += [(d.isoformat(), j, rno) for j in venues_on(d.isoformat()) for rno in range(1, 13)]
        d += dt.timedelta(days=1)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--races", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--minutes", type=float, default=330)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    t0 = time.time()
    done, keep = set(), []
    if a.resume and os.path.exists(a.resume):
        for line in gzip.open(a.resume, "rt", encoding="utf-8"):
            x = json.loads(line)
            if x["o"].get("3連単") or all(v == 404 for v in x["s"].values()):
                done.add((x["d"], x["j"], x["r"]))
                keep.append(line)
    rows = [x for x in race_list(a.races, a.start, a.end) if x not in done]
    if a.limit:
        rows = rows[:a.limit]
    print(f"{a.start}〜{a.end}: 残り {len(rows)} レース (取れている {len(done)})", flush=True)
    n = bad = 0
    stop = threading.Event()

    def job(x):
        if stop.is_set() or time.time() - t0 > a.minutes * 60:
            stop.set()
            return None
        return fetch_race(x)

    with gzip.open(a.out, "wt", encoding="utf-8") as f, ThreadPoolExecutor(a.workers) as ex:
        for line in keep:
            f.write(line)
        for res in ex.map(job, rows):
            if res is None:
                continue
            f.write(json.dumps(res, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
            bad += not res["o"].get("3連単")
            if n % 200 == 0:
                print(f"{n}/{len(rows)}  {res['d']}  {(time.time() - t0) / n:.1f}秒/レース  3連単が無い {bad}", flush=True)
    left = len(rows) - n
    print(f"{'完了' if not left else '時間切れ'} {n} レース (残り {left})  3連単が無い {bad}  {(time.time() - t0) / 60:.0f}分",
          flush=True)
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as g:
        g.write(f"left={left}\n")


if __name__ == "__main__":
    main()

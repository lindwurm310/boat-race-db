"""BOATCAST (race.boatcast.jp) の、公式サイトには無いデータを過去レースの分まで集める。

  python analysis/more/collect_boatcast.py --races analysis/extra/races_2021-10-25.csv.gz \
      --start 2026-08-01 --end 2026-08-20 --out bc_2026-08-01.jsonl.gz [--resume 前のファイル] [--minutes 320]

  取るファイル (1レース4つ。過去分は 2024年3月末ごろから。それより前は 403):
    oriten   = オリジナル展示 (一周・まわり足・直線など。場によって項目が違う。江戸川などは無し)
    hyousu1  = 確定票数 (単勝・複勝・2連複・拡連複など。券種ごとの票数と合計)
    hyousu2  = 確定票数 (2連単・2連複)
    hyousu3  = 確定票数 (3連単・3連複。1着の艇ごとの行)
  - 1行 = {"d", "j", "r", "s": {名前: HTTP状態}, "t": {名前: ファイルの中身 (タブ区切りの文字)}}
  - アクセスを始めるのは 1 秒に 1 回まで
"""
import argparse
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import collect_odds  # noqa: E402  (レースの一覧の作り方を共有)

BC = "https://race.boatcast.jp"
FILES = {
    "oriten": "/txt/{j}/bc_oriten_{ymd}_{j}_{r}.txt",
    "hyousu1": "/txt/{j}/bc_kakutei_hyousu1_{ymd}_{j}_{r}.txt",
    "hyousu2": "/txt/{j}/bc_kakutei_hyousu2_{ymd}_{j}_{r}.txt",
    "hyousu3": "/txt/{j}/bc_kakutei_hyousu3_{ymd}_{j}_{r}.txt",
}
UA = "Mozilla/5.0 (personal research; low-rate)"
_lock = threading.Lock()
_next = [0.0]


def get(url):
    with _lock:
        w = _next[0] - time.time()
        if w > 0:
            time.sleep(w)
        _next[0] = time.time() + 1.0
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return 200, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404) or k == 2:
                return e.code, ""
        except Exception:
            if k == 2:
                return 0, ""
        time.sleep(10 * (k + 1))


def fetch_race(x):
    d, j, r = x
    st, tx = {}, {}
    for name, path in FILES.items():
        s, t = get(BC + path.format(j=f"{j:02d}", ymd=d.replace("-", ""), r=f"{r:02d}"))
        st[name] = s
        if s == 200:
            tx[name] = t
    return dict(d=d, j=j, r=r, s=st, t=tx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--races", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--minutes", type=float, default=320)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    t0 = time.time()
    done, keep = set(), []
    if a.resume and os.path.exists(a.resume):
        for line in gzip.open(a.resume, "rt", encoding="utf-8"):
            x = json.loads(line)
            if all(v in (200, 403, 404) for v in x["s"].values()):
                done.add((x["d"], x["j"], x["r"]))
                keep.append(line)
    rows = [x for x in collect_odds.race_list(a.races, a.start, a.end) if x not in done]
    if a.limit:
        rows = rows[:a.limit]
    print(f"{a.start}〜{a.end}: 残り {len(rows)} レース (取れている {len(done)})", flush=True)
    n = ok = 0
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
            ok += "hyousu3" in res["t"]
            if n % 200 == 0:
                print(f"{n}/{len(rows)}  {res['d']}  {(time.time() - t0) / n:.1f}秒/レース  票数あり {ok}", flush=True)
    print(f"{'完了' if n == len(rows) else '時間切れ'} {n} レース (残り {len(rows) - n})  票数あり {ok}  "
          f"{(time.time() - t0) / 60:.0f}分", flush=True)


if __name__ == "__main__":
    main()

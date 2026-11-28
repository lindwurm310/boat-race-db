"""その日のオッズの動きを記録する。締切の 30・20・10・5・3・1 分前に、全券種のオッズを取る。

  python analysis/more/odds_live.py --out odds_live_20261129_1.jsonl.gz --from 07:30 --to 13:30

  - --from〜--to (日本時間) に取る予定のものだけを取る (1日を何回かに分けて動かすため)
  - 締切予定時刻は各場の出走表から読む。遅れることがあるので 20 分ごとに読み直す
  - アクセスは 1 秒に 1 回まで。1回 = 5 ページ (odds3t・odds3f・odds2tf・oddsk・oddstf)
  - 1行 = {"d", "j", "r", "deadline": 締切予定, "m": 何分前, "at": 取った時刻, "t": ページの時刻表示, "o": {券種: {組: オッズ}}}
  - 予定より 3 分以上遅れたもの (前のものに時間がかかったとき) は飛ばす
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from odds_parse import parse  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
BASE = "https://www.boatrace.jp/owpc/pc/race/{page}?rno={rno}&jcd={jcd:02d}&hd={hd}"
PAGES = ["odds3t", "odds3f", "odds2tf", "oddsk", "oddstf"]
UA = "Mozilla/5.0 (personal research; low-rate)"
_last = [0.0]


def get(url):
    w = 1.0 - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print("取れない", url, e, flush=True)
        return ""
    finally:
        _last[0] = time.time()


def now():
    return dt.datetime.now(JST)


def schedule(hd):
    """{(場, R): 締切予定 datetime}"""
    h = get(f"https://www.boatrace.jp/owpc/pc/race/index?hd={hd}")
    js = sorted({int(j) for j, d in re.findall(r"raceindex\?jcd=(\d\d)&amp;hd=(\d+)", h) if d == hd})
    out = {}
    day = dt.datetime.strptime(hd, "%Y%m%d").replace(tzinfo=JST)
    for j in js:
        h = re.sub(r"\s+", " ", get(BASE.format(page="racelist", rno=1, jcd=j, hd=hd)))
        m = re.search(r"締切予定時刻</td>(.*?)</tr>", h)
        if not m:
            continue
        for r, t in enumerate(re.findall(r"<td[^>]*>\s*(\d{1,2}):(\d\d)\s*</td>", m.group(1)), 1):
            out[(j, r)] = day.replace(hour=int(t[0]), minute=int(t[1]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", default=None, help="YYYYMMDD。省略で今日 (日本時間)")
    ap.add_argument("--from", dest="frm", default="00:00")
    ap.add_argument("--to", default="23:59")
    ap.add_argument("--minutes", default="30,20,10,5,3,1")
    a = ap.parse_args()
    hd = a.date or now().strftime("%Y%m%d")
    day = dt.datetime.strptime(hd, "%Y%m%d").replace(tzinfo=JST)
    lo = day.replace(hour=int(a.frm[:2]), minute=int(a.frm[3:]))
    hi = day.replace(hour=int(a.to[:2]), minute=int(a.to[3:]))
    mins = [int(x) for x in a.minutes.split(",")]
    sch, loaded = schedule(hd), time.time()
    print(hd, len(sch), "レース", flush=True)
    done, n, skip = set(), 0, 0
    with gzip.open(a.out, "wt", encoding="utf-8") as f:
        while True:
            if time.time() - loaded > 1200:
                sch.update(schedule(hd))
                loaded = time.time()
            todo = sorted((dl - dt.timedelta(minutes=m), j, r, m, dl) for (j, r), dl in sch.items() for m in mins
                          if (j, r, m) not in done and lo <= dl - dt.timedelta(minutes=m) < hi)
            if not todo:
                break
            due, j, r, m, dl = todo[0]
            wait = (due - now()).total_seconds()
            if wait > 0:
                time.sleep(min(wait, 60))
                continue
            done.add((j, r, m))
            if -wait > 180:
                skip += 1
                continue
            at, odds, label = now(), {}, None
            for p in PAGES:
                h = get(BASE.format(page=p, rno=r, jcd=j, hd=hd))
                if h:
                    res = parse(h)
                    label = res.pop("time", None) or label
                    odds.update(res)
            f.write(json.dumps(dict(d=hd, j=j, r=r, deadline=dl.strftime("%H:%M"), m=m, at=at.strftime("%H:%M:%S"),
                                    t=label, o=odds), ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()
            n += 1
            if n % 50 == 0:
                print(now().strftime("%H:%M"), n, "回  飛ばした", skip, flush=True)
    print("完了", n, "回  飛ばした", skip, flush=True)


if __name__ == "__main__":
    main()

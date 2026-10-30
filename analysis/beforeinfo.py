"""公式サイトの直前情報 (展示タイム・チルト・体重・スタート展示の進入コースと ST・風・波) を1日分ずつ集める。

DB (成績・番組表) にはスタート展示が入っていないので、予想に使えるように別に貯める。
1日分を predictions/beforeinfo/YYYYMMDD.csv に書く (取得済みの日は飛ばす)。1 秒に 1 回までしかアクセスしない。

  python analysis/beforeinfo.py --db boatrace.sqlite --date 2026-10-29             1日分
  python analysis/beforeinfo.py --db boatrace.sqlite --backfill-minutes 40 --until 2024-10-01
                                        取得済みの一番古い日の前の日から、40分のあいだ過去にさかのぼって集める
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import live  # noqa: E402

OUT = os.path.join(ROOT, "predictions", "beforeinfo")
COLS = ["race_id", "lane", "racer_no", "weight", "exhibition_time", "tilt", "ex_course", "ex_st", "wind_speed",
        "wave_cm"]


def path(date):
    return os.path.join(OUT, date.replace("-", "") + ".csv")


def races_of(db, date):
    con = sqlite3.connect(db)
    r = con.execute("SELECT race_id, jcd, rno FROM races WHERE race_date = ? ORDER BY race_id", (date,)).fetchall()
    con.close()
    return r


def collect(db, date, limit=None):
    """1日分を集めて保存する。保存した行数を返す (その日のレースが DB に無ければ None)。"""
    races = races_of(db, date)
    if not races:
        return None
    rows, fails = [], 0
    for rid, jcd, rno in races[:limit]:
        try:
            b = live.parse_beforeinfo(live.get("beforeinfo", int(jcd), int(rno), date))
        except Exception as e:  # 中止レースなど
            fails += 1
            if fails >= 20 and not rows:
                raise RuntimeError(f"{date}: 直前情報を取得できません ({e})")
            continue
        for lane, x in b["boats"].items():
            course, st = b["start"].get(lane, (None, None))
            rows.append([rid, lane, x["racer_no"], x["weight"], x["exhibition_time"], x["tilt"], course, st,
                         b["wind_speed"], b["wave_cm"]])
    os.makedirs(OUT, exist_ok=True)
    pd.DataFrame(rows, columns=COLS).to_csv(path(date), index=False)
    print(f"{date}: {len(races)}レース中 {len(rows) // 6}レース分 (取得できなかったもの {fails})", flush=True)
    return len(rows)


def backfill(db, minutes, until):
    """取得済みの一番古い日の前の日から、時間いっぱいまで過去にさかのぼる。"""
    t0 = time.time()
    have = sorted(f[:8] for f in os.listdir(OUT)) if os.path.isdir(OUT) else []
    d = dt.date.fromisoformat(f"{have[0][:4]}-{have[0][4:6]}-{have[0][6:]}") if have else dt.date.today()
    while time.time() - t0 < minutes * 60:
        d -= dt.timedelta(days=1)
        if d.isoformat() < until:
            print(f"{until} までさかのぼりました")
            return
        if not os.path.exists(path(d.isoformat())):
            collect(db, d.isoformat())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "boatrace.sqlite"))
    ap.add_argument("--date", help="この1日を集める (yesterday も可)")
    ap.add_argument("--limit", type=int, help="1日のうち最初の何レースだけ (動作確認用)")
    ap.add_argument("--backfill-minutes", type=float, default=0)
    ap.add_argument("--until", default="2024-10-01", help="さかのぼる一番古い日")
    a = ap.parse_args()
    if a.date:
        d = (dt.date.today() - dt.timedelta(days=1)).isoformat() if a.date == "yesterday" else a.date
        if a.limit or not os.path.exists(path(d)):
            collect(a.db, d, a.limit)
    if a.backfill_minutes:
        backfill(a.db, a.backfill_minutes, a.until)


if __name__ == "__main__":
    main()

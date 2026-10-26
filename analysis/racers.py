"""選手ごとの成績表 (1着率・2連率・3連率・枠別の1着率・平均ST・単勝/複勝の回収率)。

  python analysis/racers.py --db boatrace.sqlite --start 2025-01-01 --out racers.csv
  python analysis/racers.py --db boatrace.sqlite --start 2025-01-01 --racer 4444     (1人だけ表示)
"""
import argparse
import sqlite3

import numpy as np
import pandas as pd


def racer_table(db, start, end="2099-12-31", min_starts=0):
    con = sqlite3.connect(db)
    lo, hi = start.replace("-", ""), end.replace("-", "") + "~"
    res = pd.read_sql_query("SELECT race_id, lane, racer_no, racer_name, course, start_timing, finish, finish_raw "
                            "FROM results WHERE race_id >= ? AND race_id < ?", con, params=(lo, hi))
    pay = pd.read_sql_query("SELECT race_id, bet_type, combination, payout FROM payouts "
                            "WHERE bet_type IN ('単勝', '複勝') AND race_id >= ? AND race_id < ?", con,
                            params=(lo, hi))
    cls = pd.read_sql_query("SELECT racer_no, class, branch, age FROM entries WHERE race_id >= ? AND race_id < ? "
                            "ORDER BY race_id", con, params=(lo, hi)).drop_duplicates("racer_no", keep="last")
    con.close()
    res = res[~res["finish_raw"].fillna("").str.startswith("K")]  # 欠場は出走に数えない
    pay = pay[pay.combination != "特払い"].copy()
    pay["lane"] = pay["combination"].astype(int)
    tan = pay[pay.bet_type == "単勝"].drop_duplicates(["race_id", "lane"]).set_index(["race_id", "lane"])["payout"]
    fuku = pay[pay.bet_type == "複勝"].drop_duplicates(["race_id", "lane"]).set_index(["race_id", "lane"])["payout"]
    key = list(zip(res.race_id, res.lane))
    refund = res["finish_raw"].fillna("").str[0].isin(["F", "L"]).values  # F・L は返還
    res["tan"] = np.where(refund, 100, tan.reindex(key).fillna(0).values)
    res["fuku"] = np.where(refund, 100, fuku.reindex(key).fillna(0).values)
    st = res["start_timing"].map(lambda s: -float(s[1:]) if isinstance(s, str) and s[:1] == "F"
                                 else pd.to_numeric(s, errors="coerce"))
    res["st"] = st
    res["w"], res["t2"], res["t3"] = res.finish == 1, res.finish <= 2, res.finish <= 3
    res["f"] = res["finish_raw"].fillna("").str.startswith("F")
    g = res.groupby("racer_no")
    t = pd.DataFrame({
        "名前": g["racer_name"].last(), "出走": g.size(), "1着率": g["w"].mean(), "2連率": g["t2"].mean(),
        "3連率": g["t3"].mean(), "平均ST": g["st"].mean(), "フライング": g["f"].sum(),
        "単勝回収率": g["tan"].sum() / (100 * g.size()), "複勝回収率": g["fuku"].sum() / (100 * g.size()),
    })
    by_lane = res.pivot_table(index="racer_no", columns="lane", values="w", aggfunc=["mean", "size"])
    for l in range(1, 7):
        t[f"{l}号艇 1着率"] = by_lane[("mean", l)] if ("mean", l) in by_lane else np.nan
        t[f"{l}号艇 出走"] = by_lane[("size", l)].fillna(0).astype(int) if ("size", l) in by_lane else 0
    t = t.join(cls.set_index("racer_no").rename(columns={"class": "級別", "branch": "支部", "age": "年齢"}))
    t = t[t["出走"] >= min_starts]
    cols = ["名前", "級別", "支部", "年齢", "出走", "1着率", "2連率", "3連率", "平均ST", "フライング", "単勝回収率", "複勝回収率"] + \
        [f"{l}号艇 {k}" for l in range(1, 7) for k in ("1着率", "出走")]
    return t[cols].sort_values("1着率", ascending=False)


def show(t, racer_no):
    if racer_no not in t.index:
        print("見つかりません:", racer_no)
        return
    r = t.loc[racer_no]
    print(f"{racer_no} {r['名前']}  {r['級別']} {r['支部']} {r['年齢']}歳")
    print(f"  出走 {r['出走']}  1着率 {r['1着率']:.1%}  2連率 {r['2連率']:.1%}  3連率 {r['3連率']:.1%}  "
          f"平均ST {r['平均ST']:.2f}  F {r['フライング']}")
    print(f"  単勝回収率 {r['単勝回収率']:.1%}  複勝回収率 {r['複勝回収率']:.1%}")
    print("  枠別 1着率: " + "  ".join(f"{l}号艇 {r[f'{l}号艇 1着率']:.0%}({r[f'{l}号艇 出走']})"
                                    for l in range(1, 7) if r[f"{l}号艇 出走"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="boatrace.sqlite")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2099-12-31")
    ap.add_argument("--min-starts", type=int, default=30)
    ap.add_argument("--racer", type=int)
    ap.add_argument("--out", default="racers.csv")
    a = ap.parse_args()
    t = racer_table(a.db, a.start, a.end, 0 if a.racer else a.min_starts)
    if a.racer:
        show(t, a.racer)
        return
    t.to_csv(a.out, encoding="utf-8-sig", float_format="%.4f")
    print(f"{len(t)}人 → {a.out}")


if __name__ == "__main__":
    main()

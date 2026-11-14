"""追加の情報を足すと予想の当たり方・回収率がどれだけ変わるかを、同じレース・同じ買い方で比べる (展示タイムの検証と同じ方法)。

  python analysis/extra/extra_bt.py --db boatrace.sqlite --years 5 --set morning --out extra
  python analysis/extra/extra_bt.py --db boatrace.sqlite --years 2 --set late --bi beforeinfo.csv.gz --out extra_late

  - DB の最終日から1年ずつさかのぼって --years 年分を、その1年より前の --days 日だけで学習して予想する
  - morning (朝から使える): なし = 今の予想 (V4_FEATS) と、それにモーター・ボート・今節・当地・体重 を1つずつ足したもの、全部足したもの
  - late (直前): 展示 = なし + 展示タイム (前の検証の「展示」) と、それにスタート展示・チルト・当日体重・部品交換 を1つずつ足したもの、全部
  - 単勝の期待値に使うオッズのモデルは、なし・展示とも同じ特徴量 (勝率のモデルとそろえる)
  - 出力のファイルは tenji.py と同じ形 (<年>_<モデル>_check.csv など)。まとめは summary.py
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import feats  # noqa: E402
from predict import (V4_FEATS, Book, Predictor, build_strategies, features, fit_odds, load,  # noqa: E402
                     metrics, simulate, tickets_from_probs, win_payouts)
import yosou  # noqa: E402
from yosou import HISTORY_DAYS, compare, kaime_rows, last_date, pred_table  # noqa: E402

EX = ["exhibition_time", "ex_d", "ex_rk"]


def groups_morning(new):
    g = {"モーター": [c for c in new if c.startswith("motor")],
         "ボート": [c for c in new if c.startswith("boat")],
         "今節": [c for c in new if c.startswith("meet")],
         "当地": [c for c in new if c.startswith("venue")],
         "体重": ["weight_d"]}
    g["朝全部"] = [c for v in g.values() for c in v]
    return {"なし": V4_FEATS} | {k: V4_FEATS + v for k, v in g.items()}


def groups_late(late):
    base = V4_FEATS + EX
    g = {"スタート展示": late["sx"], "チルト": late["tilt"], "当日体重": late["wday"], "部品交換": late["parts"]}
    g["直前全部"] = [c for v in g.values() for c in v]
    return {"なし": V4_FEATS, "展示": base} | {k: base + v for k, v in g.items()}


def periods(end, years):
    for i in range(years, 0, -1):
        yield end - dt.timedelta(days=365 * i - 1), end - dt.timedelta(days=365 * (i - 1))


def build(a, start, end):
    raw, pay = load(a.db, start.isoformat(), end.isoformat())
    raw.loc[raw["exhibition_time"] <= 0, "exhibition_time"] = np.nan  # 0.00 は記録なし
    print("features", flush=True)
    df = features(raw).merge(win_payouts(pay), on=["race_id", "lane"], how="left")
    del raw, pay
    df = df.astype({c: "float32" for c in df.columns if df[c].dtype == "float64" and c != "payout"})  # メモリを半分に
    df = feats.add_meta(a.db, df)
    new = feats.add(df)
    df = df.copy()
    keep = ["race_id", "lane", "race_date", "win", "finish", "weight", "series_avg", "loc_win_rate", "venue_skill",
            "motor_2_rate", "boat_2_rate", "exhibition_time", "ex_d", "st_avg"] + new
    df[keep].to_pickle(os.path.join(a.out, f"corr_{start}_{end}.pkl"))  # corr.py 用
    return df, new


def run(a):
    end = dt.date.fromisoformat(last_date(a.db))
    ps = list(periods(end, a.years))
    start = ps[0][0] - dt.timedelta(days=a.days + HISTORY_DAYS)
    print(f"読み込み {start}〜{end}", flush=True)
    df, new = build(a, start, end)
    if a.set == "morning":
        models = groups_morning(new)
    else:
        bi = pd.read_csv(a.bi)
        late = feats.add_late(df, bi)
        models = groups_late(late)
    if a.models:
        models = {k: v for k, v in models.items() if k in a.models.split(",")}
    _, pay = load(a.db, ps[0][0].isoformat(), end.isoformat())
    con = sqlite3.connect(a.db)
    venue = dict(con.execute("SELECT jcd, name FROM venues").fetchall())
    con.close()
    for t0, t1 in ps:
        warm, val = t0 - dt.timedelta(days=a.days), t0 - dt.timedelta(days=30)
        d = df.race_date
        tr = df[(d >= warm.isoformat()) & (d < val.isoformat())]
        va = df[(d >= val.isoformat()) & (d < t0.isoformat())]
        te = df[(d >= t0.isoformat()) & (d <= t1.isoformat())]
        if a.set == "late":  # 直前情報がそろっているレースだけ (学習・予想とも)
            ok = df.groupby("race_id")["sx_course"].transform(lambda s: s.notna().all())
            tr, va, te = tr[ok.loc[tr.index]], va[ok.loc[va.index]], te[ok.loc[te.index]]
        print(f"\n===== 予想 {t0}〜{t1} {te.race_id.nunique()}R / 学習 {tr.race_id.nunique()}R / 検証 {va.race_id.nunique()}R",
              flush=True)
        con = sqlite3.connect(a.db)
        info = pd.read_sql_query("SELECT race_id, jcd, rno, deadline, technique, wind_speed, wave_cm FROM races "
                                 "WHERE race_date >= ? AND race_date <= ?", con,
                                 params=(t0.isoformat(), t1.isoformat())).set_index("race_id")
        names = pd.read_sql_query("SELECT race_id, lane, racer_name FROM entries WHERE race_id >= ? AND race_id < ?",
                                  con, params=(f"{t0:%Y%m%d}", f"{t1:%Y%m%d}~")).set_index(["race_id", "lane"])["racer_name"]
        con.close()
        p = pay[pay.race_id.isin(te.race_id.unique())]
        res = te[["race_id", "lane", "racer_no", "finish_raw", "finish", "course", "start_timing", "exhibition_time"]]
        for m, fs in models.items():
            tag = os.path.join(a.out, f"{t0:%Y%m%d}_{m}")
            if os.path.exists(tag + "_check.csv"):
                print(f"{m}: 済み", flush=True)
                continue
            t = dt.datetime.now()
            om = fit_odds(tr, va, fs)
            pr = Predictor(fs, "v3").fit(tr, va)
            yosou.V4_FEATS = fs  # pred_table の期待値はこのオッズのモデルの特徴量で出す
            pred = pred_table(pr, te, info, names, venue, om).set_index("race_id")
            yosou.V4_FEATS = V4_FEATS
            pred.reset_index().to_csv(tag + "_pred.csv", index=False, encoding="utf-8-sig")
            r = res[res.race_id.isin(pred.index)]
            compare(pred, info, r, p).to_csv(tag + "_check.csv", index=False, encoding="utf-8-sig")
            kaime_rows(pred, p, r).to_csv(tag + "_kaime.csv", index=False, encoding="utf-8-sig")
            P, Q = pr.probs(te)
            ex, tri = pr.orders(P, Q)
            tk = tickets_from_probs(P.values, ex, tri)
            st = simulate(Book(p, te), list(P.index), tk, build_strategies(tk, P.values))
            st.insert(0, "model", m)
            st.to_csv(tag + "_strategies.csv", index=False, encoding="utf-8-sig")
            mt = metrics(P, tri, te) | dict(lam2=pr.lam2, lam3=pr.lam3, iters=pr.win.best_iteration)
            pd.DataFrame([mt]).to_csv(tag + "_metrics.csv", index=False)
            imp = pd.Series(pr.win.feature_importance("gain"), index=fs)
            (imp / imp.sum()).sort_values(ascending=False).to_csv(tag + "_importance.csv")
            print(f"{m}: {(dt.datetime.now() - t).seconds // 60}分 本命1着 {mt['win_top1']:.2%} "
                  f"3連単1点目 {mt['tri_top1']:.2%} logloss {mt['win_logloss']:.4f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--set", choices=["morning", "late"], default="morning")
    ap.add_argument("--bi", help="parse_beforeinfo.py の出力 (late のとき)")
    ap.add_argument("--models", help="この名前のモデルだけ (カンマ区切り)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    run(a)

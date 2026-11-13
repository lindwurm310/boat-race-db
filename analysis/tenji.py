"""展示タイムを足すと予想の当たり方・回収率がどれだけ変わるかを、同じレース・同じ買い方で比べる。

  python analysis/tenji.py --db boatrace.sqlite --years 2 --out tenji

  - DB の最終日から1年ずつさかのぼって --years 年分を、その1年より前の --days 日だけで学習して予想する
    (yosou.py backtest と同じ分け方。テストの1年は学習にも早期打ち切りにも使わない)
  - 比べるモデル (どれも v3 の学習方法・同じ学習データ・同じオッズのモデル)
      なし: 今の予想 (V4_FEATS、番組表と過去の成績だけ)
      展示: なし + 展示タイム・展示タイムの1位との差・展示タイムの順位
      直前: 展示 + 風速・波高 (live で使っている V4_LATE。参考)
      展示o: 展示 で、単勝の期待値に使うオッズの予想にも展示タイムを入れる
  - 買い方は毎日の答え合わせ (check) と同じ: 本命・予想着順・3連単上位5点・単勝の期待値買い・kaime.py の買い方。
    ほかに predict.py の券種ごとの買い方 (上位k点・本命の確率で絞る など) も出す
  - レースごとの結果は out/<年>_<モデル>_check.csv・_kaime.csv、まとめは out/summary.csv と out/strategies.csv
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kaime  # noqa: E402
from predict import (V4_FEATS, V4_LATE, Book, Predictor, build_strategies, features, fit_odds, load,  # noqa: E402
                     metrics, simulate, tickets_from_probs, win_payouts)
import yosou  # noqa: E402
from yosou import HISTORY_DAYS, compare, kaime_rows, last_date, pred_table  # noqa: E402

EX = ["exhibition_time", "ex_d", "ex_rk"]
# 名前: (勝率のモデルの特徴量, 単勝の期待値に使うオッズのモデルの特徴量)
MODELS = {"なし": (V4_FEATS, V4_FEATS), "展示": (V4_FEATS + EX, V4_FEATS), "直前": (V4_LATE, V4_FEATS),
          # オッズのモデルにも展示タイムを入れる (締切のオッズは展示を見た人が買った結果なので)
          "展示o": (V4_FEATS + EX, V4_FEATS + EX)}


def periods(end, years):
    for i in range(years, 0, -1):
        yield end - dt.timedelta(days=365 * i - 1), end - dt.timedelta(days=365 * (i - 1))


def run(a):
    end = dt.date.fromisoformat(last_date(a.db))
    ps = list(periods(end, a.years))
    start = ps[0][0] - dt.timedelta(days=a.days + HISTORY_DAYS)
    print(f"読み込み {start}〜{end}", flush=True)
    raw, pay = load(a.db, start.isoformat(), end.isoformat())
    raw.loc[raw["exhibition_time"] <= 0, "exhibition_time"] = np.nan  # 0.00 は記録なし
    df = features(raw).merge(win_payouts(pay), on=["race_id", "lane"], how="left")
    con = sqlite3.connect(a.db)
    venue = dict(con.execute("SELECT jcd, name FROM venues").fetchall())
    con.close()
    models = [m for m in a.models.split(",")]
    for t0, t1 in ps:
        warm, val = t0 - dt.timedelta(days=a.days), t0 - dt.timedelta(days=30)
        d = df.race_date
        tr = df[(d >= warm.isoformat()) & (d < val.isoformat())]
        va = df[(d >= val.isoformat()) & (d < t0.isoformat())]
        te = df[(d >= t0.isoformat()) & (d <= t1.isoformat())]
        print(f"\n===== 予想 {t0}〜{t1} {te.race_id.nunique()}R / 学習 {warm}〜{val} {tr.race_id.nunique()}R / "
              f"検証 {va.race_id.nunique()}R", flush=True)
        oms = {}
        con = sqlite3.connect(a.db)
        info = pd.read_sql_query("SELECT race_id, jcd, rno, deadline, technique, wind_speed, wave_cm FROM races "
                                 "WHERE race_date >= ? AND race_date <= ?", con,
                                 params=(t0.isoformat(), t1.isoformat())).set_index("race_id")
        names = pd.read_sql_query("SELECT race_id, lane, racer_name FROM entries WHERE race_id >= ? AND race_id < ?",
                                  con, params=(f"{t0:%Y%m%d}", f"{t1:%Y%m%d}~")).set_index(["race_id", "lane"])["racer_name"]
        con.close()
        for m in models:
            tag = os.path.join(a.out, f"{t0:%Y%m%d}_{m}")
            if os.path.exists(tag + "_check.csv"):
                print(f"{m}: 済み", flush=True)
                continue
            feats, ofeats = MODELS[m]
            key = tuple(ofeats)
            if key not in oms:  # オッズ (買う人の評価) のモデルは特徴量が同じなら同じものを使う
                oms[key] = fit_odds(tr, va, ofeats)
            t = dt.datetime.now()
            pr = Predictor(feats, "v3").fit(tr, va)
            yosou.V4_FEATS = ofeats  # pred_table の期待値はこのオッズのモデルの特徴量で出す
            pred = pred_table(pr, te, info, names, venue, oms[key]).set_index("race_id")
            yosou.V4_FEATS = V4_FEATS
            res = raw[raw.race_id.isin(pred.index)]
            p = pay[pay.race_id.isin(pred.index)]
            compare(pred, info, res, p).to_csv(tag + "_check.csv", index=False, encoding="utf-8-sig")
            kaime_rows(pred, p, res).to_csv(tag + "_kaime.csv", index=False, encoding="utf-8-sig")
            P, Q = pr.probs(te)
            ex, tri = pr.orders(P, Q)
            tk = tickets_from_probs(P.values, ex, tri)
            st = simulate(Book(p, te), list(P.index), tk, build_strategies(tk, P.values))
            st.insert(0, "model", m)
            st.to_csv(tag + "_strategies.csv", index=False, encoding="utf-8-sig")
            mt = metrics(P, tri, te) | dict(lam2=pr.lam2, lam3=pr.lam3, iters=pr.win.best_iteration)
            pd.DataFrame([mt]).to_csv(tag + "_metrics.csv", index=False)
            print(f"{m}: {(dt.datetime.now() - t).seconds // 60}分 本命1着 {mt['win_top1']:.2%} "
                  f"3連単1点目 {mt['tri_top1']:.2%} logloss {mt['win_logloss']:.4f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--models", default="なし,展示,直前,展示o")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    run(a)

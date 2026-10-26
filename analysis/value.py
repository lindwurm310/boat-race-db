"""単勝の期待値 (予想勝率 × 予想オッズ) で買う方法の検証。

  python analysis/value.py --db boatrace.sqlite --features feat.pkl --pred pred_dir \
      --odds-train 2022-01-01 --start 2025-01-01 --split 2026-01-01 --out out

  - pred_dir/P.pkl: start 以降の予想勝率 (start より前のデータだけで学習したモデルの出力)
  - オッズの予想モデルは [odds-train, start) の「1着になった艇の単勝払戻」で学習する
  - start 以降を split で「発見」「確認」に分けて、期待値の下限ごとの回収率を出す
"""
import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from predict import V4_FEATS, fit_odds, win_payouts  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--odds-train", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    df = pd.read_pickle(a.features)
    df = df[df.race_date >= a.odds_train]
    pay = pd.read_sql_query("SELECT * FROM payouts WHERE bet_type = '単勝' AND race_id >= ?", sqlite3.connect(a.db),
                            params=(a.odds_train.replace("-", ""),))
    df = df.merge(win_payouts(pay), on=["race_id", "lane"], how="left")
    hist = df[df.race_date < a.start]
    val_start = (pd.Timestamp(a.start) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    om = fit_odds(hist[hist.race_date < val_start], hist[hist.race_date >= val_start], V4_FEATS)
    te = df[df.race_date >= a.start].copy()
    P = pd.read_pickle(f"{a.pred}/P.pkl")
    te["p"] = P.stack().reindex(list(zip(te.race_id, te.lane))).values
    te["odds_hat"] = np.exp(om.predict(te[V4_FEATS], num_iteration=om.best_iteration))
    te["ev"] = te.p * te.odds_hat
    te["ret"] = te.payout.fillna(0)
    te.loc[te.refund, "ret"] = 100  # 欠場・F・L は返還
    te = te[te.p.notna() & (te.groupby("race_id").payout.transform("max") > 0)]
    te["期間"] = np.where(te.race_date < a.split, "発見", "確認")
    rows = []
    rng = np.random.default_rng(0)
    for th in [0.8, 0.9, 1.0, 1.1, 1.2, 1.3]:
        for per in ["発見", "確認", "全期間"]:
            x = te[(te.ev >= th) & ((te["期間"] == per) | (per == "全期間"))]
            g = x.groupby("race_id").ret.agg(["sum", "size"]).values
            bs = [g[i, 0].sum() / (100 * g[i, 1].sum()) for i in (rng.integers(0, len(g), len(g)) for _ in range(1000))]
            rows.append(dict(期待値以上=th, 期間=per, 点数=len(x), 的中率=(x.ret > 100).mean(),
                             回収率=x.ret.sum() / (100 * len(x)), 下限95=np.percentile(bs, 2.5),
                             上限95=np.percentile(bs, 97.5)))
    r = pd.DataFrame(rows)
    r.to_csv(f"{a.out}/value.csv", index=False, encoding="utf-8-sig")
    te[["race_id", "lane", "racer_no", "class", "p", "odds_hat", "ev", "ret", "期間"]].to_csv(
        f"{a.out}/value_bets.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 200)
    print(r.round(3).to_string(index=False))


if __name__ == "__main__":
    main()

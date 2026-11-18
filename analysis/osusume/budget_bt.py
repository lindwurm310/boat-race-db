"""毎日決まった予算 (例 1,000円) で単勝を買うとき、何点・いくらずつ・どの条件で買うと回収率が一番高いかの検証。

  python analysis/osusume/budget_bt.py --bt out --budget 1000 --out analysis/osusume

  - --bt: extra-backtest の結果 (リリース bi-history の extra_morning_*.tar.gz を展開した out/)。
    その年より前のデータだけで学習したモデルの予想 (<年>_朝全部_pred.csv) と答え合わせ (<年>_朝全部_check.csv) を使う
  - 期待値 = 1着確率 × 予想オッズ。期待値と1着確率の下限を満たす艇を、その日の中で期待値の高い順に N 点選び、
    予算を N で割った金額 (100円単位) ずつ買う。自動投票は朝 9:30 に動くので、締切 9:40 より前のレースは除く
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd


def boats(bt):
    rows = []
    for f in sorted(glob.glob(os.path.join(bt, "*_朝全部_pred.csv"))):
        p = pd.read_csv(f, encoding="utf-8-sig")
        c = pd.read_csv(f.replace("_pred", "_check"), encoding="utf-8-sig")[["race_id", "結果", "単勝配当"]]
        m = p.merge(c, on="race_id")
        m = m[m["結果"].notna() & m["単勝配当"].notna()]
        for l in range(1, 7):
            rows.append(pd.DataFrame({"race_id": m.race_id, "dl": m["締切"].astype(str), "p": m[f"{l}号艇"],
                                      "o": m[f"{l}号艇の予想オッズ"], "pay": m["単勝配当"],
                                      "win": m["結果"].astype(str).str.split("-").str[0] == str(l)}))
    b = pd.concat(rows)
    b = b[b.p.notna() & b.o.notna()]
    b["date"] = pd.to_datetime(b.race_id.str[:8])
    b["ev"] = b.p * b.o
    b["ret"] = np.where(b.win, b.pay, 0.0)
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", required=True)
    ap.add_argument("--budget", type=int, default=1000)
    ap.add_argument("--first", default="09:40", help="これより前に締め切るレースは買わない")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    b = boats(a.bt)
    b = b[b.dl > a.first]
    rows = []
    for ev in (1.0, 1.1, 1.2, 1.3):
        for pm in (0.0, 0.05, 0.1, 0.15, 0.2):
            x = b[(b.ev >= ev) & (b.p >= pm)].sort_values(["date", "ev"], ascending=[True, False])
            rk = x.groupby("date").cumcount()
            for n in (1, 2, 3, 4, 5, 10):
                unit = a.budget // n // 100 * 100
                t = x[rk < n].copy()
                t["got"] = t.ret * unit / 100
                d = t.groupby("date").agg(n=("got", "size"), got=("got", "sum"))
                d["cost"] = d.n * unit
                m = d.groupby(d.index.to_period("M")).agg(n=("n", "sum"), cost=("cost", "sum"), got=("got", "sum"))
                months = len(m)
                m = m[m.n >= n * 25]  # ほぼ1か月ある月だけ
                m["r"] = m.got / m.cost
                y = d.groupby(d.index.year).agg(cost=("cost", "sum"), got=("got", "sum"))
                pl = (d.got - d.cost).cumsum()
                rows.append({"期待値以上": ev, "1着確率以上": pm, "点数": n, "1点の金額": unit, "1日の金額": unit * n,
                             "日数": len(d), "的中率": t.win.mean(), "回収率": d.got.sum() / d.cost.sum(),
                             "月平均の収支": (d.got - d.cost).sum() / months, "プラスの月": (m.r >= 1).mean(),
                             "一番悪い月": m.r.min(), "一番良い月": m.r.max(), "最大のへこみ(円)": (pl.cummax() - pl).max(),
                             **{f"{k}年": v for k, v in (y.got / y.cost).items()}})
    r = pd.DataFrame(rows).sort_values("回収率", ascending=False)
    os.makedirs(a.out, exist_ok=True)
    r.to_csv(os.path.join(a.out, f"budget_{a.budget}.csv"), index=False, encoding="utf-8-sig")
    print(r.head(15).round(3).to_string())


if __name__ == "__main__":
    main()

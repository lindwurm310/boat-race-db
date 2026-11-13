"""tenji.py の結果をまとめる: モデルごとの的中率・回収率と、「なし」との差 (同じレースでの差の 95% 区間)。

  python analysis/tenji_summary.py --out tenji
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kaime  # noqa: E402

BASE = "なし"


def per_race(out, m):
    """モデル m のレースごとの (買い方 → 投資, 払戻, 的中) の表。全年をつなげる。"""
    rows = []
    for c in sorted(glob.glob(os.path.join(out, f"*_{m}_check.csv"))):
        ck = pd.read_csv(c, encoding="utf-8-sig").set_index("race_id")
        km = pd.read_csv(c.replace("_check", "_kaime"), encoding="utf-8-sig").set_index("race_id")
        d = pd.DataFrame(index=ck.index)
        d["年"] = os.path.basename(c)[:8]
        d["本命1着 的中"] = ck["本命1着"].astype(int)
        d["本命3着以内 的中"] = ck["本命3着以内"].astype(int)
        d["予想2着 的中"] = np.nan
        d["単勝 本命1点 投資"] = 100
        d["単勝 本命1点 払戻"] = np.where(ck["本命1着"], ck["単勝配当"].fillna(0), 0)
        d["単勝 本命1点 的中"] = ck["本命1着"].astype(int)
        d["単勝 期待値1以上 投資"] = 100 * ck["単勝点数"]
        d["単勝 期待値1以上 払戻"] = ck["単勝払戻"]
        d["単勝 期待値1以上 的中"] = ck["単勝的中"]
        for bet, name, _ in kaime.PATTERNS:
            k = kaime.key(bet, name)
            d[f"{k} 投資"] = 100 * km[k + " 点数"].reindex(d.index)
            d[f"{k} 払戻"] = km[k + " 払戻"].reindex(d.index)
            d[f"{k} 的中"] = km[k + " 的中"].reindex(d.index)
        rows.append(d)
    return pd.concat(rows).dropna(axis=1, how="all")


def buys(d):
    return [c[:-3] for c in d.columns if c.endswith(" 投資")]


def boot(d0, d1, b, n=1000):
    """同じレースを復元抽出して、回収率の差 (d1 − d0) の 95% 区間。"""
    rng = np.random.default_rng(0)
    s0, r0, s1, r1 = (x[f"{b} {c}"].fillna(0).values for x in (d0, d1) for c in ("投資", "払戻"))
    ok = (s0 > 0) | (s1 > 0)
    s0, r0, s1, r1 = s0[ok], r0[ok], s1[ok], r1[ok]
    diffs = []
    for _ in range(n):
        i = rng.integers(0, len(s0), len(s0))
        a, c = s0[i].sum(), s1[i].sum()
        if a and c:
            diffs.append(r1[i].sum() / c - r0[i].sum() / a)
    return np.percentile(diffs, [2.5, 97.5])


def table(out, models):
    data = {m: per_race(out, m) for m in models}
    common = sorted(set.intersection(*(set(d.index) for d in data.values())))
    data = {m: d.loc[common] for m, d in data.items()}
    rows = []
    for col in ["本命1着", "本命3着以内"]:
        r = {"買い方": col, "指標": "的中率"}
        for m in models:
            r[m] = data[m][f"{col} 的中"].mean()
        rows.append(r)
    for b in buys(data[BASE]):
        for m in models:
            d = data[m]
            buy = d[f"{b} 投資"] > 0
            rows.append(dict(買い方=b, model=m, レース=int(buy.sum()), 点数=d[f"{b} 投資"].sum() / 100,
                             的中率=d.loc[buy, f"{b} 的中"].mean(),
                             回収率=d[f"{b} 払戻"].sum() / d[f"{b} 投資"].sum()))
    long = pd.DataFrame(rows[2:])
    head = pd.DataFrame(rows[:2])
    out_rows = []
    for b, g in long.groupby("買い方", sort=False):
        g = g.set_index("model")
        r = {"買い方": b}
        for m in models:
            r[f"{m} 買ったレース"] = int(g.at[m, "レース"])
            r[f"{m} 的中率"] = g.at[m, "的中率"]
            r[f"{m} 回収率"] = g.at[m, "回収率"]
        for m in models:
            if m == BASE:
                continue
            lo, hi = boot(data[BASE], data[m], b)
            r[f"{m}-なし 的中率差"] = g.at[m, "的中率"] - g.at[BASE, "的中率"]
            r[f"{m}-なし 回収率差"] = g.at[m, "回収率"] - g.at[BASE, "回収率"]
            r[f"{m}-なし 回収率差 95%下"] = lo
            r[f"{m}-なし 回収率差 95%上"] = hi
        out_rows.append(r)
    return head, pd.DataFrame(out_rows), len(common)


def by_year(out, models):
    rows = []
    for m in models:
        d = per_race(out, m)
        for y, g in d.groupby("年"):
            r = dict(model=m, 年=y, レース=len(g), 本命1着=g["本命1着 的中"].mean())
            for b in ["単勝 期待値1以上", "3連単 確率の上位5点", "3連単 ◎-○▲-○▲△ (4点)", "3連複 ◎-○▲△ (3点)"]:
                r[f"{b} 回収率"] = g[f"{b} 払戻"].sum() / g[f"{b} 投資"].sum()
            rows.append(r)
    return pd.DataFrame(rows)


def metrics(out, models):
    rows = []
    for m in models:
        for f in sorted(glob.glob(os.path.join(out, f"*_{m}_metrics.csv"))):
            rows.append(pd.read_csv(f).assign(model=m, 年=os.path.basename(f)[:8]))
    return pd.concat(rows)


def strategies(out, models):
    s = pd.concat(pd.read_csv(f, encoding="utf-8-sig").assign(年=os.path.basename(f)[:8])
                  for m in models for f in glob.glob(os.path.join(out, f"*_{m}_strategies.csv")))
    s["stake"] = s["races"] * s["tickets_per_race"] * 100
    s["ret"] = s["stake"] * s["return_rate"]
    s["hits"] = s["races"] * s["hit_rate"]
    g = s.groupby(["strategy", "model"], sort=False)[["races", "stake", "ret", "hits"]].sum()
    g["的中率"] = g["hits"] / g["races"]
    g["回収率"] = g["ret"] / g["stake"]
    w = g[["races", "的中率", "回収率"]].unstack("model")
    w.columns = [f"{b} {a}" for a, b in w.columns]
    return w.reset_index()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default="なし,展示,直前,展示o")
    a = ap.parse_args()
    models = [m for m in a.models.split(",") if glob.glob(os.path.join(a.out, f"*_{m}_check.csv"))]
    head, t, n = table(a.out, models)
    t.to_csv(os.path.join(a.out, "summary.csv"), index=False, encoding="utf-8-sig")
    by_year(a.out, models).to_csv(os.path.join(a.out, "by_year.csv"), index=False, encoding="utf-8-sig")
    mt = metrics(a.out, models)
    mt.to_csv(os.path.join(a.out, "metrics.csv"), index=False, encoding="utf-8-sig")
    strategies(a.out, models).to_csv(os.path.join(a.out, "strategies.csv"), index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250, "display.max_rows", 200, "display.max_columns", 40,
                  "display.unicode.east_asian_width", True)
    print(f"共通のレース {n}")
    print(head.to_string(index=False))
    print(mt.groupby("model")[["win_top1", "pos2_hit", "pos3_hit", "tri_top1", "win_logloss", "tri_logloss"]].mean())
    print(t.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

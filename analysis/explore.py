"""全組み合わせから回収率の高い買い方を探す (2010〜2026年)。

  買い目: 枠番で決まる 212 通り (単勝・複勝 6、2連単 30、2連複・拡連複 15、3連単 120、3連複 20)
  条件  : 買う前に分かるもの。場・レース番号・レースの種類・風・波・各枠の級別・全国勝率の順位・
          モーターの順位・展示タイムの順位・A1 の人数。1つの条件と、2つの条件の組み合わせ
  期間  : 発見 2010〜2019 / 確認 2020〜2022 / 本番 2023〜2026。発見で 100% を超えたものが
          あとの2期間でも超えるかを見る。条件をでたらめに入れ替えた「偽の条件」で同じことをして、
          偶然でどのくらい生き残るかと比べる

  python analysis/explore.py --db boatrace.sqlite --out explore
"""
import argparse
import itertools
import os
import sqlite3
import time

import numpy as np
import pandas as pd
from scipy import sparse

BETS = {"単勝": 1, "複勝": 1, "２連単": 2, "２連複": 2, "拡連複": 2, "３連単": 3, "３連複": 3}
ORDERED = {"単勝", "複勝", "２連単", "３連単"}
PERIODS = {"発見": (2010, 2019), "確認": (2020, 2022), "本番": (2023, 2026)}


def tickets():
    out = []
    for bet, k in BETS.items():
        it = itertools.permutations(range(1, 7), k) if bet in ORDERED else itertools.combinations(range(1, 7), k)
        for c in it:
            out.append((bet, "-".join(map(str, c)), set(c)))
    return out


def load(db, start):
    con = sqlite3.connect(db)
    lo = start.replace("-", "")
    q = lambda s: pd.read_sql_query(s, con, params=(lo,))
    races = q("SELECT race_id, race_date, jcd, rno, race_type, wind_speed, wave_cm FROM races WHERE race_id >= ?")
    ent = q("SELECT race_id, lane, class, nat_win_rate, motor_2_rate FROM entries WHERE race_id >= ?")
    res = q("SELECT race_id, lane, exhibition_time, finish_raw, finish FROM results WHERE race_id >= ?")
    pay = q("SELECT race_id, bet_type, combination, payout FROM payouts WHERE race_id >= ?")
    con.close()
    return races, ent, res, pay


def build(db, start):
    races, ent, res, pay = load(db, start)
    # 結果があり、6艇そろったレースだけ。特払い (全艇返還など) のレースは除く
    ok = res.groupby("race_id").size() == 6
    sp = set(pay.loc[pay.combination == "特払い", "race_id"])
    rids = races.loc[races.race_id.isin(ok[ok].index) & ~races.race_id.isin(sp)].sort_values("race_id")
    rids = rids[rids.race_id.isin(set(pay.race_id))].reset_index(drop=True)
    idx = pd.Series(np.arange(len(rids)), index=rids.race_id)
    N = len(rids)
    tk = tickets()
    col = {(b, c): i for i, (b, c, _) in enumerate(tk)}
    R = np.zeros((N, len(tk)), np.float32)
    p = pay[pay.race_id.isin(idx.index) & pay.bet_type.isin(BETS)].copy()
    p["c"] = [col.get((b, c), -1) for b, c in zip(p.bet_type, p.combination)]
    p = p[p.c >= 0]
    R[idx[p.race_id].values, p.c.values] = p.payout.values
    # 欠場・F・L の艇を含む買い目は返還 (100円)
    fr = res[res.race_id.isin(idx.index)]
    ref = fr[fr["finish_raw"].fillna("").str[0].isin(["K", "F", "L"])]
    for rid, g in ref.groupby("race_id"):
        lanes = set(g.lane.astype(int))
        n = idx[rid]
        for i, (_, _, s) in enumerate(tk):
            if s & lanes:
                R[n, i] = 100
    # 条件
    F = pd.DataFrame(index=np.arange(N))
    F["年"] = rids.race_date.str[:4].astype(int).values
    F["場"] = rids.jcd.astype(int).map(lambda j: f"{j:02d}").values
    F["R"] = rids.rno.astype(int).map(lambda r: f"{r:02d}R").values
    rt = rids.race_type.fillna("")
    F["種類"] = np.select([rt.str.contains("優勝"), rt.str.contains("準優"), rt.str.contains("固定"),
                         rt.str.contains("予選"), rt.str.contains("一般")],
                        ["優勝戦", "準優勝戦", "進入固定", "予選", "一般"], "選抜・特別").astype(object)
    F.loc[rt.str.contains("準優").values, "種類"] = "準優勝戦"
    F["風"] = pd.cut(rids.wind_speed.values, [-1, 1, 3, 5, 99], labels=["0-1m", "2-3m", "4-5m", "6m以上"]).astype(str)
    F["波"] = pd.cut(rids.wave_cm.values, [-1, 2, 5, 999], labels=["0-2cm", "3-5cm", "6cm以上"]).astype(str)
    e = ent[ent.race_id.isin(idx.index)].drop_duplicates(["race_id", "lane"]).copy()
    e["n"] = idx[e.race_id].values
    e["wr_rk"] = e.groupby("race_id")["nat_win_rate"].rank(ascending=False, method="min")
    e["mo_rk"] = e.groupby("race_id")["motor_2_rate"].rank(ascending=False, method="min")
    x = fr.copy()
    x["n"] = idx[x.race_id].values
    x["ex_rk"] = x.groupby("race_id")["exhibition_time"].rank(method="min")
    rk = lambda v: np.select([v == 1, v <= 3, v <= 6], ["1位", "2-3位", "4-6位"], "不明")
    for lane in range(1, 7):
        el = e[e.lane == lane].set_index("n")
        xl = x[x.lane == lane].set_index("n")
        F[f"{lane}号艇の級"] = el["class"].reindex(F.index).fillna("不明").values
        F[f"{lane}号艇の勝率順位"] = rk(el["wr_rk"].reindex(F.index).fillna(9).values)
        F[f"{lane}号艇のモーター順位"] = rk(el["mo_rk"].reindex(F.index).fillna(9).values)
        F[f"{lane}号艇の展示順位"] = rk(xl["ex_rk"].reindex(F.index).fillna(9).values)
    a1 = e[e["class"] == "A1"].groupby("n").size().reindex(F.index).fillna(0).astype(int)
    F["A1の人数"] = np.minimum(a1.values, 3).astype(str)
    F.loc[F["A1の人数"] == "3", "A1の人数"] = "3人以上"
    return F, R, tk


def cell_sums(codes, years, R, R2, L):
    """条件の値 × 年 ごとに、各買い目の (レース数, 払戻合計, 払戻の2乗合計)。"""
    y0 = years - 2010
    Y = 17
    key = codes * Y + y0
    M = sparse.csr_matrix((np.ones(len(key), np.float32), (key, np.arange(len(key)))), shape=(L * Y, len(key)))
    n = np.asarray(M.sum(1)).ravel().reshape(L, Y)
    s = (M @ R).reshape(L, Y, -1)
    s2 = (M @ R2).reshape(L, Y, -1)
    return n, s, s2


def period(n, s, s2, a, b):
    i, j = a - 2010, b - 2010 + 1
    return n[:, i:j].sum(1), s[:, i:j].sum(1), s2[:, i:j].sum(1)


def search(F, R, tk, min_n=300, shuffle=None, max_pairs=None):
    """条件 (1つ・2つ) × 買い目 のうち、発見期間で回収率 100% 超え・レース数 min_n 以上のものを返す。"""
    R2 = R.astype(np.float64) ** 2
    R2 = R2.astype(np.float32)
    years = F["年"].values
    feats = [c for c in F.columns if c != "年"]
    G = F[feats].copy()
    if shuffle is not None:  # 偽の条件: 年ごとに条件の行をでたらめに入れ替える
        rng = np.random.default_rng(shuffle)
        for y in np.unique(years):
            m = np.where(years == y)[0]
            G.iloc[m] = G.iloc[rng.permutation(m)].values
    codes = {f: pd.factorize(G[f])[0] for f in feats}
    levels = {f: pd.factorize(G[f])[1] for f in feats}
    combos = [(f,) for f in feats] + list(itertools.combinations(feats, 2))
    if max_pairs:
        combos = combos[:max_pairs]
    rows = []
    t0 = time.time()
    for k, fs in enumerate(combos):
        if len(fs) == 1:
            c, L = codes[fs[0]], len(levels[fs[0]])
        else:
            L2 = len(levels[fs[1]])
            c, L = codes[fs[0]] * L2 + codes[fs[1]], len(levels[fs[0]]) * L2
        n, s, s2 = cell_sums(c, years, R, R2, L)
        nd, sd, _ = period(n, s, s2, *PERIODS["発見"])
        roi = sd / np.maximum(nd, 1)[:, None] / 100
        hit = np.argwhere((roi > 1.0) & (nd[:, None] >= min_n))
        if len(hit):
            li, ti = hit[:, 0], hit[:, 1]
            if len(fs) == 1:
                lab = [f"{fs[0]}={levels[fs[0]][i]}" for i in li]
            else:
                L2 = len(levels[fs[1]])
                lab = [f"{fs[0]}={levels[fs[0]][i // L2]} & {fs[1]}={levels[fs[1]][i % L2]}" for i in li]
            rec = {"条件": lab, "券種": [tk[t][0] for t in ti], "買い目": [tk[t][1] for t in ti]}
            for name, (ya, yb) in PERIODS.items():
                pn, ps, ps2 = period(n, s, s2, ya, yb)
                pn, ps, ps2 = pn[li], ps[li, ti], ps2[li, ti]
                m = ps / np.maximum(pn, 1)
                var = np.maximum(ps2 / np.maximum(pn, 1) - m ** 2, 0)  # 1レースあたりの払戻の分散
                rec[f"{name}_レース"] = pn.astype(int)
                rec[f"{name}_回収率"] = m / 100
                rec[f"{name}_下限"] = (m - 1.96 * np.sqrt(var / np.maximum(pn, 1))) / 100
            yr = s[li, :, ti] / np.maximum(n[li], 1) / 100
            rec["100%超えの年数"] = ((yr > 1) & (n[li] >= 20)).sum(1)
            rec["年数"] = (n[li] >= 20).sum(1)
            rows.append(pd.DataFrame(rec))
        if k % 50 == 0:
            print(f"  {k}/{len(combos)} 条件  候補 {sum(map(len, rows))}  {time.time() - t0:.0f}秒", flush=True)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="boatrace.sqlite")
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--out", default="explore")
    ap.add_argument("--min-n", type=int, default=300)
    ap.add_argument("--placebo", type=int, default=1, help="偽の条件で何回やるか")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cache = os.path.join(a.out, "table.npz")
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        R, tk = z["R"], [tuple(t) for t in z["tk"]]
        tk = [(b, c, set(map(int, c.split("-")))) for b, c, *_ in tk]
        F = pd.read_pickle(os.path.join(a.out, "F.pkl"))
    else:
        F, R, tk = build(a.db, a.start)
        np.savez(cache, R=R, tk=np.array([(b, c) for b, c, _ in tk], dtype=object))
        F.to_pickle(os.path.join(a.out, "F.pkl"))
    print(f"{len(F)}レース × 買い目 {len(tk)}通り")
    base = R.mean(0) / 100
    pd.DataFrame({"券種": [t[0] for t in tk], "買い目": [t[1] for t in tk], "回収率": base}).to_csv(
        os.path.join(a.out, "base.csv"), index=False, encoding="utf-8-sig")
    real = search(F, R, tk, a.min_n)
    real.to_csv(os.path.join(a.out, "candidates.csv"), index=False, encoding="utf-8-sig")
    fakes = []
    for i in range(a.placebo):
        f = search(F, R, tk, a.min_n, shuffle=i + 1)
        f.to_csv(os.path.join(a.out, f"placebo{i + 1}.csv"), index=False, encoding="utf-8-sig")
        fakes.append(f)
    for name, d in [("本物の条件", real)] + [(f"偽の条件{i + 1}", f) for i, f in enumerate(fakes)]:
        v = d[(d["確認_回収率"] > 1) & (d["本番_回収率"] > 1)]
        print(f"{name}: 発見で100%超え {len(d)} → 確認・本番とも100%超え {len(v)}")


if __name__ == "__main__":
    main()

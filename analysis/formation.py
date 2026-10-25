"""フォーメーション (流し・BOX など) と、選手ごとの買い方で回収率 100% 超えを探す。

  A. モデルの予想順位で作るフォーメーション × 条件
     python analysis/formation.py model --db boatrace.sqlite --model model --features feat.pkl --pred pred_dir \
         --start 2025-01-01 --end 2026-12-31 --split 2026-01-01 --out out
  B. 選手ごと (選手 × 枠 / 選手全体 / 選手 × 場) に、その選手を軸にした買い方 (モデルを使わない)
     python analysis/formation.py racer --db boatrace.sqlite --start 2015-01-01 --split 2023-01-01 --out out

  どちらも split より前を「発見期間」、以降を「確認期間」とし、発見期間で選んだ買い方を確認期間で評価する。
  欠場・フライング・出遅れがあったレースは返還の計算を省くため除く (全体の約 3%)。
"""
import argparse
import itertools
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BET_COLS = ["単勝", "複勝", "２連単", "２連複", "拡連複", "３連単", "３連複"]


def race_table(db, start, end):
    """レースごとに 1〜3 着の艇、各券種の払戻、返還の有無を 1 行にまとめる。"""
    con = sqlite3.connect(db)
    lo, hi = start.replace("-", ""), end.replace("-", "") + "~"
    res = pd.read_sql_query("SELECT race_id, lane, racer_no, finish, finish_raw FROM results "
                            "WHERE race_id >= ? AND race_id < ?", con, params=(lo, hi))
    pay = pd.read_sql_query("SELECT race_id, bet_type, combination, payout FROM payouts "
                            "WHERE race_id >= ? AND race_id < ?", con, params=(lo, hi))
    con.close()
    bad = res.groupby("race_id")["finish_raw"].apply(lambda s: s.fillna("").str[0].isin(["K", "F", "L"]).any()
                                                      or len(s) != 6)
    order = res[res.finish.between(1, 3)].pivot_table(index="race_id", columns="finish", values="lane",
                                                      aggfunc="first")
    order = order[order.notna().all(axis=1)]
    t = pd.DataFrame(index=order.index)
    t["w1"], t["w2"], t["w3"] = order[1].astype(int), order[2].astype(int), order[3].astype(int)
    t = t[~bad.reindex(t.index).fillna(True)]
    single = pay[pay.bet_type.isin(["単勝", "２連単", "３連単", "２連複", "３連複"])]
    single = single[single.combination != "特払い"].drop_duplicates(["race_id", "bet_type"])
    p1 = single.pivot(index="race_id", columns="bet_type", values="payout")
    for b in ["単勝", "２連単", "３連単", "２連複", "３連複"]:
        t[b] = p1[b].reindex(t.index)
    # 複勝は 1・2 着の艇ごと、拡連複は 3 組
    fuku = pay[pay.bet_type == "複勝"].set_index(["race_id", "combination"])["payout"]
    wide = pay[pay.bet_type == "拡連複"].set_index(["race_id", "combination"])["payout"]
    idx = t.index
    t["複勝1"] = fuku.reindex(list(zip(idx, t.w1.astype(str)))).values
    t["複勝2"] = fuku.reindex(list(zip(idx, t.w2.astype(str)))).values
    for a, b in [("w1", "w2"), ("w1", "w3"), ("w2", "w3")]:
        lo_, hi_ = np.minimum(t[a], t[b]), np.maximum(t[a], t[b])
        t[f"拡{a}{b}"] = wide.reindex(list(zip(idx, lo_.astype(str) + "-" + hi_.astype(str)))).values
    t = t.dropna()
    return t, res


# ---------------- A. モデルの順位で作るフォーメーション ----------------
def formations():
    """(券種, 名前, 1着の順位集合, 2着, 3着, 点数)。順位は予想の 1 着確率の順 (1 = 本命)。"""
    F = []
    top = lambda n: tuple(range(1, n + 1))
    for r in (1, 2, 3):
        F.append(("単勝", f"{r}番手", (r,), None, None, 1))
        F.append(("複勝", f"{r}番手", (r,), None, None, 1))
    # 2連単: 1着候補 → 2着候補
    for f1 in [(1,), (2,), (1, 2), (1, 2, 3)]:
        for n in range(2, 7):
            f2 = top(n)
            k = sum(1 for a in f1 for b in f2 if a != b)
            if k:
                F.append(("２連単", f"{'.'.join(map(str, f1))}→上位{n}", f1, f2, None, k))
    # 3連単: 1着 → 2着 → 3着
    for f1 in [(1,), (2,), (3,), (1, 2), (1, 2, 3)]:
        for n2 in range(2, 6):
            for n3 in range(n2, 7):
                f2, f3 = top(n2), top(n3)
                k = sum(1 for a in f1 for b in f2 for c in f3 if len({a, b, c}) == 3)
                if k:
                    F.append(("３連単", f"{'.'.join(map(str, f1))}→上位{n2}→上位{n3}", f1, f2, f3, k))
    # 2連複・拡連複: 本命軸流し / BOX
    for b in ("２連複", "拡連複"):
        for n in range(2, 7):
            F.append((b, f"本命軸→上位{n}", (1,), top(n), None, n - 1))
            if n >= 3:
                F.append((b, f"上位{n}BOX", top(n), top(n), None, n * (n - 1) // 2))
    # 3連複: 1頭軸・2頭軸・BOX
    for n in range(3, 7):
        F.append(("３連複", f"上位{n}BOX", top(n), top(n), top(n), n * (n - 1) * (n - 2) // 6))
        F.append(("３連複", f"本命1頭軸→上位{n}", (1,), top(n), top(n), (n - 1) * (n - 2) // 2))
        F.append(("３連複", f"本命・2番手の2頭軸→上位{n}", (1, 2), (1, 2), top(n), n - 2))
    return F


def formation_returns(t, rank):
    """rank: (レース数, 7) の配列。rank[:, lane] = その艇の予想順位。各フォーメーションの払戻を返す。"""
    n = np.arange(len(t))
    r1, r2, r3 = rank[n, t.w1.values], rank[n, t.w2.values], rank[n, t.w3.values]
    out = {}
    for bet, name, f1, f2, f3, k in formations():
        i1, i2, i3 = np.isin(r1, f1), np.isin(r2, f2 or ()), np.isin(r3, f3 or ())
        if bet == "単勝":
            ret = np.where(i1, t["単勝"], 0)
        elif bet == "複勝":
            ret = np.where(r1 == f1[0], t["複勝1"], 0) + np.where(r2 == f1[0], t["複勝2"], 0)
        elif bet == "２連単":
            ret = np.where(i1 & i2, t["２連単"], 0)
        elif bet == "３連単":
            ret = np.where(i1 & i2 & i3, t["３連単"], 0)
        elif bet == "２連複":
            ret = np.where(pair_hit(r1, r2, f1, f2), t["２連複"], 0)
        elif bet == "拡連複":
            ret = (np.where(pair_hit(r1, r2, f1, f2), t["拡w1w2"], 0) + np.where(pair_hit(r1, r3, f1, f2), t["拡w1w3"], 0)
                   + np.where(pair_hit(r2, r3, f1, f2), t["拡w2w3"], 0))
        else:  # ３連複: 3 艇の順位集合が「軸を含み、全員が流し先の範囲」
            s = np.sort(np.stack([r1, r2, r3], 1), 1)
            if "2頭軸" in name:
                ret = np.where((s[:, 0] == 1) & (s[:, 1] == 2) & (s[:, 2] <= max(f3)), t["３連複"], 0)
            elif "1頭軸" in name:
                ret = np.where((s[:, 0] == 1) & (s[:, 2] <= max(f2)), t["３連複"], 0)
            else:  # BOX
                ret = np.where(s[:, 2] <= max(f1), t["３連複"], 0)
        out[(bet, name, k)] = np.asarray(ret, float)
    return out


def pair_hit(ra, rb, f1, f2):
    """2 艇 (順不同) が「軸 f1 の 1 艇 + 流し先 f2」または BOX (f1 == f2) に入るか。"""
    if f1 == f2:
        return np.isin(ra, f1) & np.isin(rb, f1)
    return (np.isin(ra, f1) & np.isin(rb, f2) & (ra != rb)) | (np.isin(rb, f1) & np.isin(ra, f2) & (ra != rb))


def cmd_model(a):
    from grid import race_conditions, filters
    t, _ = race_table(a.db, a.start, a.end)
    P = pd.read_pickle(f"{a.pred}/P.pkl")
    P = P.loc[P.index.intersection(t.index)]
    t = t.loc[P.index]
    df = pd.read_pickle(a.features)
    df = df[df.race_id.isin(P.index)]
    rank = np.zeros((len(P), 7), int)
    rank[:, 1:] = (-P.values).argsort(1).argsort(1) + 1
    rets = formation_returns(t, rank)
    cond = race_conditions(df, P)
    split = a.split.replace("-", "")
    per = {"発見": (P.index < split), "確認": (P.index >= split)}
    rows = []
    for name, value, mask in filters(cond):
        for (bet, form, k), ret in rets.items():
            row = dict(条件=name, 値=value, 券種=bet, 買い方=form, 点数=k)
            for p, pm in per.items():
                m = mask & pm
                row[f"{p}レース数"] = int(m.sum())
                row[f"{p}回収率"] = ret[m].sum() / (100 * k * m.sum()) if m.sum() else np.nan
                row[f"{p}的中率"] = (ret[m] > 0).mean() if m.sum() else np.nan
            rows.append(row)
    g = pd.DataFrame(rows)
    g = g[(g["発見レース数"] >= 200) & (g["確認レース数"] >= 100)]
    g.to_csv(f"{a.out}/formation_grid.csv", index=False, encoding="utf-8-sig")
    print(len(g), "combos")


# ---------------- B. 選手ごとの買い方 (モデルなし) ----------------
RACER_BETS = {  # 名前: (点数, 払戻の計算)
    "単勝": 1, "複勝": 1, "2連単 1着固定流し": 5, "3連単 1着固定流し": 20, "3連複 軸流し": 10, "拡連複 軸流し": 5,
    "2連単 2着固定": 5, "3連単 1・2着付け": 40,
}


def racer_returns(t, res):
    """出走 1 回 (選手 × レース) ごとに、その選手を軸にした各買い方の払戻。"""
    x = res[res.race_id.isin(t.index)].merge(t, left_on="race_id", right_index=True)
    lane = x["lane"].values
    first, second, third = lane == x.w1.values, lane == x.w2.values, lane == x.w3.values
    top3 = first | second | third
    wide = np.zeros(len(x))
    for a, b, c in [("w1", "w2", "拡w1w2"), ("w1", "w3", "拡w1w3"), ("w2", "w3", "拡w2w3")]:
        wide += np.where((lane == x[a].values) | (lane == x[b].values), x[c].values, 0)
    out = pd.DataFrame({
        "単勝": np.where(first, x["単勝"], 0),
        "複勝": np.where(first, x["複勝1"], 0) + np.where(second, x["複勝2"], 0),
        "2連単 1着固定流し": np.where(first, x["２連単"], 0),
        "3連単 1着固定流し": np.where(first, x["３連単"], 0),
        "3連複 軸流し": np.where(top3, x["３連複"], 0),
        "拡連複 軸流し": wide,
        "2連単 2着固定": np.where(second, x["２連単"], 0),
        "3連単 1・2着付け": np.where(first | second, x["３連単"], 0),
    }, index=x.index)
    keys = x[["race_id", "racer_no", "lane"]].copy()
    keys["jcd"] = x["race_id"].str[9:11].astype(int)
    keys["date"] = x["race_id"].str[:8]
    return keys, out


def cmd_racer(a):
    t, res = race_table(a.db, a.start, a.end)
    keys, out = racer_returns(t, res)
    split = a.split.replace("-", "")
    disc = (keys["date"] < split).values
    rows = []
    for group in (["racer_no"], ["racer_no", "lane"], ["racer_no", "jcd"]):
        gname = "×".join({"racer_no": "選手", "lane": "枠", "jcd": "場"}[c] for c in group)
        for bet, k in RACER_BETS.items():
            d = keys[group].copy()
            d["ret"] = out[bet].values
            d["disc"] = disc
            agg = d.groupby(group + ["disc"])["ret"].agg(["sum", "size"]).unstack("disc")
            agg.columns = [f"{s}_{'発見' if c else '確認'}" for s, c in agg.columns]
            agg = agg.fillna(0)
            for p in ("発見", "確認"):
                agg[f"{p}回収率"] = agg[f"sum_{p}"] / (100 * k * agg[f"size_{p}"])
            agg = agg.reset_index()
            agg.insert(0, "買い方", bet)
            agg.insert(0, "単位", gname)
            rows.append(agg.rename(columns={"size_発見": "発見出走数", "size_確認": "確認出走数"}))
    g = pd.concat(rows, ignore_index=True)
    g["点数"] = g["買い方"].map(RACER_BETS)
    g.to_csv(f"{a.out}/racer_grid.csv", index=False, encoding="utf-8-sig")
    print(len(g), "rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["model", "racer"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--features")
    ap.add_argument("--pred")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default="2099-12-31")
    ap.add_argument("--split", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    {"model": cmd_model, "racer": cmd_racer}[a.command](a)


if __name__ == "__main__":
    main()

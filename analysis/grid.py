"""買い方の全組み合わせ (券種 × 点数 × 条件) で的中率と回収率を出し、両者の関係を調べる。

  python analysis/grid.py --db boatrace.sqlite --model model --start 2025-01-01 --end 2026-12-31 \
      --split 2026-01-01 --out grid_out

  - 予想は --model のモデル (学習期間より後の期間を指定すること)
  - 条件は「本命の1着確率」「本命の枠・級別」「場」「レース種別」「R番号」「風・波 (直前情報)」「本命のモーター順位」
    の1つ、または「本命の1着確率」と他の条件の2つの組み合わせ
  - --split より前を「発見期間」、以降を「確認期間」として、発見期間で良かった買い方が確認期間でも良いかを見る
"""
import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from predict import Book, Predictor, features, load, tickets_from_probs  # noqa: E402

KS = {"単勝": [1, 2, 3], "複勝": [1, 2, 3], "２連単": [1, 2, 3, 4, 5, 6, 8, 10], "２連複": [1, 2, 3, 4, 6],
      "拡連複": [1, 2, 3, 4, 6], "３連単": [1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30], "３連複": [1, 2, 3, 4, 5, 6, 8, 10]}
MIN_RACES = 200


def ticket_returns(book, rids, tk):
    """券種ごとに (レース数, 最大点数) の配列: 予想上位から順に買ったときの累計払戻と、累計で当たったか。"""
    out = {}
    for bet, ks in KS.items():
        combos, prob = tk[bet]
        kmax = max(ks)
        ret = np.full((len(rids), kmax), np.nan)
        hit = np.zeros((len(rids), kmax), bool)
        for n, rid in enumerate(rids):
            win = book.pay.get((rid, bet))
            if not win:
                continue  # レース不成立など
            order = np.argsort(-prob[n])[:kmax]
            r = [book.ret(rid, bet, combos[k]) for k in order]
            h = [combos[k] in win for k in order]
            ret[n] = np.cumsum(r)
            hit[n] = np.logical_or.accumulate(h)
        out[bet] = (ret, hit)
    return out


def race_conditions(df, P):
    """レースごとの条件 (買う前に分かるもの。風・波は直前情報)。"""
    r = df.drop_duplicates("race_id").set_index("race_id").loc[P.index]
    fav = P.values.argmax(1) + 1
    p = P.values.max(1)
    boats = df.set_index(["race_id", "lane"])
    key = list(zip(P.index, fav))
    c = pd.DataFrame(index=P.index)
    c["本命の確率"] = pd.cut(p, [0, .35, .5, .6, .7, .8, 1.01], right=False,
                         labels=["35%未満", "35-50%", "50-60%", "60-70%", "70-80%", "80%以上"]).astype(str)
    c["本命の枠"] = np.where(fav == 1, "1号艇", "2〜6号艇")
    c["本命の級別"] = boats["class"].reindex(key).values
    c["場"] = r["jcd"].map(lambda j: f"{int(j):02d}").values
    t = r["race_type"].fillna("")
    c["レース種別"] = np.select([t.str.contains("優勝") & ~t.str.contains("準"), t.str.contains("準優"),
                            t.str.contains("予選"), t.str.contains("一般")], ["優勝戦", "準優勝戦", "予選", "一般"],
                           "特選・選抜など")
    c["R番号"] = pd.cut(r["rno"], [0, 4, 8, 12], labels=["1-4R", "5-8R", "9-12R"]).astype(str).values
    c["風速(直前)"] = pd.cut(r["wind_speed"], [-1, 2, 4, 99], labels=["0-2m", "3-4m", "5m以上"]).astype(str).values
    c["波(直前)"] = pd.cut(r["wave_cm"], [-1, 2, 5, 999], labels=["0-2cm", "3-5cm", "6cm以上"]).astype(str).values
    c["本命のモーター"] = np.where(boats["motor_2_rate_rk"].reindex(key).values <= 2, "レース内2位以内", "3位以下")
    c["1号艇の級別"] = np.where(boats["class"].reindex(list(zip(P.index, [1] * len(P)))).values == "A1", "A1",
                            "A1以外")
    return c


def filters(cond):
    """(条件名, 値の説明, マスク) を全部作る。単独の条件と、本命の確率 × 他の条件。"""
    yield "全レース", "", np.ones(len(cond), bool)
    for col in cond.columns:
        for v in sorted(cond[col].dropna().unique()):
            yield col, v, (cond[col] == v).values
    for col in cond.columns:
        if col == "本命の確率":
            continue
        for pv, v in itertools.product(sorted(cond["本命の確率"].unique()), sorted(cond[col].dropna().unique())):
            yield f"本命の確率×{col}", f"{pv} / {v}", ((cond["本命の確率"] == pv) & (cond[col] == v)).values


def grid(rets, cond, period):
    rows = []
    for name, value, mask in filters(cond):
        for bet, ks in KS.items():
            ret, hit = rets[bet]
            for part, pm in period.items():
                m = mask & pm & ~np.isnan(ret[:, 0])
                n = int(m.sum())
                if n < MIN_RACES:
                    continue
                for k in ks:
                    rows.append(dict(条件=name, 値=value, 券種=bet, 点数=k, 期間=part, レース数=n,
                                     的中率=hit[m, k - 1].mean(), 回収率=ret[m, k - 1].sum() / (100 * k * n)))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--model")
    ap.add_argument("--features", help="features() 済みの pickle (あれば --db の代わりに使う)")
    ap.add_argument("--pred", help="予想済みの P.pkl / Q.pkl があるフォルダ")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--history-days", type=int, default=1460)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.features:
        df = pd.read_pickle(a.features)
    else:
        warm = (pd.Timestamp(a.start) - pd.Timedelta(days=a.history_days)).strftime("%Y-%m-%d")
        df = features(load(a.db, warm, a.end)[0])
    df = df[(df.race_date >= a.start) & (df.race_date <= a.end)]
    pr = Predictor.load(a.model)
    if a.pred:
        P, Q = pd.read_pickle(f"{a.pred}/P.pkl"), pd.read_pickle(f"{a.pred}/Q.pkl")
        P = P[(P.index >= a.start.replace("-", "")) & (P.index <= a.end.replace("-", "") + "~")]
        Q = Q.loc[P.index]
    else:
        P, Q = pr.probs(df)
    ex, tri = pr.orders(P, Q)
    tk = tickets_from_probs(P.values, ex, tri)
    import sqlite3
    con = sqlite3.connect(a.db)
    pay = pd.read_sql_query("SELECT * FROM payouts WHERE race_id >= ? AND race_id < ?", con,
                            params=(a.start.replace("-", ""), a.end.replace("-", "") + "~"))
    book = Book(pay[pay.race_id.isin(P.index)], df)
    rets = ticket_returns(book, list(P.index), tk)
    cond = race_conditions(df, P)
    split = a.split.replace("-", "")
    period = {"全期間": np.ones(len(P), bool), "発見": P.index < split, "確認": P.index >= split}
    g = grid(rets, cond, period)
    g.to_csv(f"{a.out}/grid.csv", index=False, encoding="utf-8-sig")
    print(len(g), "rows")


if __name__ == "__main__":
    main()

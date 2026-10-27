"""答え合わせ用の買い方 (3連単・3連複)。予想の各艇の1着確率の高い順に ◎○▲△□ の印を付けて、
買い方ごとの買い目を作り、実際の払戻と比べる。

  ◎ = 本命 (1着確率が一番高い艇)、○ = 2番目、▲ = 3番目、△ = 4番目、□ = 5番目
  1点100円。欠場・フライング・出遅れの艇を含む買い目は返還 (100円)。
"""
from itertools import combinations, permutations

import pandas as pd

MARKS = "◎○▲△□"


def marks(r):
    """予想の行 (1号艇〜6号艇の1着確率) から、確率の高い順の艇番 [◎, ○, ▲, △, □, 6番目]。"""
    p = [(float(r[f"{l}号艇"]), -l) for l in range(1, 7)]
    return [-l for _, l in sorted(p, reverse=True)]


def mark_of(m, lane):
    """艇番の印 (6番目の艇は「無」)。"""
    i = m.index(lane)
    return MARKS[i] if i < 5 else "無"


def tri(m, a, b, c):
    """3連単フォーメーション: 1着 a × 2着 b × 3着 c (印の番号 0=◎)。"""
    return [(m[i], m[j], m[k]) for i in a for j in b for k in c if len({i, j, k}) == 3]


def box(m, n):
    return list(permutations(m[:n], 3))


def fuku(combos):
    return sorted({tuple(sorted(c)) for c in combos})


def ticket(s):
    return tuple(int(x) for x in s.split("-"))


# (券種, 買い方の名前, 買い目を作る関数 (印の順の艇番, 予想の行) → [(1着, 2着, 3着), ...])
PATTERNS = [
    ("3連単", "予想着順 1点", lambda m, r: [ticket(r["予想着順"])]),
    ("3連単", "確率の上位5点", lambda m, r: [ticket(t) for t in r["3連単上位5点"].split()]),
    ("3連単", "◎-○▲-○▲△ (4点)", lambda m, r: tri(m, [0], [1, 2], [1, 2, 3])),
    ("3連単", "◎-○▲△-○▲△ (6点)", lambda m, r: tri(m, [0], [1, 2, 3], [1, 2, 3])),
    ("3連単", "◎○-◎○▲-◎○▲△ (6点)", lambda m, r: tri(m, [0, 1], [0, 1, 2], [0, 1, 2, 3])),
    ("3連単", "◎-○▲△□ 流し (12点)", lambda m, r: tri(m, [0], [1, 2, 3, 4], [1, 2, 3, 4])),
    ("3連単", "◎○▲ BOX (6点)", lambda m, r: box(m, 3)),
    ("3連単", "◎○▲△ BOX (24点)", lambda m, r: box(m, 4)),
    ("3連複", "◎○▲ 1点", lambda m, r: fuku([m[:3]])),
    ("3連複", "◎○-▲△□ 2軸 (3点)", lambda m, r: fuku((m[0], m[1], x) for x in m[2:5])),
    ("3連複", "◎-○▲△ (3点)", lambda m, r: fuku((m[0],) + c for c in combinations(m[1:4], 2))),
    ("3連複", "◎-○▲△□ (6点)", lambda m, r: fuku((m[0],) + c for c in combinations(m[1:5], 2))),
    ("3連複", "◎○▲△ BOX (4点)", lambda m, r: fuku(combinations(m[:4], 3))),
    ("3連複", "◎○▲△□ BOX (10点)", lambda m, r: fuku(combinations(m[:5], 3))),
]
BET = {"3連単": "３連単", "3連複": "３連複"}


def key(bet, name):
    return f"{bet} {name}"


def payout_table(pay):
    """払戻 (race_id, bet_type, combination, payout) → {race_id: {(券種, 組): 払戻}}。3連複の組は小さい順。"""
    out = {}
    for rid, bt, combo, p in pay[["race_id", "bet_type", "combination", "payout"]].itertuples(index=False):
        if bt not in BET.values() or not isinstance(combo, str) or "-" not in combo:
            continue
        c = ticket(combo)
        out.setdefault(rid, {})[(bt, tuple(sorted(c)) if bt == "３連複" else c)] = int(p)
    return out


def race_rows(pred, pays, void):
    """予想 (race_id が index) と払戻・返還の艇 {race_id: {艇番}} から、レースごと・買い方ごとの点数・払戻・的中。"""
    rows = []
    for rid, r in pred.iterrows():
        if rid not in pays:
            continue
        m = marks(r)
        won = [c for b, c in pays[rid] if b == "３連単"]
        row = {"race_id": rid, "場": r["場"], "R": r["R"], "印": " ".join(f"{MARKS[i]}{m[i]}" for i in range(5)),
               "本命1着確率": r["本命1着確率"], "結果": "-".join(map(str, won[0])) if won else "",
               "結果の印": "".join(mark_of(m, x) for x in won[0]) if won else "",
               "3連単配当": pays[rid].get(("３連単", won[0])) if won else None,
               "3連複配当": pays[rid].get(("３連複", tuple(sorted(won[0])))) if won else None}
        for bet, name, f in PATTERNS:
            got, hit = 0, 0
            tickets = f(m, r)
            for t in tickets:
                if set(t) & void.get(rid, set()):
                    got += 100
                    continue
                p = pays[rid].get((BET[bet], tuple(sorted(t)) if bet == "3連複" else t), 0)
                got += p
                hit |= p > 0
            k = key(bet, name)
            row[k + " 点数"], row[k + " 払戻"], row[k + " 的中"] = len(tickets), got, int(hit)
        rows.append(row)
    return pd.DataFrame(rows)


def summary(k):
    """race_rows の結果から買い方ごとの 投資・払戻・収支・回収率・的中レース数。"""
    out = []
    for bet, name, _ in PATTERNS:
        c = key(bet, name)
        cost, got = int(k[c + " 点数"].sum()) * 100, int(k[c + " 払戻"].sum())
        out.append({"券種": bet, "買い方": name, "レース": len(k), "投資": cost, "払戻": got, "収支": got - cost,
                    "回収率": got / cost if cost else 0, "的中レース": int(k[c + " 的中"].sum())})
    return pd.DataFrame(out)

"""これまでの予想の成績。predictions フォルダの答え合わせ (YYYYMMDD_check.csv・_kaime.csv) を全部まとめて、
的中率・回収率などを期間ごと (全期間・直近30日・直近7日) と日ごとに集計し、良い悪いを「備考」に書く。

  python analysis/yosou.py seiseki            画面に出して predictions/seiseki.csv・seiseki_days.csv に保存
  python analysis/yosou.py seiseki --backtest  過去10年を予想し直した分 (yosou.py backtest) の成績

備考の目安:
  回収率  100%以上 = 黒字 / 90〜100% = あと少し / 75〜90% = 赤字だが、でたらめに買う (払戻率 約75%) よりは良い / 75%未満 = でたらめ以下
  的中率  予想した確率 (本命1着確率・3連単上位5点の的中確率の平均) と実際を比べる。いつも1号艇を本命にした場合とも比べる
  的中が少ない (10回未満) ときは、1回の大穴で大きく変わるので参考程度
"""
import datetime as dt
import json
import os

import pandas as pd

PAYBACK = 0.75   # ボートレースの払戻率 (控除率 25%)。でたらめに買ったときの回収率の目安
FEW_HITS = 10    # これより的中が少ないと回収率は当てにならない
NEAR = 0.03      # 的中率が予想とこのくらいずれるまでは「予想どおり」
TREND = 0.10     # 直近7日の回収率が全期間とこれ以上違えば備考に書く
PERIODS = [("全期間", None), ("直近1年", 365), ("直近30日", 30), ("直近7日", 7)]
RECENT_DAYS = 60  # 日ごとの表に出す日数 (長い期間は月ごと・年ごとの表で見る)


def load(out):
    """答え合わせした全レース (日付つき) と、買い方ごとの払戻 (_kaime.csv) と、log.csv。"""
    files = sorted(os.listdir(out)) if os.path.isdir(out) else []
    checks, kaimes = [], []
    for f in files:
        if not (f.endswith("_check.csv") and f[:8].isdigit()):
            continue
        d = f"{f[:4]}-{f[4:6]}-{f[6:8]}"
        c = pd.read_csv(os.path.join(out, f), encoding="utf-8-sig")
        pred = os.path.join(out, f[:8] + ".csv")
        if os.path.exists(pred) and "3連単上位5点の的中確率" not in c:  # 予想したときの3連単上位5点の的中確率 (予想と実際を比べる)
            p = pd.read_csv(pred, encoding="utf-8-sig")
            if "3連単上位5点の的中確率" in p:
                c = c.merge(p[["race_id", "3連単上位5点の的中確率"]], on="race_id", how="left")
        checks.append(c if "日付" in c else c.assign(日付=d))  # backtest は1年分のファイルに日付が入っている
        kp = os.path.join(out, f[:8] + "_kaime.csv")
        if os.path.exists(kp):
            kk = pd.read_csv(kp, encoding="utf-8-sig")
            kaimes.append(kk if "日付" in kk else kk.assign(日付=d))
    c = pd.concat(checks, ignore_index=True) if checks else pd.DataFrame()
    k = pd.concat(kaimes, ignore_index=True) if kaimes else pd.DataFrame()
    lp = os.path.join(out, "log.csv")
    lg = pd.read_csv(lp, encoding="utf-8-sig") if os.path.exists(lp) else pd.DataFrame()
    for col in ("本命1着", "本命3着以内", "予想着順的中", "上位5点的中"):
        if col in c:
            c[col] = c[col].astype(str).str.lower().eq("true")
    return c, k, lg


SAVED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "seiseki.json")


def saved():
    """リポジトリに入れてある過去10年の検証の集計 (analysis/backtest/seiseki.json)。"""
    if not os.path.exists(SAVED):
        return None
    with open(SAVED, encoding="utf-8") as f:
        return json.load(f)


def pct(x):
    return f"{x:.1%}" if x == x else "-"


def payback_note(rate, hits, cost):
    """回収率の評価と備考。"""
    if not cost:
        return "", "買い目なし"
    if rate >= 1:
        grade, note = "◎", "黒字。この買い方は続ける価値あり"
    elif rate >= 0.9:
        grade, note = "○", "あと少しで黒字"
    elif rate >= PAYBACK:
        grade, note = "△", f"赤字。でたらめに買う (約{PAYBACK:.0%}) よりは良い"
    else:
        grade, note = "×", f"でたらめに買う (約{PAYBACK:.0%}) より悪い。この買い方はやめたほうがいい"
    if hits < FEW_HITS:
        note += f"。※的中 {hits}回で少なく、1回の大穴で大きく変わるので参考程度"
    return grade, note


def hit_note(actual, expected, what):
    """的中率を予想した確率と比べた評価と備考。"""
    if expected != expected:
        return "", ""
    d = actual - expected
    if d >= NEAR:
        return "◎", f"予想した確率 ({expected:.1%}) より {d * 100:.1f}pt よく当たっている"
    if d > -NEAR:
        return "○", f"予想した確率 ({expected:.1%}) どおり。{what}の自信の付け方は合っている"
    return "×", f"予想した確率 ({expected:.1%}) より {-d * 100:.1f}pt 当たっていない。{what}が自信過剰"


def summarize(c, k, lg):
    """1期間分の成績の表 (1行 = 指標・買い方)。"""
    n = len(c)
    rows = []
    if not n:
        return rows

    def bet(kind, name, races, tickets, hits, got, extra=""):
        cost = tickets * 100
        rate = got / cost if cost else float("nan")
        grade, note = payback_note(rate, hits, cost)
        rows.append({"区分": kind, "項目": name, "レース": races, "点数": tickets, "的中": hits,
                     "的中率": hits / races if races else float("nan"), "投資": cost, "払戻": got, "収支": got - cost,
                     "回収率": rate, "評価": grade, "備考": note + extra})

    # 当たり方 (お金は賭けない指標)
    one = (c["結果"].astype(str).str[0] == "1").mean()  # いつも1号艇を本命にしていたら
    hit = c["本命1着"].mean()
    grade, note = hit_note(hit, c["本命1着確率"].mean(), "本命")
    if hit >= one + NEAR:
        note += f"。いつも1号艇を本命にする ({one:.1%}) より良い"
    elif hit <= one - NEAR:
        note += f"。いつも1号艇を本命にする ({one:.1%}) より悪い"
        grade = "×" if grade != "◎" else "△"
    else:
        note += f"。いつも1号艇を本命にする ({one:.1%}) と同じくらい"
    rows.append({"区分": "当たり方", "項目": "本命の1着", "レース": n, "的中": int(c["本命1着"].sum()), "的中率": hit,
                 "評価": grade, "備考": note})
    t3 = c["本命3着以内"].mean()
    rows.append({"区分": "当たり方", "項目": "本命の3着以内", "レース": n, "的中": int(c["本命3着以内"].sum()), "的中率": t3,
                 "評価": "◎" if t3 >= 0.85 else "○" if t3 >= 0.75 else "×",
                 "備考": "本命が3着までに来ている割合。85%以上なら軸として信頼できる、75%未満なら軸に向かない"})
    ex = c["予想着順的中"].mean()
    rows.append({"区分": "当たり方", "項目": "予想着順 (3連単1点) の的中", "レース": n, "的中": int(c["予想着順的中"].sum()),
                 "的中率": ex, "評価": "◎" if ex >= 0.1 else "○" if ex >= 0.07 else "×",
                 "備考": f"でたらめなら 0.8% (120通りに1つ)。10%以上なら良い、7%未満は今ひとつ"})
    if "3連単上位5点の的中確率" in c and c["3連単上位5点の的中確率"].notna().any():
        h5 = c["上位5点的中"].mean()
        grade, note = hit_note(h5, c["3連単上位5点の的中確率"].mean(), "3連単の確率")
        rows.append({"区分": "当たり方", "項目": "3連単上位5点の的中", "レース": n, "的中": int(c["上位5点的中"].sum()),
                     "的中率": h5, "評価": grade, "備考": note})

    # 買っていたらの収支 (1点100円)
    if "単勝点数" in c and c["単勝点数"].fillna(0).sum():
        u = c[c["単勝点数"].fillna(0) > 0]
        bet("単勝", "単勝の買い目 (期待値1以上)", len(u), int(u["単勝点数"].sum()), int(u["単勝的中"].sum()),
            int(u["単勝払戻"].sum()))
    h1 = c["予想着順的中"]
    bet("3連単", "予想着順 1点", n, n, int(h1.sum()), int(c.loc[h1, "三連単配当"].fillna(0).sum()))
    h5 = c["上位5点的中"]
    bet("3連単", "確率の上位5点", n, 5 * n, int(h5.sum()), int(c.loc[h5, "三連単配当"].fillna(0).sum()))
    if len(lg) and "live買い_点数" in lg and lg["live買い_点数"].fillna(0).sum():
        kk = int(lg["live買い_点数"].fillna(0).sum())
        got = int(lg["live買い_払戻"].fillna(0).sum()) if "live買い_払戻" in lg else 0
        grade, note = payback_note(got / (100 * kk), FEW_HITS, kk)
        rows.append({"区分": "単勝", "項目": "直前 (live・拡張機能) の買い目", "点数": kk, "投資": kk * 100, "払戻": got,
                     "収支": got - kk * 100, "回収率": got / (100 * kk), "評価": grade,
                     "備考": note + "。※的中数は記録していないので、点数が少ないうちは参考程度"})
    if len(k):
        import kaime
        for b, name, _ in kaime.PATTERNS:
            col = kaime.key(b, name)
            if col + " 点数" not in k or name in ("予想着順 1点", "確率の上位5点"):  # 上の行と同じ (_kaime.csv が無い日も入る)
                continue
            bet(b, name, len(k), int(k[col + " 点数"].sum()), int(k[col + " 的中"].sum()), int(k[col + " 払戻"].sum()))
    # 買い方の中で一番良いもの・悪いもの
    money = [r for r in rows if r.get("投資")]
    if len(money) >= 2:
        best = max(money, key=lambda r: r["回収率"])
        worst = min(money, key=lambda r: r["回収率"])
        best["備考"] += "。【買い方の中で回収率が一番高い】"
        worst["備考"] += "。【買い方の中で回収率が一番低い】"
    return rows


def by_table(c, k, key):
    """key (日付・月・年) ごとの成績と備考。"""
    out = []
    if key != "日付":
        c = c.assign(**{key: c["日付"].str[:7 if key == "月" else 4]})
        k = k.assign(**{key: k["日付"].str[:7 if key == "月" else 4]}) if len(k) else k
    for d, g in c.groupby(key):
        n = len(g)
        h5 = g["上位5点的中"]
        r = {key: d, "レース": n, "本命1着率": g["本命1着"].mean(), "予想の本命1着確率": g["本命1着確率"].mean(),
             "予想着順的中率": g["予想着順的中"].mean(), "3連単上位5点的中率": h5.mean(),
             "3連単上位5点回収率": g.loc[h5, "三連単配当"].fillna(0).sum() / (500 * n)}
        if "単勝点数" in g and g["単勝点数"].fillna(0).sum():
            kk = g["単勝点数"].fillna(0).sum()
            r |= {"単勝点数": int(kk), "単勝的中": int(g["単勝的中"].fillna(0).sum()),
                  "単勝回収率": g["単勝払戻"].fillna(0).sum() / (100 * kk)}
        kd = k[k[key] == d] if len(k) else k
        if len(kd):  # 買い方の中で一番良かったもの
            import kaime
            best = None
            for b, name, _ in kaime.PATTERNS:
                col = kaime.key(b, name)
                if col + " 点数" in kd:
                    rate = kd[col + " 払戻"].sum() / (100 * kd[col + " 点数"].sum())
                    if best is None or rate > best[1]:
                        best = (col, rate)
            if best:
                r |= {"一番良かった買い方": best[0], "その回収率": best[1]}
        notes = []
        diff = r["本命1着率"] - r["予想の本命1着確率"]
        if diff >= NEAR:
            notes.append("本命がよく勝った")
        elif diff <= -NEAR * 2:
            notes.append("本命が予想より負けた (荒れた)")
        if r["3連単上位5点回収率"] >= 1:
            notes.append("3連単上位5点が黒字")
        if r.get("単勝回収率", 0) >= 1:
            notes.append("単勝の買い目が黒字")
        elif "単勝回収率" in r and r["単勝回収率"] < PAYBACK:
            notes.append("単勝の買い目が不調")
        if r.get("その回収率", 0) >= 1:
            notes.append(f"{r['一番良かった買い方']} が黒字")
        r["評価"] = "◎" if r["3連単上位5点回収率"] >= 1 or r.get("単勝回収率", 0) >= 1 else \
            "×" if diff <= -NEAR * 2 else "○" if diff > -NEAR else "△"
        r["備考"] = "、".join(notes) or "ふつう"
        out.append(r)
    return sorted(out, key=lambda r: r[key], reverse=True)


def build(out):
    """期間ごとの成績と日ごとの成績 (画面・JSON 用)。"""
    c, k, lg = load(out)
    if c.empty:
        return None
    last = dt.date.fromisoformat(c["日付"].max())
    periods = {}
    for name, days in PERIODS:
        if days is None:
            cc, kk, ll = c, k, lg
        else:
            start = (last - dt.timedelta(days=days - 1)).isoformat()
            cc = c[c["日付"] >= start]
            kk = k[k["日付"] >= start] if len(k) else k
            ll = lg[lg["date"] >= start] if len(lg) and "date" in lg else lg
            if cc["日付"].nunique() == c["日付"].nunique():  # 全期間と同じなら出さない
                continue
        periods[name] = {"from": cc["日付"].min(), "to": cc["日付"].max(), "days": int(cc["日付"].nunique()),
                         "rows": summarize(cc, kk, ll)}
    # 直近が全期間と比べて良くなったか
    allr = {(r["区分"], r["項目"]): r for r in periods["全期間"]["rows"]}
    for name in [n for n in periods if n != "全期間"]:
        for r in periods[name]["rows"]:
            a = allr.get((r["区分"], r["項目"]))
            if not a or "回収率" not in r or r["回収率"] != r["回収率"] or a.get("回収率") != a.get("回収率"):
                continue
            d = r["回収率"] - a["回収率"]
            if d >= TREND:
                r["備考"] += f"。全期間 ({a['回収率']:.1%}) より良くなっている"
            elif d <= -TREND:
                r["備考"] += f"。全期間 ({a['回収率']:.1%}) より悪くなっている"
    recent = sorted(c["日付"].unique())[-RECENT_DAYS:]
    out = {"periods": periods, "days": by_table(c[c["日付"].isin(recent)], k[k["日付"].isin(recent)] if len(k) else k, "日付")}
    if c["日付"].str[:7].nunique() > 1:
        out["months"] = by_table(c, k, "月")
    if c["日付"].str[:4].nunique() > 1:
        out["years"] = by_table(c, k, "年")
    return out


def show(s, out):
    """画面に出して CSV に保存する。"""
    if s is None:
        print("答え合わせの記録がまだありません (predict → check を先に)")
        return
    lines = []
    for name, p in s["periods"].items():
        lines.append(f"\n■ {name} ({p['from']}〜{p['to']}、{p['days']}日)")
        for r in p["rows"]:
            if "回収率" in r and r.get("投資"):
                lines.append(f"  {r['評価']} {r['区分']} {r['項目']}: 的中 {r.get('的中', '-')}  投資 {r['投資']:,}円  "
                             f"払戻 {r['払戻']:,}円  収支 {r['収支']:+,}円  回収率 {pct(r['回収率'])}")
            else:
                lines.append(f"  {r['評価']} {r['項目']}: {r['的中']}/{r['レース']}レース  的中率 {pct(r['的中率'])}")
            lines.append(f"      備考: {r['備考']}")
    for key, name in [("years", "年"), ("months", "月"), ("days", "日付")]:
        if key not in s or (key == "months" and "years" in s):  # 何年もあるときは月ごとは CSV だけ
            continue
        lines.append(f"\n■ {name}ごと (新しい順)" if key != "days" else f"\n■ 日ごと (直近{RECENT_DAYS}日、新しい順)")
        for r in s[key]:
            lines.append(f"  {r['評価']} {r[name]} {r['レース']}R  本命1着 {pct(r['本命1着率'])} (予想 {pct(r['予想の本命1着確率'])})  "
                         f"3連単5点 的中 {pct(r['3連単上位5点的中率'])} 回収 {pct(r['3連単上位5点回収率'])}"
                         + (f"  単勝 回収 {pct(r['単勝回収率'])}" if "単勝回収率" in r else "") + f"  備考: {r['備考']}")
    print("\n".join(lines))
    t = pd.DataFrame([{"期間": name, "から": p["from"], "まで": p["to"], **r} for name, p in s["periods"].items()
                      for r in p["rows"]])
    t.to_csv(os.path.join(out, "seiseki.csv"), index=False, encoding="utf-8-sig")
    saved = [os.path.join(out, "seiseki.csv")]
    for key in ("days", "months", "years"):
        if key in s:
            saved.append(os.path.join(out, f"seiseki_{key}.csv"))
            pd.DataFrame(s[key]).to_csv(saved[-1], index=False, encoding="utf-8-sig")
    print("\n保存しました:", " / ".join(saved))
    return lines

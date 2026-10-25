"""今後のレースを予想し、終わったら答え合わせをして、外れの原因を集計する。Windows でも動く。

  python analysis/yosou.py setup                 GitHub の Release から最新の DB (boatrace.sqlite) を取得
  python analysis/yosou.py update                DB の最終日の翌日〜昨日の成績・番組表を公式サイトから取り込む
  python analysis/yosou.py train                 直近のデータでモデルを学習し直す (model フォルダに保存)
  python analysis/yosou.py predict --date 2026-10-26   番組表を取得してその日の全レースを予想
  python analysis/yosou.py live                  締切30分以内のレースの直前情報とオッズを取って、おすすめの買い目を表示
  python analysis/yosou.py check   --date 2026-10-26   成績を取得して答え合わせ・外れの原因を集計
  python analysis/yosou.py daily                 毎日1回: 成績の取り込み → 昨日の答え合わせ → 7日ごとに学習し直す
  python analysis/yosou.py racer --no 4444       選手の成績 (1着率・枠別の1着率・回収率)。--no なしで全選手の CSV
  python analysis/yosou.py week                  今日から1週間のうち、番組表が出ている日をまとめて予想 (ふつう今日と明日)
  python analysis/yosou.py review                先週の予想と結果を比べて振り返り → 学習し直す (毎週月曜は daily が自動で振り返る)
  python analysis/yosou.py serve                 Chrome 拡張機能用のサーバー (boatrace.jp のページにおすすめを表示)

  毎日の流れ: predict (朝) → check (夜) → 週に1回くらい update → train で学習し直す (フィードバック)
  予想は predictions/YYYYMMDD.csv、答え合わせは predictions/YYYYMMDD_check.csv と predictions/log.csv に出る。
"""
import argparse
import contextlib
import datetime as dt
import io
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import build_db  # noqa: E402
import fetch  # noqa: E402
from predict import (V4_FEATS, V4_LATE, PERMS2, PERMS3, Predictor, expected_value, features, fit_odds, load,  # noqa: E402
                     win_payouts)
import lightgbm as lgb  # noqa: E402

EV_MIN = 1.0  # 単勝を買う期待値の下限 (2025〜2026年の検証で回収率 約103%。analysis/README.md)

REPO = "lindwurm310/boat-race-db"
OUT = os.path.join(ROOT, "predictions")
HISTORY_DAYS = 1460  # 選手の過去成績 (枠別成績・直接対決など) を作るために読む日数。学習時も同じだけ前から読む


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": fetch.UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


# ---------------- setup / update ----------------
def cmd_setup(a):
    """Release の中から一番新しい日付まで入っている DB を選んで取得する。"""
    rels = json.loads(http_get(f"https://api.github.com/repos/{REPO}/releases?per_page=100"))
    best = None
    for r in rels:
        if not r["tag_name"].startswith("db-"):
            continue
        assets = {x["name"]: x["browser_download_url"] for x in r["assets"]}
        if "summary.txt" not in assets or "boatrace.sqlite.gz" not in assets:
            continue
        first = http_get(assets["summary.txt"]).decode("utf-8", "replace").splitlines()[0].split("|")
        key = (first[1], int(first[2]))  # (最終日, レース数)
        print(r["tag_name"], first)
        if best is None or key > best[0]:
            best = (key, r["tag_name"], assets["boatrace.sqlite.gz"])
    if best is None:
        sys.exit("DB の Release が見つかりません")
    print("取得:", best[1], best[0])
    tmp = a.db + ".gz.part"
    req = urllib.request.Request(best[2], headers={"User-Agent": fetch.UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    with gzip.open(tmp, "rb") as g, open(a.db, "wb") as f:
        shutil.copyfileobj(g, f)
    os.remove(tmp)
    print("保存しました:", a.db)


def last_date(db):
    con = sqlite3.connect(db)
    d = con.execute("SELECT max(race_date) FROM races r WHERE EXISTS (SELECT 1 FROM results x "
                    "WHERE x.race_id = r.race_id)").fetchone()[0]
    con.close()
    return d


def cmd_update(a):
    start = (dt.date.fromisoformat(last_date(a.db)) + dt.timedelta(days=1)).isoformat()
    end = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    if start > end:
        print("DB は最新です"); return
    print(f"{start} 〜 {end} を取り込みます")
    subprocess.run([sys.executable, os.path.join(ROOT, "fetch.py"), "--kind", "K", "B", "--start", start,
                    "--end", end, "--out", a.raw], check=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "build_db.py"), "--raw", a.raw, "--db", a.db], check=True)
    print("最終日:", last_date(a.db))


# ---------------- train ----------------
def cmd_train(a):
    end = last_date(a.db)
    warm = (dt.date.fromisoformat(end) - dt.timedelta(days=a.days)).isoformat()
    start = (dt.date.fromisoformat(warm) - dt.timedelta(days=HISTORY_DAYS)).isoformat()  # 過去成績の蓄積用
    val = (dt.date.fromisoformat(end) - dt.timedelta(days=30)).isoformat()
    raw, pay = load(a.db, start, end)
    df = features(raw).merge(win_payouts(pay), on=["race_id", "lane"], how="left")
    tr, va = df[(df.race_date >= warm) & (df.race_date < val)], df[df.race_date >= val]
    print(f"学習 {warm}〜{val} {tr.race_id.nunique()}レース / 検証 {val}〜{end} {va.race_id.nunique()}レース")
    pr = Predictor(V4_FEATS, "v3").fit(tr, va)
    P, Q = pr.probs(va)
    win = va.pivot(index="race_id", columns="lane", values="win").loc[P.index].values
    print(f"検証期間の本命1着率 {(win[np.arange(len(P)), P.values.argmax(1)] == 1).mean():.1%}")
    pr.save(a.model)
    if not a.no_late:  # 直前情報 (展示タイムなど) も使うモデル。live で使う
        print("直前情報のモデルを学習します")
        Predictor(V4_LATE, "v3").fit(tr, va).save(os.path.join(a.model, "late"))
    om = fit_odds(tr, va, V4_FEATS)
    om.save_model(os.path.join(a.model, "odds.txt"), num_iteration=om.best_iteration)
    with open(os.path.join(a.model, "trained.json"), "w", encoding="utf-8") as f:
        json.dump(dict(data_end=end, train=[warm, val], val=[val, end]), f, ensure_ascii=False)
    print("保存しました:", a.model)


# ---------------- predict ----------------
def read_day(kind, date, raw):
    """公式サイトから1日分 (K または B) を取得してテキストを返す。取得済みなら raw から読む。"""
    d = dt.date.fromisoformat(date)
    path = fetch.path_for(raw, kind, d)
    if not os.path.exists(path):
        data = fetch.fetch(fetch.url_for(kind, d))
        if not data or data[2:5] != b"-lh":
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return [t for _, t in build_db.read_lzh(path)]


ENTRY_COLS = ["race_id", "lane", "racer_no", "racer_name", "age", "branch", "weight", "class", "nat_win_rate",
              "nat_2_rate", "loc_win_rate", "loc_2_rate", "motor_no", "motor_2_rate", "boat_no", "boat_2_rate",
              "series_results", "early_race"]


def program(date, raw):
    texts = read_day("B", date, raw)
    if texts is None:
        return None
    rows = []
    for text in texts:
        for jcd, body in build_db.split_blocks(text, "B"):
            races, entries = build_db.parse_b(date, jcd, body, [])
            info = {r[0]: dict(jcd=r[2], rno=r[3], race_type=r[4], deadline=r[6]) for r in races}
            for e in entries:
                rows.append(dict(zip(ENTRY_COLS, e)) | info[e[0]] | dict(race_date=date))
    return pd.DataFrame(rows)


def advice(p_top):
    # バックテスト (analysis/README.md) の結果: 本命の確率が高いレースほど単勝・複勝の回収率が高い
    if p_top >= 0.7:
        return "堅い: 本命の複勝/単勝"
    if p_top >= 0.5:
        return "本命有力"
    if p_top < 0.35:
        return "混戦: 見送り推奨"
    return "やや混戦"


def ev_columns(ev, odds):
    buy = [l for l in range(1, 7) if ev[l - 1] >= EV_MIN]
    return {"単勝買い目": " ".join(map(str, buy)),
            "買い目の期待値": " ".join(f"{ev[l - 1]:.2f}" for l in buy),
            **{f"{l}号艇の予想オッズ": round(float(odds[l - 1]), 1) for l in range(1, 7)}}


def day_path(date, suffix):
    return os.path.join(OUT, date.replace("-", "") + suffix)


def cmd_predict(a):
    pr = Predictor.load(a.model)
    prog = program(a.date, a.raw)
    if prog is None or prog.empty:
        sys.exit(f"{a.date} の番組表がまだ公開されていません (公式サイトで公開されるのは前日〜当日朝)")
    start = (dt.date.fromisoformat(a.date) - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    hist, _ = load(a.db, start, (dt.date.fromisoformat(a.date) - dt.timedelta(days=1)).isoformat())
    names = prog.set_index(["race_id", "lane"])["racer_name"]
    new = prog.reindex(columns=hist.columns)
    df = features(pd.concat([hist, new], ignore_index=True), require_result=False)
    df = df[df.race_date == a.date]
    os.makedirs(OUT, exist_ok=True)
    df.to_pickle(day_path(a.date, "_feat.pkl"))  # live で使う (直前情報だけ差し替える)
    P, Q = pr.probs(df)
    ex, tri = pr.orders(P, Q)
    con = sqlite3.connect(a.db)
    venue = dict(con.execute("SELECT jcd, name FROM venues").fetchall())
    con.close()
    info = prog.drop_duplicates("race_id").set_index("race_id")
    odds_path = os.path.join(a.model, "odds.txt")
    EV = None
    if os.path.exists(odds_path):
        om = lgb.Booster(model_file=odds_path)
        om.best_iteration = 0
        odds, EV = expected_value(df, P, om, V4_FEATS)
    rows = []
    for n, rid in enumerate(P.index):
        p = P.values[n]
        order = np.argsort(-tri[n])
        top3 = [PERMS3[k] for k in order[:5]]
        ex_top = [PERMS2[k] for k in np.argsort(-ex[n])[:3]]
        fav = int(p.argmax()) + 1
        rows.append({"race_id": rid, "場": venue.get(int(info.at[rid, "jcd"]), info.at[rid, "jcd"]),
                     "R": int(info.at[rid, "rno"]), "締切": info.at[rid, "deadline"],
                     "本命": fav, "本命選手": names.get((rid, fav), ""), "本命1着確率": round(float(p.max()), 3),
                     "予想着順": "-".join(str(x + 1) for x in top3[0]),
                     "3連単上位5点": " ".join("-".join(str(x + 1) for x in t) for t in top3),
                     "3連単上位5点の的中確率": round(float(tri[n][order[:5]].sum()), 3),
                     "2連単上位3点": " ".join(f"{i+1}-{j+1}" for i, j in ex_top),
                     **{f"{l}号艇": round(float(p[l - 1]), 3) for l in range(1, 7)},
                     "判断": advice(float(p.max())),
                     **(ev_columns(EV.values[n], odds.values[n]) if EV is not None else {})})
    out = pd.DataFrame(rows).sort_values(["締切", "場"])
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, a.date.replace("-", "") + ".csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")  # Excel で文字化けしないように BOM 付き
    pd.set_option("display.width", 200, "display.max_rows", 300, "display.unicode.east_asian_width", True)
    cols = ["場", "R", "締切", "本命", "本命選手", "本命1着確率", "予想着順", "3連単上位5点", "判断"]
    if "単勝買い目" in out:
        cols += ["単勝買い目", "買い目の期待値"]
        n = out["単勝買い目"].str.split().str.len().sum()
        print(f"単勝の買い目 (期待値 {EV_MIN} 以上): {n} 点")
    print(out[cols].to_string(index=False))
    print("\n保存しました:", path)


# ---------------- check ----------------
def results_of(date, raw):
    texts = read_day("K", date, raw)
    if texts is None:
        return None, None
    races, res, pays = [], [], []
    for text in texts:
        for jcd, body in build_db.split_blocks(text, "K"):
            _, r, x, p = build_db.parse_k(date, jcd, body, [])
            races += r; res += x; pays += p
    res = pd.DataFrame(res, columns=["race_id", "lane", "racer_no", "racer_name", "motor_no", "boat_no",
                                     "exhibition_time", "course", "start_timing", "race_time", "finish_raw",
                                     "finish"])
    return pd.DataFrame(races).set_index("race_id"), (res, pd.DataFrame(
        pays, columns=["race_id", "bet_type", "combination", "payout", "popularity"]))


def ev_check(r, rid, tan):
    """予想の単勝買い目 (期待値 1 以上) の点数・的中・払戻。"""
    if "単勝買い目" not in r or not isinstance(r["単勝買い目"], str) or not r["単勝買い目"].strip():
        return {"単勝点数": 0, "単勝的中": 0, "単勝払戻": 0} if "単勝買い目" in r else {}
    lanes = [int(x) for x in r["単勝買い目"].split()]
    got = sum(tan.get((rid, l), 0) for l in lanes)
    return {"単勝点数": len(lanes), "単勝的中": int(got > 0), "単勝払戻": got}


def cmd_check(a):
    path = os.path.join(OUT, a.date.replace("-", "") + ".csv")
    if not os.path.exists(path):
        sys.exit(f"予想ファイルがありません: {path} (先に predict を実行)")
    pred = pd.read_csv(path, encoding="utf-8-sig", dtype={"単勝買い目": str}).set_index("race_id")
    races, rp = results_of(a.date, a.raw)
    if races is None:
        sys.exit(f"{a.date} の成績がまだ公開されていません (公式サイトでは翌日以降)")
    res, pay = rp
    fin = res.pivot(index="race_id", columns="lane", values="finish")
    st = res.assign(st=res["start_timing"].map(lambda s: -float(s[1:]) if isinstance(s, str) and s[:1] == "F"
                                                else pd.to_numeric(s, errors="coerce")))
    st = st.pivot(index="race_id", columns="lane", values="st")
    pay3 = pay[pay.bet_type == "３連単"].set_index("race_id")["payout"]
    tan = win_payouts(pay).set_index(["race_id", "lane"])["payout"]
    # 欠場・フライング・出遅れの艇の単勝は返還 (100 円)
    for rid_, lane in res.loc[res["finish_raw"].fillna("").str[0].isin(["K", "F", "L"]), ["race_id", "lane"]].values:
        tan[(rid_, int(lane))] = 100
    rows = []
    for rid, r in pred.iterrows():
        if rid not in fin.index:
            continue
        f = fin.loc[rid]
        order = [int(f[f == k].index[0]) if (f == k).any() else None for k in (1, 2, 3)]
        actual = "-".join(str(x) for x in order)
        fav = int(r["本命"])
        tickets = r["3連単上位5点"].split()
        fav_st_rank = st.loc[rid].rank().get(fav)
        rows.append(dict(race_id=rid, 場=r["場"], R=r["R"], 本命=fav, 本命1着確率=r["本命1着確率"],
                         予想着順=r["予想着順"], 結果=actual, 決まり手=races.at[rid, "technique"] if rid in races.index
                         else None, 本命の着=f.get(fav), 本命のST順位=fav_st_rank,
                         風速=races.at[rid, "wind_speed"], 波=races.at[rid, "wave_cm"],
                         本命1着=order[0] == fav, 本命3着以内=f.get(fav) in (1, 2, 3),
                         予想着順的中=r["予想着順"] == actual, 上位5点的中=actual in tickets,
                         三連単配当=pay3.get(rid),
                         **ev_check(r, rid, tan)))
    c = pd.DataFrame(rows)
    out = os.path.join(OUT, a.date.replace("-", "") + "_check.csv")
    c.to_csv(out, index=False, encoding="utf-8-sig")
    n = len(c)
    if "単勝点数" in c:
        k = c["単勝点数"].sum()
        summary_ev = dict(単勝期待値買い_点数=int(k), 単勝期待値買い_的中=int(c["単勝的中"].sum()),
                          単勝期待値買い_回収率=c["単勝払戻"].sum() / (100 * k) if k else 0)
    else:
        summary_ev = {}
    summary = dict(date=a.date, races=n, 本命1着率=c["本命1着"].mean(), 本命3着以内率=c["本命3着以内"].mean(),
                   予想着順的中率=c["予想着順的中"].mean(), 三連単上位5点的中率=c["上位5点的中"].mean(),
                   三連単上位5点回収率=c.loc[c["上位5点的中"], "三連単配当"].sum() / (500 * n) if n else 0,
                   **summary_ev)
    print(f"{a.date}  {n}レース")
    for k, v in list(summary.items())[2:]:
        print(f"  {k}: {v:.1%}" if isinstance(v, float) else f"  {k}: {v}")
    miss = c[~c["本命1着"]]
    if len(miss):
        print("\n本命が1着を逃したレースの内訳 (原因の手がかり)")
        print("  決まり手:", miss["決まり手"].value_counts().to_dict())
        print("  本命のスタート順位 (1=一番速い):", miss["本命のST順位"].value_counts().sort_index().to_dict())
        print("  1着になった艇:", miss["結果"].str[0].value_counts().sort_index().to_dict())
        strong = miss[miss["本命1着確率"] >= 0.7]
        if len(strong):
            print(f"  堅いと見たのに負けたレース ({len(strong)}):")
            print(strong[["場", "R", "本命", "本命1着確率", "結果", "決まり手", "本命のST順位", "風速", "波"]]
                  .to_string(index=False))
    summary |= check_live(a.date, tan)
    log = os.path.join(OUT, "log.csv")
    old = pd.read_csv(log, encoding="utf-8-sig") if os.path.exists(log) else pd.DataFrame()
    if len(old) and "date" in old:
        old = old[old["date"] != a.date]  # 同じ日をもう一度 check したら置き換える
    pd.concat([old, pd.DataFrame([summary])]).to_csv(log, index=False, encoding="utf-8-sig")
    print("\n保存しました:", out, "/", log)
    print_totals(log)


def check_live(date, tan):
    """live で「買い」と表示した艇 (その艇の最後の表示) の単勝の結果。"""
    path = os.path.join(OUT, "live_log.csv")
    if not os.path.exists(path):
        return {}
    lg = pd.read_csv(path, encoding="utf-8-sig")
    lg = lg[lg["race_id"].str.startswith(date.replace("-", ""))]
    lg = lg.drop_duplicates(["race_id", "lane"], keep="last")
    buy = lg[lg["買い"] == 1]
    if buy.empty:
        return {}
    ret = np.array([tan.get((r, int(l)), 0) for r, l in zip(buy.race_id, buy.lane)])
    print(f"\nlive の買い目: {len(buy)}点  的中 {(ret > 100).sum()}  回収率 {ret.sum() / (100 * len(buy)):.1%}")
    return dict(live買い_点数=len(buy), live買い_払戻=int(ret.sum()), live買い_回収率=ret.sum() / (100 * len(buy)))


def print_totals(log):
    """これまでの答え合わせの累計 (フィードバックの確認用)。"""
    lg = pd.read_csv(log, encoding="utf-8-sig")
    print(f"\n累計 {len(lg)}日 {int(lg['races'].sum())}レース")
    w = lg["races"]
    for k in ["本命1着率", "予想着順的中率", "三連単上位5点的中率"]:
        if k in lg:
            print(f"  {k}: {(lg[k] * w).sum() / w.sum():.1%}")
    for pre, name in [("単勝期待値買い", "単勝の買い目 (予想オッズ)"), ("live買い", "live の買い目 (実オッズ)")]:
        if f"{pre}_点数" in lg:
            k = lg[f"{pre}_点数"].fillna(0).sum()
            pay = (lg[f"{pre}_回収率"].fillna(0) * lg[f"{pre}_点数"].fillna(0) * 100).sum()
            if k:
                print(f"  {name}: {int(k)}点 回収率 {pay / (100 * k):.1%}")


# ---------------- live: 直前情報とオッズで買い目を出す ----------------
def load_models(model_dir):
    """(番組表だけのモデル, 直前情報のモデル or None, オッズのモデル)"""
    pre = Predictor.load(model_dir)
    late_dir = os.path.join(model_dir, "late")
    late = Predictor.load(late_dir) if os.path.exists(late_dir) else None
    om = lgb.Booster(model_file=os.path.join(model_dir, "odds.txt"))
    om.best_iteration = 0
    return pre, late, om


def recommend(d, lv, models, stats):
    """1レース分の予想。d はそのレースの特徴量 (6行)、lv は live.race_live の結果。
    (直前情報を使ったか, 表示用の行, 記録用の行, 3連単の上位5点) を返す。"""
    pre, late, om = models
    d = d.copy()
    rid = d["race_id"].iloc[0]
    ex = {l: b["exhibition_time"] for l, b in lv["boats"].items()}
    use_late = late is not None and len([v for v in ex.values() if v]) >= 5
    if use_late:
        d["exhibition_time"] = d["lane"].map(ex).astype(float)
        d["ex_d"] = d["exhibition_time"] - d["exhibition_time"].min()
        d["ex_rk"] = d["exhibition_time"].rank(method="min")
        d["wind_speed"], d["wave_cm"] = lv["wind_speed"], lv["wave_cm"]
    pr = late if use_late else pre
    P, Q = pr.probs(d)
    _, tri = pr.orders(P, Q)
    top5 = ["-".join(str(x + 1) for x in PERMS3[k]) for k in np.argsort(-tri[0])[:5]]
    odds_hat, ev_hat = expected_value(d, P, om, V4_FEATS)
    p, oh, eh = P.values[0], odds_hat.values[0], ev_hat.values[0]
    rows, logs = [], []
    for l in range(1, 7):
        r = d[d.lane == l].iloc[0]
        o = lv["odds"].get(l)
        ev_real = p[l - 1] * o if o else None
        buy = eh[l - 1] >= EV_MIN and (ev_real is None or ev_real >= EV_MIN)
        st = stats.get(int(r["racer_no"]), {})
        course, st_ex = lv["start"].get(l, (None, None))
        rows.append({"艇": l, "選手": f"{int(r['racer_no'])} {st.get('名前', '')}", "級": r["class"],
                     "1着率": fmt_pct(st.get("1着率")), "この枠の1着率": fmt_pct(st.get(f"{l}号艇 1着率")),
                     "展示": ex.get(l), "進入": course, "展示ST": st_ex, "勝率予想": fmt_pct(p[l - 1]),
                     "予想オッズ": round(float(oh[l - 1]), 1), "期待値(予想オッズ)": round(float(eh[l - 1]), 2),
                     "オッズ": o, "期待値(実オッズ)": round(ev_real, 2) if ev_real else None,
                     "買い": "◎" if buy else ""})
        logs.append(dict(time=dt.datetime.now().isoformat(timespec="seconds"), race_id=rid, lane=l,
                         model="直前" if use_late else "前日", p=round(float(p[l - 1]), 4),
                         odds_hat=round(float(oh[l - 1]), 2), odds=o, ev_hat=round(float(eh[l - 1]), 3),
                         ev=round(ev_real, 3) if ev_real else None, 買い=int(buy)))
    return use_late, rows, logs, top5


def cmd_live(a):
    import live
    feat = day_path(a.date, "_feat.pkl")
    if not os.path.exists(feat):
        print("今日の予想がまだ無いので、先に predict を実行します")
        cmd_predict(a)
    df = pd.read_pickle(feat)
    models = load_models(a.model)
    stats = racer_stats(a)
    info = df.drop_duplicates("race_id").set_index("race_id")
    prog = program(a.date, a.raw).drop_duplicates("race_id").set_index("race_id")
    # どのレースを見るか
    if a.race:
        jcd, rno = (int(x) for x in a.race.split("-"))
        rids = [r for r in info.index if info.at[r, "jcd"] == jcd and info.at[r, "rno"] == rno]
    else:
        now = dt.datetime.now()
        rids = []
        for r in info.index:
            dl = prog.at[r, "deadline"] if r in prog.index else None
            if not isinstance(dl, str) or not dl:
                continue
            t = dt.datetime.combine(dt.date.fromisoformat(a.date), dt.time(*map(int, dl.split(":"))))
            mins = (t - now).total_seconds() / 60
            if a.all or -2 <= mins <= a.within:
                rids.append((t, r))
        rids = [r for _, r in sorted(rids)]
    if not rids:
        sys.exit(f"対象のレースがありません (締切まで {a.within} 分以内のレースだけ表示します。--all で全レース)")
    con = sqlite3.connect(a.db)
    venue = dict(con.execute("SELECT jcd, name FROM venues").fetchall())
    con.close()
    logs = []
    for rid in rids:
        jcd, rno = int(info.at[rid, "jcd"]), int(info.at[rid, "rno"])
        try:
            lv = live.race_live(jcd, rno, a.date)
        except Exception as e:  # 通信エラーなどは飛ばす
            print(f"{venue.get(jcd, jcd)} {rno}R: 取得できませんでした ({e})")
            continue
        d = df[df.race_id == rid]
        use_late, rows, lg, top5 = recommend(d, lv, models, stats)
        logs += lg
        print(f"\n■ {venue.get(jcd, jcd)} {rno}R  締切 {prog['deadline'].get(rid, '')}"
              f"  ({'直前情報あり' if use_late else '展示前: 番組表だけで予想'})"
              f"  風 {lv['wind_speed']}m 波 {lv['wave_cm']}cm")
        print(pd.DataFrame(rows).to_string(index=False))
        buys = [x["艇"] for x in rows if x["買い"]]
        print("  おすすめ: " + (f"単勝 {' '.join(map(str, buys))}" if buys else "見送り (予想オッズ・実オッズの両方で期待値1以上の艇なし)"))
        print("  3連単の上位5点: " + " ".join(top5))
    path = append_log(logs)
    if path:
        print(f"\n記録しました: {path} (翌日の check で結果を集計します)")


def append_log(logs):
    if not logs:
        return
    path = os.path.join(OUT, "live_log.csv")
    os.makedirs(OUT, exist_ok=True)
    pd.DataFrame(logs).to_csv(path, mode="a", header=not os.path.exists(path), index=False, encoding="utf-8-sig")
    return path


# ---------------- serve: Chrome 拡張機能から呼ばれるサーバー ----------------
def cmd_serve(a):
    """http://127.0.0.1:PORT/recommend?hd=20261026&jcd=1&rno=12 に直前情報とオッズを取って予想を返す。
    拡張機能 (chrome-extension フォルダ) が boatrace.jp のレースのページにこの結果を表示する。"""
    import copy
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    import live
    models = load_models(a.model)
    con = sqlite3.connect(a.db)
    venue = dict(con.execute("SELECT jcd, name FROM venues").fetchall())
    con.close()
    days, lock = {}, threading.Lock()

    def day(date):
        """その日の特徴量と選手成績 (無ければ predict を実行して作る)。"""
        if date not in days:
            b = copy.copy(a)
            b.date = date
            if not os.path.exists(day_path(date, "_feat.pkl")):
                print(f"{date} の予想がまだ無いので predict を実行します")
                try:
                    cmd_predict(b)
                except SystemExit as e:  # 番組表がまだ無いなど
                    raise RuntimeError(str(e))
            days[date] = (pd.read_pickle(day_path(date, "_feat.pkl")), racer_stats(b))
        return days[date]

    def answer(q):
        hd, jcd, rno = q["hd"][0].replace("-", ""), int(q["jcd"][0]), int(q["rno"][0])
        date = f"{hd[:4]}-{hd[4:6]}-{hd[6:]}"
        with lock:  # 予想と公式サイトへのアクセスは1つずつ
            df, stats = day(date)
            rid = f"{hd}-{jcd:02d}-{rno:02d}"
            d = df[df.race_id == rid]
            if len(d) != 6:
                return 404, {"error": f"{venue.get(jcd, jcd)} {rno}R は予想にありません (6艇そろっていない・番組表に無い)"}
            lv = live.race_live(jcd, rno, date)
            use_late, rows, logs, top5 = recommend(d, lv, models, stats)
            append_log(logs)
        buys = [x["艇"] for x in rows if x["買い"]]
        print(f"{dt.datetime.now():%H:%M:%S} {venue.get(jcd, jcd)} {rno}R  おすすめ: "
              + (f"単勝 {' '.join(map(str, buys))}" if buys else "見送り"))
        return 200, {"venue": venue.get(jcd, str(jcd)), "rno": rno, "date": date, "late": use_late,
                     "wind_speed": lv["wind_speed"], "wave_cm": lv["wave_cm"], "rows": rows, "buys": buys,
                     "top5": top5, "ev_min": EV_MIN}

    def plain(o):
        """JSON にできる形へ (numpy の数値と NaN を直す)"""
        if isinstance(o, dict):
            return {str(k): plain(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [plain(v) for v in o]
        if hasattr(o, "item"):
            o = o.item()
        if isinstance(o, float) and np.isnan(o):
            return None
        return o

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/health":
                    code, body = 200, {"ok": True}
                elif u.path == "/recommend":
                    code, body = answer(parse_qs(u.query))
                else:
                    code, body = 404, {"error": "not found"}
            except Exception as e:  # 通信エラーなどはそのまま拡張機能に表示する
                code, body = 500, {"error": f"{type(e).__name__}: {e}"}
            data = json.dumps(plain(body), ensure_ascii=False)
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"拡張機能用のサーバーを起動しました: http://127.0.0.1:{a.port}  (この画面を閉じると止まります)")
    print("boatrace.jp のレースのページ (出走表・直前情報・オッズ) を開くと、おすすめが表示されます")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def fmt_pct(x):
    return f"{x:.0%}" if isinstance(x, (float, np.floating)) and not np.isnan(x) else ""


def racer_stats(a):
    """直近1年の選手成績 (1日1回作って保存)。"""
    from racers import racer_table
    path = day_path(a.date, "_racers.pkl")
    if os.path.exists(path):
        t = pd.read_pickle(path)
    else:
        start = (dt.date.fromisoformat(a.date) - dt.timedelta(days=365)).isoformat()
        t = racer_table(a.db, start)
        os.makedirs(OUT, exist_ok=True)
        t.to_pickle(path)
    return t.to_dict("index")


# ---------------- week: 1週間分をまとめて予想 / review: 先週の振り返り ----------------
def quietly(f, *args):
    """f(*args) を画面に出さずに実行する。(成功したか, sys.exit のメッセージ) を返す。"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            f(*args)
        return True, ""
    except SystemExit as e:
        return False, str(e)
    except Exception as e:  # 通信エラーなど
        return False, f"{type(e).__name__}: {e}"


def with_date(a, date):
    return argparse.Namespace(**{**vars(a), "date": date})


def cmd_week(a):
    """今日から7日分、番組表が公開されている日をまとめて予想する。
    番組表 (出走表) は公式サイトで前日〜当日朝に公開されるので、ふつう予想できるのは今日と明日まで。
    毎朝の自動実行 (daily) で、残りの日も公開され次第その日の朝に予想される。"""
    start = dt.date.fromisoformat(a.date)
    done, frames = [], []
    for i in range(7):
        d = (start + dt.timedelta(days=i)).isoformat()
        ok, msg = quietly(cmd_predict, with_date(a, d))
        if ok:
            t = pd.read_csv(day_path(d, ".csv"), encoding="utf-8-sig", dtype={"単勝買い目": str})
            frames.append(t.assign(日付=d))
            done.append(d)
            print(f"{d}: {len(t)}レースを予想")
        else:
            print(f"{d}: {msg if 'Error' in msg else '番組表がまだ公開されていません'}")
    if not frames:
        sys.exit("予想できる日がありません")
    week = pd.concat(frames)
    week = week[["日付"] + [c for c in week.columns if c != "日付"]]
    path = os.path.join(OUT, f"week_{start:%Y%m%d}.csv")
    week.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n{len(done)}日分 {len(week)}レース → {path}")
    if "単勝買い目" in week:
        buy = week[week["単勝買い目"].fillna("").str.strip() != ""]
        print(f"単勝の買い目 (期待値 {EV_MIN} 以上) があるレース: {len(buy)}")
        cols = ["日付", "場", "R", "締切", "本命", "本命選手", "本命1着確率", "単勝買い目", "買い目の期待値"]
        print(buy[[c for c in cols if c in buy]].to_string(index=False))
    if len(done) < 7:
        print("\n残りの日は番組表が公開されたら予想できます (毎朝の自動実行で、その日の朝に予想されます)")


def trained_end(a):
    info = os.path.join(a.model, "trained.json")
    if not os.path.exists(info):
        return None
    with open(info, encoding="utf-8") as f:
        return json.load(f)["data_end"]


def cmd_review(a):
    """先週 (--date の前日までの7日間) の予想と結果を比べて、的中率・回収率・外れの原因をまとめる。
    予想していなかった日は、その日より前のデータだけで学習したモデルがあれば後から予想して比べる。
    最後にモデルを学習し直して、先週の結果を次の予想に反映する (--no-train で省略)。"""
    end = dt.date.fromisoformat(a.date) - dt.timedelta(days=1)
    days = [(end - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    print(f"先週の振り返り: {days[0]}〜{days[-1]}")
    quietly(cmd_update, a)  # 成績を DB に取り込む
    t_end = trained_end(a)
    checks, notes = [], []
    for d in days:
        if not os.path.exists(day_path(d, ".csv")):
            if t_end and t_end < d:  # 学習に使っていない日なら後から予想しても公平
                ok, msg = quietly(cmd_predict, with_date(a, d))
                notes.append(f"{d}: 予想が無かったので後から予想 ({'OK' if ok else msg})")
            else:
                notes.append(f"{d}: 予想が無く、モデルがこの日の結果を学習済みなので比べられません")
                continue
        ok, msg = quietly(cmd_check, with_date(a, d))
        if not ok:
            notes.append(f"{d}: 答え合わせできません ({msg})")
            continue
        checks.append(pd.read_csv(day_path(d, "_check.csv"), encoding="utf-8-sig").assign(日付=d))
    for n in notes:
        print("  " + n)
    if not checks:
        sys.exit("答え合わせできる日がありません")
    c = pd.concat(checks)
    lines = report(c, days, a)
    path = os.path.join(OUT, f"review_{dt.date.fromisoformat(days[-1]):%Y%m%d}.txt")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join([f"先週の振り返り {days[0]}〜{days[-1]}"] + notes + [""] + lines) + "\n")
    print("\n".join(lines))
    print("\n保存しました:", path)
    if not a.no_train:
        print("\n先週の結果も含めてモデルを学習し直します (40分〜1時間半)")
        cmd_train(a)


def report(c, days, a):
    """週の答え合わせ (_check.csv をまとめたもの) から振り返りの文章を作る。"""
    L = []
    n = len(c)
    L.append(f"■ {c['日付'].nunique()}日 {n}レース")
    L.append(f"  本命の1着率 {c['本命1着'].mean():.1%} / 3着以内 {c['本命3着以内'].mean():.1%}")
    L.append(f"  予想着順 (3連単1点) の的中率 {c['予想着順的中'].mean():.1%}")
    hit5 = c["上位5点的中"]
    L.append(f"  3連単上位5点: 的中率 {hit5.mean():.1%}  回収率 {c.loc[hit5, '三連単配当'].sum() / (500 * n):.1%}")
    if "単勝点数" in c and c["単勝点数"].sum():
        k = c["単勝点数"].sum()
        L.append(f"  単勝の買い目 (予想オッズの期待値1以上): {int(k)}点  的中 {int(c['単勝的中'].sum())}  "
                 f"回収率 {c['単勝払戻'].sum() / (100 * k):.1%}")
    lv = live_summary(days)
    if lv:
        L.append(lv)
    # 前の週と比べる
    log = os.path.join(OUT, "log.csv")
    if os.path.exists(log):
        lg = pd.read_csv(log, encoding="utf-8-sig")
        prev = lg[(lg["date"] < days[0]) & (lg["date"] >= (dt.date.fromisoformat(days[0]) - dt.timedelta(days=7)).isoformat())]
        if len(prev):
            w = prev["races"]
            L.append(f"  (その前の週: 本命の1着率 {(prev['本命1着率'] * w).sum() / w.sum():.1%}、"
                     f"予想着順の的中率 {(prev['予想着順的中率'] * w).sum() / w.sum():.1%})")
    # 自信の度合いごとの当たり方 (予想した確率と実際の1着率がずれていないか)
    L.append("\n■ 本命の1着確率ごとの実際の1着率 (ずれが大きければ自信の付け方が合っていない)")
    bands = pd.cut(c["本命1着確率"], [0, .4, .5, .6, .7, .8, 1.0])
    for b, g in c.groupby(bands, observed=True):
        L.append(f"  予想 {b.left:.0%}〜{b.right:.0%}: {len(g):4d}レース  予想の平均 {g['本命1着確率'].mean():.0%}"
                 f" → 実際 {g['本命1着'].mean():.0%}")
    # 場ごと
    L.append("\n■ 場ごとの本命の1着率 (レース数の多い順)")
    v = c.groupby("場").agg(レース=("本命1着", "size"), 本命1着率=("本命1着", "mean"), 予想=("本命1着確率", "mean"))
    for name, r in v.sort_values("レース", ascending=False).iterrows():
        L.append(f"  {name}: {int(r['レース'])}レース  {r['本命1着率']:.0%} (予想 {r['予想']:.0%})")
    # 外れの原因
    miss = c[~c["本命1着"]]
    L.append(f"\n■ 本命が1着を逃した {len(miss)}レースの原因")
    if len(miss):
        tech = miss["決まり手"].value_counts()
        L.append("  決まり手: " + "、".join(f"{k} {v}" for k, v in tech.items()))
        slow = (miss["本命のST順位"] >= 3).mean()
        L.append(f"  本命のスタートが3番手以下だった: {slow:.0%} (本命が勝ったレースでは "
                 f"{(c.loc[c['本命1着'], '本命のST順位'] >= 3).mean():.0%})")
        L.append("  1着になった艇: " + "、".join(f"{k}号艇 {v}" for k, v in miss["結果"].str[0].value_counts().sort_index().items()))
        windy = c["風速"] >= 5
        if windy.any():
            L.append(f"  風5m以上のレース: 本命の1着率 {c.loc[windy, '本命1着'].mean():.0%} ({windy.sum()}レース)、"
                     f"それ以外 {c.loc[~windy, '本命1着'].mean():.0%}")
        strong = miss[miss["本命1着確率"] >= 0.7]
        L.append(f"  堅いと見た (70%以上) のに負けた: {len(strong)}レース")
        for _, r in strong.sort_values("本命1着確率", ascending=False).head(10).iterrows():
            L.append(f"    {r['日付']} {r['場']} {r['R']}R 本命{r['本命']} ({r['本命1着確率']:.0%}) → {r['結果']} "
                     f"{r['決まり手']} ST{r['本命のST順位']:.0f}番手 風{r['風速']}m")
    L.append("\n■ フィードバック")
    L.append("  先週の結果を DB に取り込み済み。学習し直すと、モーターの入れ替え・選手の調子・場の傾向が次の予想に入る。")
    return L


def live_summary(days):
    path = os.path.join(OUT, "log.csv")
    if not os.path.exists(path):
        return None
    lg = pd.read_csv(path, encoding="utf-8-sig")
    lg = lg[lg["date"].isin(days)]
    if "live買い_点数" not in lg or not lg["live買い_点数"].fillna(0).sum():
        return None
    k = lg["live買い_点数"].fillna(0).sum()
    return f"  live・拡張機能の買い目 (実オッズ): {int(k)}点  回収率 {lg['live買い_払戻'].fillna(0).sum() / (100 * k):.1%}"


# ---------------- daily: 結果の取り込み → 答え合わせ → 再学習 ----------------
def cmd_daily(a):
    """毎日1回。昨日までの成績を DB に取り込み、昨日の予想を答え合わせし、7日ごとにモデルを学習し直す。"""
    cmd_update(a)
    yday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    if os.path.exists(day_path(yday, ".csv")):
        try:
            cmd_check(argparse.Namespace(**{**vars(a), "date": yday}))
        except SystemExit as e:  # 成績がまだ公開されていないなど
            print(e)
    if dt.date.today().weekday() == 0:  # 月曜は先週の振り返り (学習し直しは下でまとめてする)
        print("\n===== 先週の振り返り")
        try:
            cmd_review(argparse.Namespace(**{**vars(a), "date": dt.date.today().isoformat(), "no_train": True}))
        except SystemExit as e:
            print(e)
    info = os.path.join(a.model, "trained.json")
    age = None
    if os.path.exists(info):
        with open(info, encoding="utf-8") as f:
            age = (dt.date.fromisoformat(last_date(a.db)) - dt.date.fromisoformat(json.load(f)["data_end"])).days
    if age is None or age >= a.retrain_days:
        print(f"\nモデルを学習し直します (前回の学習から {age} 日)")
        cmd_train(a)
    else:
        print(f"\nモデルは {age} 日前のデータまで学習済み ({a.retrain_days} 日ごとに学習し直します)")


def cmd_racer(a):
    from racers import racer_table, show
    start = (dt.date.today() - dt.timedelta(days=a.racer_days)).isoformat()
    t = racer_table(a.db, start)
    if a.no:
        show(t, a.no)
        return
    path = os.path.join(OUT, "racers.csv")
    os.makedirs(OUT, exist_ok=True)
    t[t["出走"] >= 30].to_csv(path, encoding="utf-8-sig", float_format="%.4f")
    print(f"{(t['出走'] >= 30).sum()}人の成績 (直近 {a.racer_days} 日、30走以上) → {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "update", "train", "predict", "live", "check", "daily", "racer", "serve", "week", "review"])
    ap.add_argument("--db", default=os.path.join(ROOT, "boatrace.sqlite"))
    ap.add_argument("--raw", default=os.path.join(ROOT, "raw"))
    ap.add_argument("--model", default=os.path.join(ROOT, "model"))
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="予想・答え合わせする日 (既定: 今日)。today / yesterday / tomorrow も可")
    ap.add_argument("--days", type=int, default=730, help="train: 直近何日分で学習するか")
    ap.add_argument("--no-late", action="store_true", help="train: 直前情報のモデルを作らない (学習が半分の時間になる)")
    ap.add_argument("--within", type=int, default=30, help="live: 締切まで何分以内のレースを見るか")
    ap.add_argument("--all", action="store_true", help="live: その日の全レースを見る (時間がかかる)")
    ap.add_argument("--race", help="live: 1レースだけ。場コード-レース番号 (例: 01-12)")
    ap.add_argument("--retrain-days", type=int, default=7, help="daily: 何日ごとに学習し直すか")
    ap.add_argument("--no", type=int, help="racer: 登録番号")
    ap.add_argument("--no-train", action="store_true", help="review: 振り返りのあと学習し直さない")
    ap.add_argument("--port", type=int, default=8765, help="serve: ポート番号")
    ap.add_argument("--racer-days", type=int, default=365, help="racer: 直近何日の成績か")
    a = ap.parse_args()
    if a.date in ("today", "yesterday", "tomorrow"):
        a.date = (dt.date.today() + dt.timedelta(days={"today": 0, "yesterday": -1, "tomorrow": 1}[a.date])).isoformat()
    {"setup": cmd_setup, "update": cmd_update, "train": cmd_train, "predict": cmd_predict, "live": cmd_live,
     "check": cmd_check, "daily": cmd_daily, "racer": cmd_racer, "serve": cmd_serve, "week": cmd_week, "review": cmd_review}[a.command](a)


if __name__ == "__main__":
    main()

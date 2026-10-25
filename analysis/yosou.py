"""今後のレースを予想し、終わったら答え合わせをして、外れの原因を集計する。Windows でも動く。

  python analysis/yosou.py setup                 GitHub の Release から最新の DB (boatrace.sqlite) を取得
  python analysis/yosou.py update                DB の最終日の翌日〜昨日の成績・番組表を公式サイトから取り込む
  python analysis/yosou.py train                 直近のデータでモデルを学習し直す (model フォルダに保存)
  python analysis/yosou.py predict --date 2026-10-26   番組表を取得してその日の全レースを予想
  python analysis/yosou.py check   --date 2026-10-26   成績を取得して答え合わせ・外れの原因を集計

  毎日の流れ: predict (朝) → check (夜) → 週に1回くらい update → train で学習し直す (フィードバック)
  予想は predictions/YYYYMMDD.csv、答え合わせは predictions/YYYYMMDD_check.csv と predictions/log.csv に出る。
"""
import argparse
import datetime as dt
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
from predict import BASE_FEATS, PERMS2, PERMS3, Predictor, features, load  # noqa: E402

REPO = "lindwurm310/boat-race-db"
OUT = os.path.join(ROOT, "predictions")
HISTORY_DAYS = 400  # 選手の過去成績を作るために読む日数


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
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=a.days)).isoformat()
    warm = (dt.date.fromisoformat(start) + dt.timedelta(days=30)).isoformat()  # 最初の30日は過去成績の蓄積用
    val = (dt.date.fromisoformat(end) - dt.timedelta(days=30)).isoformat()
    raw, _ = load(a.db, start, end)
    df = features(raw)
    tr, va = df[(df.race_date >= warm) & (df.race_date < val)], df[df.race_date >= val]
    print(f"学習 {warm}〜{val} {tr.race_id.nunique()}レース / 検証 {val}〜{end} {va.race_id.nunique()}レース")
    pr = Predictor(BASE_FEATS, "v3").fit(tr, va)
    P, Q = pr.probs(va)
    win = va.pivot(index="race_id", columns="lane", values="win").loc[P.index].values
    print(f"検証期間の本命1着率 {(win[np.arange(len(P)), P.values.argmax(1)] == 1).mean():.1%}")
    pr.save(a.model)
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
    P, Q = pr.probs(df)
    ex, tri = pr.orders(P, Q)
    con = sqlite3.connect(a.db)
    venue = dict(con.execute("SELECT jcd, name FROM venues").fetchall())
    con.close()
    info = prog.drop_duplicates("race_id").set_index("race_id")
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
                     "判断": advice(float(p.max()))})
    out = pd.DataFrame(rows).sort_values(["締切", "場"])
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, a.date.replace("-", "") + ".csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")  # Excel で文字化けしないように BOM 付き
    pd.set_option("display.width", 200, "display.max_rows", 300, "display.unicode.east_asian_width", True)
    print(out[["場", "R", "締切", "本命", "本命選手", "本命1着確率", "予想着順", "3連単上位5点", "判断"]]
          .to_string(index=False))
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


def cmd_check(a):
    path = os.path.join(OUT, a.date.replace("-", "") + ".csv")
    if not os.path.exists(path):
        sys.exit(f"予想ファイルがありません: {path} (先に predict を実行)")
    pred = pd.read_csv(path, encoding="utf-8-sig").set_index("race_id")
    races, rp = results_of(a.date, a.raw)
    if races is None:
        sys.exit(f"{a.date} の成績がまだ公開されていません (公式サイトでは翌日以降)")
    res, pay = rp
    fin = res.pivot(index="race_id", columns="lane", values="finish")
    st = res.assign(st=res["start_timing"].map(lambda s: -float(s[1:]) if isinstance(s, str) and s[:1] == "F"
                                                else pd.to_numeric(s, errors="coerce")))
    st = st.pivot(index="race_id", columns="lane", values="st")
    pay3 = pay[pay.bet_type == "３連単"].set_index("race_id")["payout"]
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
                         三連単配当=pay3.get(rid)))
    c = pd.DataFrame(rows)
    out = os.path.join(OUT, a.date.replace("-", "") + "_check.csv")
    c.to_csv(out, index=False, encoding="utf-8-sig")
    n = len(c)
    summary = dict(date=a.date, races=n, 本命1着率=c["本命1着"].mean(), 本命3着以内率=c["本命3着以内"].mean(),
                   予想着順的中率=c["予想着順的中"].mean(), 三連単上位5点的中率=c["上位5点的中"].mean(),
                   三連単上位5点回収率=c.loc[c["上位5点的中"], "三連単配当"].sum() / (500 * n) if n else 0)
    print(f"{a.date}  {n}レース")
    for k, v in list(summary.items())[2:]:
        print(f"  {k}: {v:.1%}")
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
    log = os.path.join(OUT, "log.csv")
    pd.DataFrame([summary]).to_csv(log, mode="a", header=not os.path.exists(log), index=False, encoding="utf-8-sig")
    print("\n保存しました:", out, "/", log)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "update", "train", "predict", "check"])
    ap.add_argument("--db", default=os.path.join(ROOT, "boatrace.sqlite"))
    ap.add_argument("--raw", default=os.path.join(ROOT, "raw"))
    ap.add_argument("--model", default=os.path.join(ROOT, "model"))
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="予想・答え合わせする日 (既定: 今日)")
    ap.add_argument("--days", type=int, default=300, help="train: 直近何日分で学習するか")
    a = ap.parse_args()
    {"setup": cmd_setup, "update": cmd_update, "train": cmd_train, "predict": cmd_predict, "check": cmd_check}[
        a.command](a)


if __name__ == "__main__":
    main()

"""iPad などで見る静的なページ (GitHub Pages) を作る。サーバーなしで操作画面 (web/index.html) を表示する。

  python analysis/site.py --out site          predictions フォルダの予想・答え合わせ・振り返りを site/data に JSON で書き出す

GitHub Actions (.github/workflows/yosou-page.yml) が毎朝 daily → week の後にこれを実行し、gh-pages ブランチに置く。
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from server import plain  # noqa: E402


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plain(obj), f, ensure_ascii=False, separators=(",", ":"))


def rows(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"単勝買い目": str}).to_dict("records")


def iso(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=os.path.join(ROOT, "predictions"))
    ap.add_argument("--out", default=os.path.join(ROOT, "site"))
    ap.add_argument("--db", default=os.path.join(ROOT, "boatrace.sqlite"))
    ap.add_argument("--model", default=os.path.join(ROOT, "model"))
    a = ap.parse_args()
    data = os.path.join(a.out, "data")
    os.makedirs(data, exist_ok=True)
    shutil.copy(os.path.join(HERE, "web", "index.html"), os.path.join(a.out, "index.html"))
    files = sorted(os.listdir(a.pred)) if os.path.isdir(a.pred) else []
    log = pd.read_csv(os.path.join(a.pred, "log.csv"), encoding="utf-8-sig") if "log.csv" in files else None
    dates = {"predictions": [], "check": [], "week": []}
    for f in files:
        p = os.path.join(a.pred, f)
        if len(f) == 12 and f.endswith(".csv") and f[:8].isdigit():  # 20261026.csv
            d = iso(f[:8])
            dump(os.path.join(data, "predictions", d + ".json"), {"date": d, "rows": rows(p)})
            dates["predictions"].append(d)
        elif f.endswith("_check.csv") and f[:8].isdigit():
            d = iso(f[:8])
            s = None
            if log is not None:
                r = log[log["date"] == d]
                s = r.iloc[-1].to_dict() if len(r) else None
            kp = os.path.join(a.pred, f[:8] + "_kaime.csv")
            kaime = rows(kp) if os.path.exists(kp) else None
            dump(os.path.join(data, "check", d + ".json"), {"date": d, "rows": rows(p), "summary": s, "kaime": kaime})
            dates["check"].append(d)
        elif f.startswith("week_") and f.endswith(".csv"):
            d = iso(f[5:13])
            dump(os.path.join(data, "week", d + ".json"), {"date": d, "rows": rows(p)})
            dates["week"].append(d)
    if log is not None:
        dump(os.path.join(data, "totals.json"), {"rows": log.sort_values("date").to_dict("records")})
    import seiseki  # これまでの成績と備考
    s = seiseki.build(a.pred)
    if s:
        dump(os.path.join(data, "seiseki.json"), s)
    s = seiseki.build(os.path.join(a.pred, "backtest")) or seiseki.saved()  # 過去10年を予想し直した分
    if s:
        dump(os.path.join(data, "seiseki_backtest.json"), s)
    reviews = sorted([f for f in files if f.startswith("review_") and f.endswith(".txt")], reverse=True)[:20]
    items = []
    for f in reviews:
        with open(os.path.join(a.pred, f), encoding="utf-8-sig") as fh:
            items.append({"file": f, "text": fh.read()})
    dump(os.path.join(data, "reviews.json"), {"items": items})
    adv = os.path.join(HERE, "advice.md")  # 「今週のまとめ」に載せるアドバイス (手で書く)
    if os.path.exists(adv):
        with open(adv, encoding="utf-8-sig") as fh:
            dump(os.path.join(data, "advice.json"), {"text": fh.read()})
    if os.path.exists(a.db) and not any(f.endswith("_racers.pkl") for f in files):
        import yosou  # 直近1年の選手成績を作る (predictions/YYYYMMDD_racers.pkl)
        yosou.racer_stats(argparse.Namespace(date=dt.date.today().isoformat(), db=a.db))
        files = sorted(os.listdir(a.pred))
    racers = sorted([f for f in files if f.endswith("_racers.pkl")])
    if racers:  # 直近1年の選手成績 (30走以上)
        t = pd.read_pickle(os.path.join(a.pred, racers[-1]))
        t = t[t["出走"] >= 30]
        dump(os.path.join(data, "racers.json"), {"rows": [{"登録番号": k, **v} for k, v in t.to_dict("index").items()]})
    status = {"static": True, "updated": dt.datetime.now().isoformat(timespec="minutes"), "dates": dates,
              "db": os.path.exists(a.db), "model": os.path.exists(os.path.join(a.model, "win.txt"))}
    if status["db"]:
        import yosou
        status["db_last"] = yosou.last_date(a.db)
    tj = os.path.join(a.model, "trained.json")
    if os.path.exists(tj):
        with open(tj, encoding="utf-8") as f:
            status["trained"] = json.load(f)["data_end"]
    dump(os.path.join(data, "status.json"), status)
    print(f"{a.out}: 予想 {len(dates['predictions'])}日 / 答え合わせ {len(dates['check'])}日 / 振り返り {len(items)}件")


if __name__ == "__main__":
    main()

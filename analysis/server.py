"""yosou serve: Chrome から全部の機能を使うためのサーバー (http://127.0.0.1:8765/)。

  /                    操作画面 (web/index.html)。今日の予想・1週間・答え合わせ・振り返り・選手・学習
  /recommend           拡張機能が boatrace.jp のレースのページに出す、直前情報とオッズでの予想
  /api/...             操作画面が使う。重い処理 (予想・学習など) は1つずつ裏で動かし、/api/job で進み具合を返す

自分のパソコンの中 (127.0.0.1) からしか繋がらない。ほかのサイトから勝手に操作されないように、
Host ヘッダーを確かめ、処理を動かす POST には X-Yosou ヘッダーを必須にしている。
"""
import argparse
import contextlib
import datetime as dt
import io
import json
import os
import sqlite3
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")


def log(*args):
    """サーバーの画面に出す (裏の処理の記録とは混ぜない)"""
    print(f"{dt.datetime.now():%H:%M:%S}", *args, file=sys.stderr, flush=True)


def plain(o):
    """JSON にできる形へ (numpy の数値と NaN を直す)"""
    if isinstance(o, dict):
        return {str(k): plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [plain(v) for v in o]
    if hasattr(o, "item"):
        o = o.item()
    if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
        return None
    return o


def read_csv(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"単勝買い目": str}).to_dict("records")


class LogBuffer(io.TextIOBase):
    def __init__(self, job):
        self.job = job

    def write(self, s):
        with self.job["lock"]:
            self.job["log"] += s
            self.job["log"] = self.job["log"][-200_000:]
        return len(s)


class App:
    def __init__(self, a, y):
        self.a, self.y = a, y  # y: yosou モジュール
        self.models = None
        self.days = {}
        self.lock = threading.Lock()  # 直前予想 (公式サイトへのアクセス) は1つずつ
        self.job = dict(cmd=None, date=None, running=False, log="", error=None, started=None, finished=None,
                        lock=threading.Lock())
        con = sqlite3.connect(a.db) if os.path.exists(a.db) else None
        self.venue = dict(con.execute("SELECT jcd, name FROM venues").fetchall()) if con else {}
        if con:
            con.close()

    # ---------- 直前予想 (拡張機能のパネル) ----------
    def get_models(self):
        if self.models is None:
            if not os.path.exists(os.path.join(self.a.model, "win.txt")):
                raise RuntimeError("予想モデルがまだありません。操作画面 (http://127.0.0.1:8765/) の「学習・データ」で学習してください")
            self.models = self.y.load_models(self.a.model)
        return self.models

    def day(self, date):
        """その日の特徴量と選手成績 (無ければ predict を実行して作る)。"""
        y = self.y
        if date not in self.days:
            b = y.with_date(self.a, date)
            if not os.path.exists(y.day_path(date, "_feat.pkl")):
                log(f"{date} の予想がまだ無いので predict を実行します")
                ok, msg = y.quietly(y.cmd_predict, b)
                if not ok:
                    raise RuntimeError(msg)
            self.days[date] = (pd.read_pickle(y.day_path(date, "_feat.pkl")), y.racer_stats(b))
        return self.days[date]

    def recommend(self, q):
        import live
        y = self.y
        hd, jcd, rno = q["hd"][0].replace("-", ""), int(q["jcd"][0]), int(q["rno"][0])
        date = f"{hd[:4]}-{hd[4:6]}-{hd[6:]}"
        with self.lock:
            models = self.get_models()
            df, stats = self.day(date)
            d = df[df.race_id == f"{hd}-{jcd:02d}-{rno:02d}"]
            if len(d) != 6:
                return 404, {"error": f"{self.venue.get(jcd, jcd)} {rno}R は予想にありません (6艇そろっていない・番組表に無い)"}
            lv = live.race_live(jcd, rno, date)
            use_late, rows, logs, top5 = y.recommend(d, lv, models, stats)
            y.append_log(logs)
        buys = [x["艇"] for x in rows if x["買い"]]
        log(f"{self.venue.get(jcd, jcd)} {rno}R  おすすめ: " + (f"単勝 {' '.join(map(str, buys))}" if buys else "見送り"))
        return 200, {"venue": self.venue.get(jcd, str(jcd)), "rno": rno, "date": date, "late": use_late,
                     "wind_speed": lv["wind_speed"], "wave_cm": lv["wave_cm"], "rows": rows, "buys": buys,
                     "top5": top5, "ev_min": y.EV_MIN}

    # ---------- 裏で動かす処理 ----------
    def commands(self):
        y = self.y
        return {"predict": y.cmd_predict, "week": y.cmd_week, "check": y.cmd_check, "review": y.cmd_review,
                "train": y.cmd_train, "update": y.cmd_update, "daily": y.cmd_daily, "setup": y.cmd_setup}

    def run(self, body):
        cmd = body.get("cmd")
        if cmd not in self.commands():
            return 400, {"error": f"知らない処理です: {cmd}"}
        job = self.job
        with job["lock"]:
            if job["running"]:
                return 409, {"error": f"「{job['cmd']}」を実行中です。終わってからもう一度押してください"}
            date = body.get("date") or dt.date.today().isoformat()
            job.update(cmd=cmd, date=date, running=True, log="", error=None,
                       started=dt.datetime.now().isoformat(timespec="seconds"), finished=None)
        args = argparse.Namespace(**{**vars(self.a), "date": date, "no_train": not body.get("train", False)})
        threading.Thread(target=self._work, args=(cmd, args), daemon=True).start()
        log(f"開始: {cmd} {date}")
        return 200, {"ok": True}

    def _work(self, cmd, args):
        job = self.job
        try:
            with contextlib.redirect_stdout(LogBuffer(job)):
                self.commands()[cmd](args)
        except SystemExit as e:
            job["error"] = str(e) or None
        except Exception as e:
            job["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            if cmd in ("train", "daily", "review", "setup"):
                self.models = None  # 学習し直したモデルを次から使う
            self.days = {}
            with job["lock"]:
                job.update(running=False, finished=dt.datetime.now().isoformat(timespec="seconds"))
            log(f"終了: {cmd}" + (f" ({job['error']})" if job["error"] else ""))

    # ---------- 画面用のデータ ----------
    def status(self):
        y, a = self.y, self.a
        st = {"today": dt.date.today().isoformat(), "db": os.path.exists(a.db)}
        if st["db"]:
            try:
                st["db_last"] = y.last_date(a.db)
            except Exception:
                st["db_last"] = None
        st["trained"] = y.trained_end(a)
        st["model"] = os.path.exists(os.path.join(a.model, "win.txt"))
        st["late_model"] = os.path.exists(os.path.join(a.model, "late", "win.txt"))
        st["today_pred"] = os.path.exists(y.day_path(st["today"], ".csv"))
        from predict import device
        with contextlib.redirect_stdout(io.StringIO()):
            st["device"] = device()
        return st

    def job_state(self):
        with self.job["lock"]:
            return {k: v for k, v in self.job.items() if k != "lock"}

    def predictions(self, date):
        path = self.y.day_path(date, ".csv")
        if not os.path.exists(path):
            return 404, {"error": f"{date} の予想はまだありません"}
        return 200, {"date": date, "rows": read_csv(path)}

    def week(self, date):
        path = os.path.join(self.y.OUT, f"week_{date.replace('-', '')}.csv")
        if not os.path.exists(path):
            return 404, {"error": f"{date} からの1週間の予想はまだありません"}
        return 200, {"date": date, "rows": read_csv(path)}

    def check(self, date):
        path = self.y.day_path(date, "_check.csv")
        if not os.path.exists(path):
            return 404, {"error": f"{date} の答え合わせはまだありません"}
        out = {"date": date, "rows": read_csv(path)}
        kp = self.y.day_path(date, "_kaime.csv")
        if os.path.exists(kp):  # 3連単・3連複の買い方ごとの払戻
            out["kaime"] = read_csv(kp)
        lg = os.path.join(self.y.OUT, "log.csv")
        if os.path.exists(lg):
            t = pd.read_csv(lg, encoding="utf-8-sig")
            row = t[t["date"] == date]
            out["summary"] = row.iloc[-1].to_dict() if len(row) else None
        return 200, out

    def totals(self):
        lg = os.path.join(self.y.OUT, "log.csv")
        if not os.path.exists(lg):
            return 404, {"error": "答え合わせの記録がまだありません"}
        t = pd.read_csv(lg, encoding="utf-8-sig").sort_values("date")
        return 200, {"rows": t.to_dict("records")}

    def reviews(self):
        out = self.y.OUT
        files = sorted([f for f in os.listdir(out) if f.startswith("review_") and f.endswith(".txt")],
                       reverse=True) if os.path.isdir(out) else []
        items = []
        for f in files[:20]:
            with open(os.path.join(out, f), encoding="utf-8-sig") as fh:
                items.append({"file": f, "text": fh.read()})
        return 200, {"items": items}

    def racers(self, q):
        b = self.y.with_date(self.a, dt.date.today().isoformat())
        with contextlib.redirect_stdout(io.StringIO()):
            stats = self.y.racer_stats(b)
        q = (q or "").strip()
        if not q:
            return 400, {"error": "登録番号か名前を入れてください"}
        hits = []
        for no, r in stats.items():
            if q == str(no) or q.replace(" ", "").replace("　", "") in str(r.get("名前", "")).replace(" ", "").replace("　", ""):
                hits.append({"登録番号": no, **r})
            if len(hits) >= 30:
                break
        return 200, {"rows": hits}


def make_handler(app):
    port = app.a.port

    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(plain(body), ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {"error": "forbidden"})
            u = urlparse(self.path)
            q = parse_qs(u.query)
            date = (q.get("date") or [dt.date.today().isoformat()])[0]
            try:
                if u.path in ("/", "/index.html"):
                    with open(os.path.join(WEB, "index.html"), "rb") as f:
                        return self.send(200, f.read(), "text/html; charset=utf-8")
                routes = {
                    "/health": lambda: (200, {"ok": True}),
                    "/recommend": lambda: app.recommend(q),
                    "/api/status": lambda: (200, app.status()),
                    "/api/job": lambda: (200, app.job_state()),
                    "/api/predictions": lambda: app.predictions(date),
                    "/api/week": lambda: app.week(date),
                    "/api/check": lambda: app.check(date),
                    "/api/totals": app.totals,
                    "/api/reviews": app.reviews,
                    "/api/racers": lambda: app.racers((q.get("q") or [""])[0]),
                }
                if u.path not in routes:
                    return self.send(404, {"error": "not found"})
                code, body = routes[u.path]()
            except Exception as e:  # 通信エラーなどはそのまま画面に出す
                code, body = 500, {"error": f"{type(e).__name__}: {e}"}
            self.send(code, body)

        def do_POST(self):
            # ほかのサイトから送れないように: Host と独自ヘッダー (付けるとブラウザが事前確認をするので、よそからは送れない)
            if not self.host_ok() or self.headers.get("X-Yosou") != "1":
                return self.send(403, {"error": "forbidden"})
            if urlparse(self.path).path != "/api/run":
                return self.send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                code, out = app.run(body)
            except Exception as e:
                code, out = 500, {"error": f"{type(e).__name__}: {e}"}
            self.send(code, out)

        def log_message(self, *args):
            pass

    return Handler


def open_browser(url):
    """Chrome があれば Chrome で、無ければふだんのブラウザで開く。"""
    for p in (os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
              os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")):
        if os.path.exists(p):
            import subprocess
            subprocess.Popen([p, url])
            return
    webbrowser.open(url)


def run(a, y):
    app = App(a, y)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(app))
    except OSError:
        url = f"http://127.0.0.1:{a.port}/"
        print(f"サーバーはもう起動しています。{url} を開きます")
        if not a.no_open:
            open_browser(url)
        return
    url = f"http://127.0.0.1:{a.port}/"
    print(f"ボートレース予想のサーバーを起動しました: {url}")
    print("Chrome の操作画面から、予想・答え合わせ・振り返り・学習などができます。この画面を閉じるとサーバーが止まります。")
    if not a.no_open:
        threading.Timer(1.0, open_browser, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

"""テレボート自動投票の操作画面 (iPad の Safari などから使う)。

  python server.py            → http://<このパソコンのIP>:8766/?t=<合言葉>
同じ Wi-Fi の中からだけ繋がる (家の外のアドレスは断る)。最初に合言葉付きの URL を開くと、そのブラウザに覚えさせる。
毎日 設定の時刻に自動で実行する。手で「テスト」「本番」も始められる。
"""
import collections
import datetime as dt
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bot  # noqa: E402
import store  # noqa: E402

WEB = os.path.join(HERE, "web")
LOG_DIR = os.path.join(store.BASE, "logs")


def debug_dir():
    """Claude が直すときに読めるよう、デスクトップの claude フォルダがあればそこにも記録を残す"""
    d = os.path.join(os.path.expanduser("~"), "Desktop", "claude")
    return os.path.join(d, "teleboat-log") if os.path.isdir(d) else None


class App:
    def __init__(self):
        self.lines = collections.deque(maxlen=3000)
        self.lock = threading.Lock()
        self.thread = None
        self.stop = threading.Event()
        self.current = None
        self.last = {}          # 最後の実行の結果
        self.shot_at = 0
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(store.SHOTS, exist_ok=True)
        self.load_today_log()

    def load_today_log(self):
        try:
            with open(os.path.join(LOG_DIR, f"{store.today()}.log"), encoding="utf-8") as f:
                for line in f.read().splitlines()[-1000:]:
                    self.lines.append(line)
        except OSError:
            pass

    def log(self, msg):
        line = f"{dt.datetime.now():%m/%d %H:%M:%S} {msg}"
        if sys.stdout:  # pythonw で動かすときは画面が無い
            print(line, flush=True)
        with self.lock:
            self.lines.append(line)
        for d in (LOG_DIR, debug_dir()):
            if not d:
                continue
            try:
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, f"{store.today()}.log"), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def shot(self, page, name, html=None):
        stamp = dt.datetime.now().strftime("%H%M%S")
        dd = debug_dir()
        if html is not None:
            for d in (store.SHOTS, dd):
                if d:
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, f"{store.today()}_{stamp}_{name}.html"), "w", encoding="utf-8") as f:
                        f.write(html)
            return
        if page is None:
            return
        png = page.screenshot(full_page=False)
        with open(os.path.join(store.SHOTS, "latest.png"), "wb") as f:
            f.write(png)
        for d in (store.SHOTS, dd):
            if d:
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, f"{store.today()}_{stamp}_{name}.png"), "wb") as f:
                    f.write(png)
        self.shot_at = time.time()

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, mode, why="手動"):
        if self.running():
            return False, "すでに実行中です"
        s = store.load_settings()
        if mode not in ("test", "live"):
            mode = s["mode"]
        self.stop.clear()
        self.thread = threading.Thread(target=self._work, args=(s, mode, why), daemon=True)
        self.thread.start()
        return True, "開始しました"

    def _work(self, s, mode, why):
        from playwright.sync_api import sync_playwright
        r = bot.Run(s, self.log, self.shot, self.stop, mode)
        self.current = r
        t0 = time.time()
        self.last = {"start": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode, "why": why, "result": "実行中"}
        self.log(f"===== 実行 ({why})")
        try:
            with sync_playwright() as pw:
                r.go(pw)
            self.last["result"] = "完了"
        except bot.Stopped as e:
            self.log(str(e))
            self.last["result"] = "停止"
        except bot.BotError as e:
            self.log(f"止めました ({r.step}): {e}")
            self.last["result"] = f"エラー: {e}"
        except Exception as e:
            first = str(e).splitlines()[0] if str(e) else type(e).__name__
            self.log(f"思わぬエラーで止めました ({r.step}): {first}")
            self.log(traceback.format_exc()[-800:])
            self.last["result"] = f"エラー: {first}"
        finally:
            self.last["sec"] = int(time.time() - t0)
            self.log(f"===== 終わり ({self.last['result']}・{self.last['sec']}秒)")
            self.current = None

    def status(self):
        s = store.load_settings()
        st = store.load_state()
        s.pop("token", None)
        with self.lock:
            lines = list(self.lines)[-400:]
        return {
            "running": self.running(), "step": self.current.step if self.current else "",
            "settings": s, "today": st, "last": self.last, "log": lines, "shot_at": self.shot_at,
            "date": store.today(), "now": dt.datetime.now().strftime("%H:%M:%S"),
        }

    def plan(self):
        s = store.load_settings()
        try:
            rows = bot.fetch_predictions(store.today()).get("rows", [])
        except Exception as e:
            return {"error": f"今日の予想が読めません: {e}"}
        p = bot.plan(bot.picks(rows), s, store.load_state())
        return {k: v for k, v in p.items()}

    def scheduler(self):
        """毎日 auto_time に1回だけ実行する (パソコンが起きていれば、時刻を過ぎてから起きても 2 時間以内なら実行)"""
        while True:
            try:
                s = store.load_settings()
                if s["auto"]:
                    now = dt.datetime.now()
                    h, m = map(int, s["auto_time"].split(":"))
                    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    st = store.load_state()
                    if at <= now < at + dt.timedelta(hours=2) and not st.get("auto_ran") and not self.running():
                        st["auto_ran"] = True
                        store.save_state(st)
                        self.start(s["mode"], "毎日の自動実行")
            except Exception as e:
                self.log(f"自動実行の確認でエラー: {e}")
            time.sleep(20)


TAILSCALE = ipaddress.ip_network("100.64.0.0/10")  # Tailscale (外出先から VPN で繋ぐとき) のアドレス


def private(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local or (a.version == 4 and a in TAILSCALE)
    except ValueError:
        return False


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def authed(self, q):
            tok = store.load_settings()["token"]
            c = SimpleCookie(self.headers.get("Cookie", ""))
            if "tb" in c and c["tb"].value == tok:
                return True, None
            if q.get("t", [""])[0] == tok:
                return True, f"tb={tok}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict"
            return False, None

        def guard(self):
            if not private(self.client_address[0]):
                self.send(403, {"error": "家のネットワークの外からは使えません"})
                return None
            u = urlparse(self.path)
            q = parse_qs(u.query)
            ok, cookie = self.authed(q)
            if not ok:
                self.send(401, "合言葉付きの URL (パソコンのデスクトップの「iPad用の自動投票.url」) から開いてください".encode(),
                          "text/plain; charset=utf-8")
                return None
            return u, q, cookie

        def do_GET(self):
            g = self.guard()
            if not g:
                return
            u, q, cookie = g
            try:
                if u.path == "/":
                    with open(os.path.join(WEB, "index.html"), "rb") as f:
                        self.send(200, f.read(), "text/html; charset=utf-8", {"Set-Cookie": cookie} if cookie else None)
                elif u.path == "/api/status":
                    self.send(200, app.status())
                elif u.path == "/api/plan":
                    self.send(200, app.plan())
                elif u.path == "/api/shot.png":
                    p = os.path.join(store.SHOTS, "latest.png")
                    if not os.path.exists(p):
                        self.send(404, {"error": "まだ画面写真がありません"})
                        return
                    with open(p, "rb") as f:
                        self.send(200, f.read(), "image/png")
                else:
                    self.send(404, {"error": "ありません"})
            except Exception as e:
                self.send(500, {"error": str(e)})

        def do_POST(self):
            g = self.guard()
            if not g:
                return
            u, _, _ = g
            if self.headers.get("X-TB") != "1":
                self.send(403, {"error": "不正なリクエストです"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/run":
                    ok, msg = app.start(body.get("mode"))
                    self.send(200 if ok else 409, {"ok": ok, "message": msg})
                elif u.path == "/api/stop":
                    app.stop.set()
                    self.send(200, {"ok": True})
                elif u.path == "/api/settings":
                    store.save_settings(body)
                    app.log("設定を保存しました")
                    self.send(200, {"ok": True})
                elif u.path == "/api/reset_today":
                    st = store.load_state()
                    st["auto_ran"] = False
                    store.save_state(st)
                    self.send(200, {"ok": True})
                else:
                    self.send(404, {"error": "ありません"})
            except ValueError as e:
                self.send(400, {"error": str(e)})
            except Exception as e:
                self.send(500, {"error": str(e)})

        def log_message(self, *a):
            pass
    return H


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))  # 実際には送らない。外向きに使うアドレスを知るだけ
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def tailscale_ip():
    """Tailscale が入っていれば、このパソコンの Tailscale のアドレス (100.x.x.x)"""
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ipaddress.ip_address(ip) in TAILSCALE:
                return ip
    except OSError:
        pass
    return None


def write_shortcut(url):
    """デスクトップに iPad で開く URL を書いたファイルを置く"""
    try:
        os.makedirs(store.BASE, exist_ok=True)
        with open(os.path.join(store.BASE, "panel_url.txt"), "w", encoding="utf-8") as f:
            f.write(url + "\n")
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(desk):
            with open(os.path.join(desk, "iPad用の自動投票.url"), "w", encoding="utf-8") as f:
                f.write(f"[InternetShortcut]\nURL={url}\n")
    except OSError:
        pass


def main():
    app = App()
    s = store.load_settings()
    port = s["port"]
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(app))
    url = f"http://{lan_ip()}:{port}/?t={s['token']}"
    write_shortcut(url)
    app.log(f"操作画面を開きました: http://{lan_ip()}:{port}/ (合言葉付きの URL はデスクトップの「iPad用の自動投票.url」)")
    ts = tailscale_ip()
    if ts:
        out = f"http://{ts}:{port}/?t={s['token']}"
        try:
            with open(os.path.join(store.BASE, "panel_url_outside.txt"), "w", encoding="utf-8") as f:
                f.write(out + "\n")
            desk = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.isdir(desk):
                with open(os.path.join(desk, "外出先用の自動投票.url"), "w", encoding="utf-8") as f:
                    f.write(f"[InternetShortcut]\nURL={out}\n")
        except OSError:
            pass
        app.log(f"外出先からは Tailscale 経由で http://{ts}:{port}/ (合言葉付きの URL はデスクトップの「外出先用の自動投票.url」)")
    threading.Thread(target=app.scheduler, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()

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
import secrets
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
        self.check_cache = {}
        self.fix_1117()
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

    def start(self, mode, why="手動", only=None, stake=None):
        if self.running():
            return False, "すでに実行中です"
        s = store.load_settings()
        if mode not in ("test", "live"):
            mode = s["mode"]
        self.stop.clear()
        self.thread = threading.Thread(target=self._work, args=(s, mode, why, only, stake), daemon=True)
        self.thread.start()
        return True, "開始しました"

    # ---- 購入前の確認 ----
    # 本番で買う前に、今の設定で買う舟券を state["approval"] に置いてパネルに出す。
    # 「購入する」を押すと、その買い目だけ (同じ1点の金額で) 本番で買う。日が変わると消える
    def prepare(self, why="手動"):
        if self.running():
            return False, "すでに実行中です"
        p = self.plan()
        if p.get("error"):
            return False, p["error"]
        rows = sorted(p["want"], key=lambda x: -x.get("score", x["ev"]))
        if not rows:
            return False, "今買える買い目がありません"
        keep = ("key", "deadline", "venue", "rno", "boat", "prob", "odds", "ev")
        ap = {"id": secrets.token_hex(6), "why": why, "at": dt.datetime.now().strftime("%H:%M"),
              "stake": p["stake"], "total": p["stake"] * len(rows), "strategy": p.get("strategy", ""),
              "note": p.get("note", ""), "rows": [{k: x.get(k) for k in keep} for x in rows]}
        st = store.load_state()
        st["approval"] = ap
        store.save_state(st)
        self.log(f"購入前の確認 ({why}): " + "、".join(f"{x['venue']}{x['rno']}R 単勝{x['boat']}" for x in rows) +
                 f" ・1点{ap['stake']:,}円・合計 {ap['total']:,}円。パネルで「購入する」を押すと買います")
        return True, "買う舟券を出しました。確かめて「購入する」を押してください"

    def approve(self, aid):
        st = store.load_state()
        ap = st.get("approval")
        if not ap or ap.get("id") != aid:
            return False, "確認の内容が古くなっています。もう一度「本番実行」を押してください"
        if self.running():
            return False, "すでに実行中です"
        st.pop("approval", None)
        store.save_state(st)
        return self.start("live", f"{ap['why']}・確認して購入", [x["key"] for x in ap["rows"]], ap["stake"])

    def reject(self):
        st = store.load_state()
        if st.pop("approval", None):
            store.save_state(st)
            self.log("購入前の確認で「やめる」を押したので買いません")
        return True, "やめました"

    def _work(self, s, mode, why, only=None, stake=None):
        from playwright.sync_api import sync_playwright
        r = bot.Run(s, self.log, self.shot, self.stop, mode, only, stake)
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
            "version": bot.VERSION, "date": store.today(), "now": dt.datetime.now().strftime("%H:%M:%S"),
        }

    def plan(self):
        s = store.load_settings()
        try:
            rows = bot.fetch_predictions(store.today()).get("rows", [])
        except Exception as e:
            return {"error": f"今日の予想が読めません: {e}"}
        p = bot.plan(bot.picks(rows, s), s, store.load_state())
        p["strategy"] = bot.STRATEGIES.get(s.get("strategy", "value"))
        return {k: v for k, v in p.items()}

    def fix_1117(self):
        """11/17 は購入を記録する前の版で買ったので、ログに残った 10点をあとから記録する (1回だけ)"""
        st = store.load_state("2026-11-17")
        if st.get("bought") or not st.get("deposit") or st.get("fixed_1117"):
            return
        keys = ["13-10-5", "15-08-4", "01-05-5", "01-10-4", "01-02-2", "15-02-4", "15-03-3", "01-09-1", "15-01-1", "15-11-3"]
        st["bought"] = sorted(f"20261117-{k}" for k in keys)
        st["spent"] = st.get("spent", 0) + 1000
        st["fixed_1117"] = True
        store.save_state(st, "2026-11-17")
        self.log("11/17 にログで確かめた 10点・1,000円 を購入の記録に加えました")

    def results(self, days=60):
        """自動購入した舟券の的中と金額。答え合わせ (翌朝) が出た日だけ当たり外れが分かる"""
        s = store.load_settings()
        try:
            names = sorted((f[:-5] for f in os.listdir(store.STATE_DIR) if f.endswith(".json")), reverse=True)[:days]
        except OSError:
            names = []
        out, tot = [], {"stake": 0, "settled": 0, "payout": 0, "n": 0, "hit": 0, "pending": 0}
        for date in names:
            st = store.load_state(date)
            tickets = st.get("tickets") or []
            known = {t["key"] for t in tickets}
            for k in st.get("bought", []):  # 古い版の記録 (買い目のキーだけ)
                if k in known:
                    continue
                d8, jcd, rno, boat = k.split("-")
                tickets.append({"key": k, "race_id": f"{d8}-{jcd}-{rno}", "venue": bot.JYO.get(jcd, jcd), "rno": int(rno),
                                "boat": boat, "stake": s["stake"]})
            if not tickets:
                continue
            chk = self.check_cache.get(date)
            if chk is None and date < store.today():
                try:
                    chk = bot.fetch_check(date)
                except Exception:
                    chk = None
                if chk:
                    self.check_cache[date] = chk
            res = {r["race_id"]: r for r in (chk or {}).get("rows", [])}
            day = {"date": date, "stake": 0, "settled": 0, "payout": 0, "n": 0, "hit": 0, "pending": 0, "tickets": []}
            for t in sorted(tickets, key=lambda t: t.get("deadline") or ""):
                r = res.get(t["race_id"])
                first = str((r or {}).get("結果") or "").split("-")[0]
                t = dict(t)
                if not r or not first:
                    t["result"] = "結果待ち"
                    t["payout"] = 0
                    day["pending"] += 1
                else:
                    win = first == str(t["boat"])
                    if win and r.get("単勝配当") is None:  # 配当がまだ入っていない
                        t["result"] = "的中 (配当待ち)"
                        t["payout"] = 0
                        day["pending"] += 1
                        day["hit"] += 1
                    else:
                        t["result"] = "的中" if win else f"外れ ({r.get('結果')})"
                        t["payout"] = int(r["単勝配当"] * t["stake"] / 100) if win else 0
                        day["hit"] += win
                        day["settled"] += t["stake"]
                day["stake"] += t["stake"]
                day["payout"] += t["payout"]
                day["n"] += 1
                day["tickets"].append(t)
            for k in ("stake", "settled", "payout", "n", "hit", "pending"):
                tot[k] += day[k]
            out.append(day)
        return {"days": out, "total": tot}

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
                        if s["mode"] == "live" and s.get("confirm", True):
                            self.prepare("毎日の自動実行")  # 本番は買い目を出して「購入する」を待つ
                        else:
                            self.start(s["mode"], "毎日の自動実行")
            except Exception as e:
                self.log(f"自動実行の確認でエラー: {e}")
            time.sleep(20)


TAILSCALE = ipaddress.ip_network("100.64.0.0/10")  # Tailscale (外出先から VPN で繋ぐとき) のアドレス


class Fails:
    """合言葉の間違いが 10 分に 20 回を超えたら、10 分間は合言葉での入室を断る (覚えたブラウザはそのまま使える)"""
    def __init__(self):
        self.t = collections.deque()
        self.lock = threading.Lock()

    def add(self):
        with self.lock:
            self.t.append(time.time())

    def blocked(self):
        with self.lock:
            while self.t and time.time() - self.t[0] > 600:
                self.t.popleft()
            return len(self.t) >= 20


FAILS = Fails()


LOGIN_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="apple-mobile-web-app-capable" content="yes">
<title>自動投票</title><style>
:root{--bg:#f4f5f7;--card:#fff;--fg:#1d2330;--mut:#6b7280;--line:#e3e6eb;--acc:#1764d8;--ng:#c62828}
@media (prefers-color-scheme: dark){:root{--bg:#111418;--card:#1b2027;--fg:#e8eaee;--mut:#9aa3af;--line:#2c333d;--acc:#5b9cff;--ng:#ff6b6b}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,"Hiragino Sans","Yu Gothic UI",sans-serif}
form{max-width:420px;margin:12vh auto 0;padding:24px 16px;background:var(--card);border:1px solid var(--line);border-radius:14px;display:grid;gap:12px}
h1{font-size:20px;margin:0}input{font:inherit;padding:12px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button{font:inherit;padding:14px;border:0;border-radius:12px;background:var(--acc);color:#fff;min-height:52px}p{margin:0;color:var(--mut);font-size:14px}.e{color:var(--ng)}
</style></head><body><form method="post" action="/login">
<h1>テレボート自動投票</h1>
<p>パネルのパスワードを入れてください（設定で決めたもの）。まだ決めていなければ、合言葉（URL の t= の後ろ）でも入れます。</p>
<input name="pw" type="password" autocomplete="current-password" autofocus required>
<button>開く</button>%MSG%
</form><script>
// 予想サイトから #site=… 付きで開いたとき、ログインの後も設定を受け取れるようにする
if (location.hash.startsWith("#site=")) { try { sessionStorage.setItem("siteHash", location.hash.slice(1)); } catch (e) {} document.forms[0].action = "/login" + location.hash; }
</script></body></html>"""


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
            import hmac
            tok = store.load_settings()["token"]
            c = SimpleCookie(self.headers.get("Cookie", ""))
            if "tb" in c and hmac.compare_digest(c["tb"].value, tok):
                return True, None
            if FAILS.blocked():
                return False, None
            given = q.get("t", [""])[0]
            if given and hmac.compare_digest(given, tok):
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" or self.headers.get("Tailscale-Funnel-Request") else ""
                return True, f"tb={tok}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}"
            if given:
                FAILS.add()
            return False, None

        def guard(self):
            ip = self.client_address[0]
            if self.path == "/version" and ip in ("127.0.0.1", "::1") and not self.headers.get("Tailscale-Funnel-Request"):
                self.send(200, {"version": bot.VERSION, "pid": os.getpid()})
                return None
            # 更新のとき、インストーラーがこのパソコンの中から止める (管理者で起動した古い画面も止められるように)
            if self.path == "/shutdown" and self.command == "POST" and ip in ("127.0.0.1", "::1") \
                    and not self.headers.get("Tailscale-Funnel-Request") and self.headers.get("X-TB") == "1":
                self.send(200, {"ok": True})
                app.stop.set()
                app.log("更新のため操作画面を止めます")
                threading.Thread(target=lambda: (time.sleep(1), os._exit(0)), daemon=True).start()
                return None
            if ip.startswith("::ffff:"):  # IPv4 を IPv6 の形で受けたとき
                ip = ip[7:]
            if not private(ip):
                app.log(f"家のネットワークと Tailscale の外 ({ip}) からの接続を断りました")
                self.send(403, {"error": f"家のネットワークと Tailscale の外からは使えません (接続元 {ip})"})
                return None
            u = urlparse(self.path)
            q = parse_qs(u.query)
            ok, cookie = self.authed(q)
            if not ok and u.path == "/login" and self.command == "POST":
                return u, q, None
            if not ok:
                if u.path == "/" or u.path == "/login":
                    self.send(200, LOGIN_HTML.replace("%MSG%", "").encode(), "text/html; charset=utf-8")
                else:
                    self.send(401, {"error": "ログインしてください"})
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
                elif u.path == "/api/results":
                    self.send(200, app.results())
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
                    if u.path.startswith("/api/"):
                        self.send(404, {"error": "ありません"})
                    else:  # /login などを開いたとき (ホーム画面に保存したアドレスなど) はトップへ
                        self.send_response(303)
                        self.send_header("Location", "/")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
            except Exception as e:
                self.send(500, {"error": str(e)})

        def do_POST(self):
            g = self.guard()
            if not g:
                return
            u, _, _ = g
            if u.path == "/login":
                self.login()
                return
            if self.headers.get("X-TB") != "1":
                self.send(403, {"error": "不正なリクエストです"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/run":
                    s = store.load_settings()
                    if body.get("mode") == "live" and s.get("confirm", True):
                        ok, msg = app.prepare("手動")  # 本番はまず買い目を出して確かめてもらう
                    else:
                        ok, msg = app.start(body.get("mode"))
                    self.send(200 if ok else 409, {"ok": ok, "message": msg})
                elif u.path == "/api/approve":
                    ok, msg = app.approve(str(body.get("id") or ""))
                    self.send(200 if ok else 409, {"ok": ok, "message": msg})
                elif u.path == "/api/reject":
                    ok, msg = app.reject()
                    self.send(200, {"ok": ok, "message": msg})
                elif u.path == "/api/stop":
                    app.stop.set()
                    self.send(200, {"ok": True})
                elif u.path == "/api/settings":
                    store.save_settings(body)
                    app.log("設定を保存しました")
                    self.send(200, {"ok": True})
                elif u.path == "/api/new_token":
                    # 合言葉を作り直す。このブラウザには新しい合言葉を覚えさせ、デスクトップの URL も書き直す
                    tok = store.new_token()
                    secure = "; Secure" if self.headers.get("Tailscale-Funnel-Request") or self.headers.get("X-Forwarded-Proto") == "https" else ""
                    write_urls(app)
                    app.log("合言葉を作り直しました (前の URL は使えなくなりました)")
                    self.send(200, {"ok": True, "token": tok},
                              extra={"Set-Cookie": f"tb={tok}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}"})
                elif u.path == "/api/reset_deposit":
                    if app.running():
                        raise ValueError("実行中は消せません。止めてから押してください")
                    st = store.load_state()
                    old = st.get("deposit", 0)
                    st["deposit"] = 0
                    st.pop("deposit_pending", None)
                    store.save_state(st)
                    app.log(f"今日の入金の記録 ({old:,}円) を消しました")
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

        def login(self):
            """固定の URL で開いたときのログイン (パネルのパスワード、または合言葉)"""
            import hmac
            n = int(self.headers.get("Content-Length") or 0)
            pw = parse_qs(self.rfile.read(min(n, 4096)).decode("utf-8", "replace")).get("pw", [""])[0].strip()
            tok = store.load_settings()["token"]
            if FAILS.blocked():
                msg = "間違いが続いたので、10分ほど待ってからもう一度試してください"
            elif pw and (store.check_panel_password(pw) or hmac.compare_digest(pw, tok)):
                secure = "; Secure" if self.headers.get("Tailscale-Funnel-Request") or self.headers.get("X-Forwarded-Proto") == "https" else ""
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"tb={tok}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            else:
                FAILS.add()
                app.log("パネルのログインでパスワードが違いました")
                msg = "パスワードが違います"
            self.send(200, LOGIN_HTML.replace("%MSG%", f'<p class="e">{msg}</p>').encode(), "text/html; charset=utf-8")

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


def tailscale_dns():
    """Tailscale のこのパソコンの名前 (xxx.tailnet.ts.net)。Funnel を使うときの https の宛先"""
    import shutil
    import subprocess
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    if not os.path.exists(exe):
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        name = (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
        return name or None
    except Exception:
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


def write_urls(app, first=False):
    s = store.load_settings()
    port = s["port"]
    url = f"http://{lan_ip()}:{port}/?t={s['token']}"
    write_shortcut(url)
    if first:
        app.log(f"操作画面を開きました: http://{lan_ip()}:{port}/ (合言葉付きの URL はデスクトップの「iPad用の自動投票.url」)")
    ts = tailscale_ip()
    dns = tailscale_dns()
    if dns:  # Tailscale Funnel (インターネットから https で届く) を使うときの URL
        out = f"https://{dns}/?t={s['token']}"
    elif ts:
        out = f"http://{ts}:{port}/?t={s['token']}"
    if dns or ts:
        try:
            with open(os.path.join(store.BASE, "panel_url_outside.txt"), "w", encoding="utf-8") as f:
                f.write(out + "\n")
            desk = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.isdir(desk):
                with open(os.path.join(desk, "外出先用の自動投票.url"), "w", encoding="utf-8") as f:
                    f.write(f"[InternetShortcut]\nURL={out}\n")
        except OSError:
            pass
        if first:
            app.log(f"外出先からは {out.split('?')[0]} (合言葉付きの URL はデスクトップの「外出先用の自動投票.url」)")


def main():
    app = App()
    s = store.load_settings()
    port = s["port"]
    class Server(ThreadingHTTPServer):
        allow_reuse_address = False
    try:
        srv = Server(("0.0.0.0", port), make_handler(app))
    except OSError as e:
        app.log(f"操作画面を起動できませんでした (8766 番がほかで使われています): {e}")
        raise
    write_urls(app, first=True)
    threading.Thread(target=app.scheduler, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()

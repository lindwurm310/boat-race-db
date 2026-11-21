"""yosou tunnel: 予想サーバーを Cloudflare の無料トンネル (trycloudflare.com) で外から開けるようにする。

  yosou tunnel --setup    最初の1回。パスワードを作り、URL を送る Gmail の設定を聞いて remote.json に保存する
  yosou tunnel            サーバー (パスワード付き) と cloudflared を起動し、URL が決まるたびに
                          デスクトップの「予想サイトのURL.txt」に書き、Gmail で自分に送る。落ちたら起動し直す

remote.json (インストール先、例: %LOCALAPPDATA%\\BoatRaceYosou\\remote.json) の中身:
  password            外から開くときのパスワード (ユーザー名は何でもよい)
  gmail_user          送る Gmail のアドレス
  gmail_app_password  Google の「アプリ パスワード」(16文字。ふだんのパスワードではない)
  mail_to             送り先 (省略すると gmail_user)
メールにはパスワードは書かない (URL だけ)。
"""
import datetime as dt
import getpass
import json
import os
import re
import secrets
import shutil
import smtplib
import subprocess
import sys
import threading
import time
import urllib.request
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONF = os.path.join(ROOT, "remote.json")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def log(*args):
    print(f"{dt.datetime.now():%m-%d %H:%M:%S}", *args, flush=True)


def load():
    if os.path.exists(CONF):
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(c):
    with open(CONF, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


def setup():
    c = load()
    if not c.get("password"):
        c["password"] = secrets.token_urlsafe(12)
    print(f"外から開くときのパスワード: {c['password']}  (ユーザー名は何でもよい。{CONF} に保存)")
    print("URL を Gmail で自分に送ります。Google アカウントの「アプリ パスワード」が要ります")
    print("  (https://myaccount.google.com/apppasswords 。2段階認証をオンにすると作れます)")
    u = input(f"Gmail のアドレス [{c.get('gmail_user', '')}]: ").strip() or c.get("gmail_user", "")
    if u:
        c["gmail_user"] = u
        p = getpass.getpass("アプリ パスワード (16文字、入力は表示されません。変えないときは Enter): ").replace(" ", "")
        if p:
            c["gmail_app_password"] = p
        to = input(f"送り先 [{c.get('mail_to') or u}]: ").strip()
        if to:
            c["mail_to"] = to
    save(c)
    if c.get("gmail_user") and c.get("gmail_app_password"):
        send_mail(c, "ボートレース予想: メールの設定ができました", "サーバーの URL が変わるたびに、ここへ送ります。")
        print("テストのメールを送りました。届いているか確かめてください")


def send_mail(c, subject, body):
    if not (c.get("gmail_user") and c.get("gmail_app_password")):
        return False
    m = MIMEText(body, "plain", "utf-8")
    m["Subject"], m["From"], m["To"] = subject, c["gmail_user"], c.get("mail_to") or c["gmail_user"]
    for i in range(5):  # 起動直後はネットがまだのことがある
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
                s.login(c["gmail_user"], c["gmail_app_password"])
                s.send_message(m)
            return True
        except Exception as e:
            log("メールを送れませんでした:", type(e).__name__, e)
            time.sleep(30 * (i + 1))
    return False


def desktop():
    d = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")
    return d if os.path.isdir(d) else ROOT


def find_cloudflared():
    p = shutil.which("cloudflared")
    if p:
        return p
    for d in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links")):
        for name in (os.path.join(d, "cloudflared", "cloudflared.exe"), os.path.join(d, "cloudflared.exe")):
            if d and os.path.exists(name):
                return name
    raise SystemExit("cloudflared が見つかりません。winget install Cloudflare.cloudflared で入れてください")


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def keep_server(a, c):
    """サーバーが動いていなければ (パスワード付きで) 起動する。落ちたら起動し直す"""
    env = dict(os.environ, YOSOU_PASSWORD=c["password"])
    cmd = [sys.executable, os.path.join(HERE, "yosou.py"), "serve", "--no-open", "--port", str(a.port),
           "--allow-host", ".trycloudflare.com"]
    while True:
        if not healthy(a.port):
            log("サーバーを起動します")
            subprocess.Popen(cmd, env=env, cwd=ROOT)
            time.sleep(20)
        time.sleep(30)


def run(a):
    c = load()
    if not c.get("password"):
        c["password"] = secrets.token_urlsafe(12)
        save(c)
        log(f"パスワードを作りました: {c['password']} ({CONF})")
    if healthy(a.port):
        log("注意: サーバーはもう動いています。tunnel.bat 以外 (server.bat など) で起動したサーバーだと、"
            "パスワードが違ったり外から開けなかったりします。そのときはサーバーの画面を閉じてください (自動で起動し直します)")
    threading.Thread(target=keep_server, args=(a, c), daemon=True).start()
    exe = find_cloudflared()
    last = None
    while True:
        log("cloudflared を起動します")
        p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{a.port}"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            m = URL_RE.search(line)
            if m and m.group(0) != last:
                last = url = m.group(0)
                now = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
                log("URL:", url)
                with open(os.path.join(desktop(), "予想サイトのURL.txt"), "w", encoding="utf-8") as f:
                    f.write(f"{url}\n\n{now} から。ユーザー名は何でもよく、パスワードは {CONF} にあります\n")
                body = (f"予想サイトの新しい URL です ({now} から)\n\n{url}\n\n"
                        "ユーザー名は何でもよく、パスワードはいつものものです。PC やサーバーを起動し直すと URL が変わり、またこのメールが届きます。")
                threading.Thread(target=send_mail, args=(c, "ボートレース予想: 新しい URL", body), daemon=True).start()
        p.wait()
        log(f"cloudflared が止まりました (終了コード {p.returncode})。30秒後に起動し直します")
        time.sleep(30)


def main(a):
    if a.setup:
        setup()
    else:
        run(a)

"""自動投票パネルの自動更新。

GitHub のブランチ (インストーラーが覚えた update_branch.txt。無ければ main) の teleboat/bot.py の VERSION を見て、
動いている版と違えば、そのブランチの zip を取って teleboat の中身を入れ替え、操作画面を自分で起動し直す。
- 実行中 (ログイン〜購入の途中) は入れ替えない。終わってから次の確認で入れ替える
- 入れ替える前に、新しいプログラムが読み込めるか別の Python で確かめる。だめなら入れ替えない
- 入れ替える前のファイルは .prev に残す
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

import store

REPO = "lindwurm310/boat-race-db"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.dirname(store.BASE)  # %LOCALAPPDATA%\BoatRaceYosou
KEEP = {".venv", ".prev", "start.bat", "__pycache__"}  # 入れ替えないもの


def branch():
    try:
        with open(os.path.join(DEST, "update_branch.txt"), encoding="utf-8-sig") as f:
            b = f.read().strip()
            if b:
                return b
    except OSError:
        pass
    return "main"


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "teleboat-panel-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def remote_version(br=None):
    """GitHub にある版 (bot.py の VERSION)。読めなければ例外"""
    br = br or branch()
    src = _get(f"https://raw.githubusercontent.com/{REPO}/{br}/teleboat/bot.py?t={int(time.time())}").decode("utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        raise RuntimeError("GitHub の bot.py に版が書いてありません")
    return m.group(1)


def download(br=None):
    """ブランチの zip を取って、teleboat の中身を一時フォルダに出す。(一時フォルダ, teleboat のフォルダ, installer のフォルダ)"""
    br = br or branch()
    data = _get(f"https://github.com/{REPO}/archive/refs/heads/{br}.zip", timeout=120)
    tmp = tempfile.mkdtemp(prefix="teleboat_update_")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    roots = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
    if not roots:
        raise RuntimeError("取った zip が空です")
    root = os.path.join(tmp, roots[0])
    src = os.path.join(root, "teleboat")
    if not os.path.isfile(os.path.join(src, "server.py")):
        raise RuntimeError("取った zip に teleboat がありません")
    return tmp, src, os.path.join(root, "installer")


def check_loads(src):
    """新しいプログラムが読み込めるか、別の Python で確かめる (動いている画面には影響しない)"""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    code = "import sys; sys.path.insert(0, sys.argv[1]); import store, bot, server; print(bot.VERSION)"
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    r = subprocess.run([sys.executable, "-c", code, src], cwd=src, env=env, capture_output=True, text=True,
                       timeout=60, creationflags=flags)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["?"]
        raise RuntimeError(f"新しい版が読み込めないので入れ替えません: {tail[0][:200]}")
    return r.stdout.strip()


def install(src, installer):
    """teleboat の中身を入れ替える。前のファイルは .prev に残す"""
    prev = os.path.join(APP_DIR, ".prev")
    shutil.rmtree(prev, ignore_errors=True)
    os.makedirs(prev, exist_ok=True)
    for name in os.listdir(APP_DIR):
        if name in KEEP:
            continue
        p = os.path.join(APP_DIR, name)
        shutil.move(p, os.path.join(prev, name))
    for name in os.listdir(src):
        if name in KEEP:
            continue
        s, d = os.path.join(src, name), os.path.join(APP_DIR, name)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    ps1 = os.path.join(installer, "teleboat_panel.ps1")
    if os.path.isfile(ps1):  # 「自動投票パネルを更新」が使うインストーラーも新しくする
        try:
            shutil.copy2(ps1, os.path.join(DEST, "teleboat_panel.ps1"))
        except OSError:
            pass


def restart():
    """操作画面を起動し直す (新しいプロセスを出してから、このプロセスを終える)。
    新しい方は 8766 番が空くまで待ってから受け付ける (server.main)"""
    args = [sys.executable, os.path.join(APP_DIR, "server.py")]
    env = dict(os.environ, PYTHONUTF8="1", TELEBOAT_RESTARTED="1")
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        subprocess.Popen(args, cwd=APP_DIR, env=env, creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(args, cwd=APP_DIR, env=env, start_new_session=True, close_fds=True)

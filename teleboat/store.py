"""設定・パスワード・今日の購入記録をパソコンの中に保存する。

保存先: %LOCALAPPDATA%\\BoatRaceYosou\\teleboat\\ (Windows 以外は ~/.boatrace-teleboat/)
  settings.json  金額・上限・自動実行の時刻など。パスワード類は Windows の DPAPI で暗号化して入れる
                 (同じパソコンの同じユーザーでしか元に戻せない)。GitHub にも外にも送らない。
  state/YYYY-MM-DD.json  その日に買えた買い目・入金額 (二重に買わない・上限を守るため)
"""
import base64
import datetime as dt
import json
import os
import secrets
import sys
import threading

if os.name == "nt":
    BASE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BoatRaceYosou", "teleboat")
else:
    BASE = os.path.join(os.path.expanduser("~"), ".boatrace-teleboat")
BASE = os.environ.get("TELEBOAT_HOME", BASE)
SETTINGS = os.path.join(BASE, "settings.json")
STATE_DIR = os.path.join(BASE, "state")
SHOTS = os.path.join(BASE, "shots")
SECRET_KEYS = ("memberNo", "pin", "authPassword", "betPassword")

DEFAULTS = {
    "stake": 100,          # 1点の金額 (円)
    "cap": 4000,           # 1日の上限 (円)。入金も購入もこれを超えない
    "auto": True,          # 毎日 auto_time に自動で実行する
    "auto_time": "09:30",
    "mode": "test",        # test = 購入の直前で止める / live = 購入まで行う
    "deposit": True,       # 残高が足りなければ不足分を入金する (本番のときだけ)
    "headless": True,      # Edge の画面を出さずに動かす
    "port": 8766,
    # 買う舟券の選び方 (単勝)
    #   model = 予想モデルの買い目 (期待値が1を超えたもの) を期待値の高い順
    #   hit   = 的中率の高い順 (各レースの1着確率が一番高い艇)
    #   roi   = 回収率 (期待値 = 1着確率 × 予想オッズ) の高い順。全部の艇から選ぶ
    #   long  = 高配当狙い (予想オッズが高い艇のうち、期待値が条件を満たすもの)
    "strategy": "value",
    "min_prob": 0.0,       # 1着確率がこれ未満の艇は買わない (0〜1)
    "min_ev": 1.0,         # 期待値がこれ未満の艇は買わない (hit 以外)
    "min_odds": 1.0,       # 予想オッズがこれ未満の艇は買わない
    "per_race": 1,         # 1レースで買う最大の点数
}

_lock = threading.Lock()


def _protect(data: bytes) -> str:
    if os.name != "nt":
        return "b64:" + base64.b64encode(data).decode()
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("パスワードを暗号化できませんでした")
    enc = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return "dpapi:" + base64.b64encode(enc).decode()


def _unprotect(s: str) -> str:
    if not s:
        return ""
    kind, _, b = s.partition(":")
    raw = base64.b64decode(b)
    if kind == "b64":
        return raw.decode()
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(raw, len(raw))
    src = BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("保存したパスワードを読めませんでした。設定画面で入れ直してください")
    dec = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return dec.decode()


def _read():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(d):
    os.makedirs(BASE, exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS)


def load_settings():
    """パスワード類は「設定済みかどうか」だけ返す"""
    with _lock:
        d = _read()
        if not d.get("token"):
            d["token"] = secrets.token_urlsafe(12)
            _write(d)
    out = {k: d.get(k, v) for k, v in DEFAULTS.items()}
    out["token"] = d["token"]
    out["secrets_set"] = {k: bool(d.get("secret_" + k)) for k in SECRET_KEYS}
    out["panel_pw_set"] = bool(d.get("panel_pw"))
    return out


def new_token():
    with _lock:
        d = _read()
        d["token"] = secrets.token_urlsafe(16)
        _write(d)
        return d["token"]


def _pw_hash(pw, salt):
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000).hex()


def set_panel_password(pw):
    if len(pw) < 6:
        raise ValueError("パネルのパスワードは6文字以上にしてください")
    salt = secrets.token_bytes(16)
    with _lock:
        d = _read()
        d["panel_pw"] = {"salt": salt.hex(), "hash": _pw_hash(pw, salt)}
        _write(d)


def check_panel_password(pw):
    import hmac
    p = _read().get("panel_pw")
    if not p or not pw:
        return False
    return hmac.compare_digest(_pw_hash(pw, bytes.fromhex(p["salt"])), p["hash"])


def get_secret(key):
    return _unprotect(_read().get("secret_" + key, ""))


def save_settings(new):
    """new: 画面から来た値。パスワード類は空なら今のまま"""
    with _lock:
        d = _read()
        for k, v in DEFAULTS.items():
            if k not in new:
                continue
            x = new[k]
            if isinstance(v, bool):
                x = bool(x)
            elif isinstance(v, int):
                x = int(x)
            elif isinstance(v, float):
                x = float(x)
            d[k] = x
        if d.get("stake", 100) < 100 or d.get("stake", 100) % 100:
            raise ValueError("1点の金額は100円単位にしてください")
        if d.get("cap", 4000) < d.get("stake", 100):
            raise ValueError("1日の上限は1点の金額以上にしてください")
        if d.get("strategy") not in ("value", "model", "hit", "roi", "long"):
            d["strategy"] = "value"
        if not 1 <= d.get("per_race", 1) <= 6:
            raise ValueError("1レースの点数は1〜6にしてください")
        if d.get("mode") not in ("test", "live"):
            d["mode"] = "test"
        for k in SECRET_KEYS:
            v = (new.get(k) or "").strip()
            if v:
                if not v.isalnum() or len(v) > 16:
                    raise ValueError(f"{k} は英数字で入れてください")
                d["secret_" + k] = _protect(v.encode())
        if new.get("panel_password"):
            pw = str(new["panel_password"])
            if len(pw) < 6:
                raise ValueError("パネルのパスワードは6文字以上にしてください")
            salt = secrets.token_bytes(16)
            d["panel_pw"] = {"salt": salt.hex(), "hash": _pw_hash(pw, salt)}
        if new.get("clear_secrets"):
            for k in SECRET_KEYS:
                d.pop("secret_" + k, None)
        _write(d)


def today():
    return dt.date.today().isoformat()


def load_state(date=None):
    p = os.path.join(STATE_DIR, f"{date or today()}.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"bought": [], "deposit": 0, "spent": 0}


def save_state(st, date=None):
    os.makedirs(STATE_DIR, exist_ok=True)
    p = os.path.join(STATE_DIR, f"{date or today()}.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)

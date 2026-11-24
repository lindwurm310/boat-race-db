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
    # ---- 全体 (メインパネル)。上限は投票パネルごとに決める ----
    "auto": True,          # 毎日 auto_time に自動で実行する
    "auto_time": "09:30",
    "mode": "test",        # test = 購入の直前で止める / live = 購入まで行う
    "deposit": True,       # 残高が足りなければ不足分を入金する (本番のときだけ)
    "confirm": True,       # 本番で買う前に、買う舟券をパネルに出して「購入する」を押すまで待つ (毎日の自動実行も)
    "headless": True,      # Edge の画面を出さずに動かす
    "port": 8766,
}

PANELS = 3  # 投票パネルの数
VALUE_POINTS = 3
# 投票パネルごとの設定 (settings.json の "panels" に3つ並べる)
PANEL_DEFAULTS = {
    "name": "",            # パネルの名前 (空なら「投票パネルN」)
    "on": False,           # この投票パネルで買う
    "budget": 1000,        # 1回の投票で使う金額 (円)。点数の舟券に振り分ける
    "points": 3,           # 1回に買う最大の点数
    "weighted": True,      # 期待値が高い舟券ほど多く振り分ける (False = 均等)
    "month_max": 0,        # このパネルの1か月の上限 (円)。0 = 決めない
    # 毎日の自動実行 (パネルごと。前の版の全体の設定を引き継ぐ)
    "auto": True,          # 毎日 auto_time に、このパネルだけで実行する
    "auto_time": "09:30",
    "mode": "test",        # test = 購入の直前で止める / live = 購入まで行う
    # 買う舟券の選び方 (単勝)
    #   value = 回収率重視 (締切11:00以降・期待値1.1以上・1着確率10%以上の上位3点)
    #   month = 月の予算でやりくり (1か月の予算を残りの日数で割って、その日の予算で回収率重視と同じ条件の上位を買う)
    #   site  = 予想サイトの「おすすめ舟券」で決めた条件
    #   model = 予想モデルの買い目 (期待値が1を超えたもの) を期待値の高い順
    #   hit   = 的中率の高い順 (各レースの1着確率が一番高い艇)
    #   roi   = 回収率 (期待値 = 1着確率 × 予想オッズ) の高い順。全部の艇から選ぶ
    #   long  = 高配当狙い (予想オッズが高い艇のうち、期待値が条件を満たすもの)
    "strategy": "value",
    "min_prob": 0.0,       # 1着確率がこれ未満の艇は買わない (0〜1)
    "min_ev": 1.0,         # 期待値がこれ未満の艇は買わない (hit 以外)
    "min_odds": 1.0,       # 予想オッズがこれ未満の艇は買わない
    "per_race": 1,         # 1レースで買う最大の点数
    # strategy = "site" のとき (予想サイトの「おすすめ舟券」で決めた条件)
    "site_ev": 1.1,        # 期待値がこれ以上
    "site_prob": 0.10,     # 1着確率がこれ以上 (0〜1)
    "site_budget": 0,      # 1日の予算 (円)。0 = 決めない (1点100円)
    "site_points": 0,      # 1日の点数。0 = 決めない
    "site_from": "",       # 締切がこの時刻 (HH:MM) より前のレースは買わない。空 = しぼらない
    # strategy = "month" のとき
    "month_budget": 30000, # 1か月の投資金額 (円)
    "month_points": 3,     # 1日に買う最大の点数
    "month_reinvest": False,  # 今月の払戻 (答え合わせが出た分) を予算に足す
}
STRATEGY_KEYS = ("value", "month", "site", "model", "hit", "roi", "long")
LEGACY_PANEL_KEYS = ("stake", "strategy", "min_prob", "min_ev", "min_odds", "per_race",
                     "site_ev", "site_prob", "site_budget", "site_points", "site_from")

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


def _panels(d):
    """投票パネルの設定。前の版 (選び方が1つだけ) の設定は投票パネル1に引き継ぐ"""
    raw = d.get("panels")
    if not isinstance(raw, list):
        first = {k: d[k] for k in LEGACY_PANEL_KEYS if k in d}
        first["on"] = True
        if "cap" in d:
            first["cap"] = d["cap"]
        raw = [first]
    out = []
    for i in range(PANELS):
        p = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        q = {k: p.get(k, v) for k, v in PANEL_DEFAULTS.items()}
        for k in ("auto", "auto_time", "mode"):  # 前の版は全体で1つ
            if k not in p and k in d:
                q[k] = d[k]
        if "budget" not in p:  # 前の版 (1点の金額と1日の上限) から: 1点の金額 × 3点、1日の上限を超えない
            q["budget"] = max(100, min(int(p.get("stake", 100)) * VALUE_POINTS, int(p.get("cap", 1000))))
        out.append(q)
    return out


def panel_name(p, i):
    return (p.get("name") or "").strip() or f"投票パネル{i}"


def load_settings():
    """パスワード類は「設定済みかどうか」だけ返す"""
    with _lock:
        d = _read()
        if not d.get("token"):
            d["token"] = secrets.token_urlsafe(12)
            _write(d)
    out = {k: d.get(k, v) for k, v in DEFAULTS.items()}
    out["panels"] = _panels(d)
    # 1日に入金・購入する上限 = オンの投票パネルの1日の上限の合計 (全体の上限は無い)
    # (前の版の1日の上限の代わり。「月の予算」のパネルは月の予算を入れる。実際の入金は足りない分だけ)
    out["cap"] = sum(p["month_budget"] if p["strategy"] == "month" else p["budget"] for p in out["panels"] if p["on"])
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


def _typed(v, x):
    if isinstance(v, bool):
        return bool(x)
    if isinstance(v, int):
        return int(x)
    if isinstance(v, float):
        return float(x)
    return str(x if x is not None else "")


def _check_panel(p, i):
    import re
    name = f"投票パネル{i}"
    p["name"] = str(p.get("name") or "").strip()[:20]
    if p["budget"] < 100 or p["budget"] % 100:
        raise ValueError(f"{name}: 1回の投票で使う金額は100円以上・100円単位にしてください")
    if not 1 <= p["points"] <= 20:
        raise ValueError(f"{name}: 点数は1〜20にしてください")
    if p["strategy"] not in STRATEGY_KEYS:
        p["strategy"] = "value"
    if not 1 <= p["per_race"] <= 6:
        raise ValueError(f"{name}: 1レースの点数は1〜6にしてください")
    if not 0 <= p["site_prob"] <= 1 or p["site_ev"] < 0:
        raise ValueError(f"{name}: サイトの設定の期待値・1着確率がおかしいです")
    if p["site_budget"] < 0 or p["site_points"] < 0:
        raise ValueError(f"{name}: サイトの設定の予算・点数は0以上にしてください")
    if p["month_max"] < 0 or p["month_max"] % 100:
        raise ValueError(f"{name}: 1か月の上限は100円単位にしてください (0 = 決めない)")
    if p["month_budget"] < 0 or p["month_budget"] % 100:
        raise ValueError(f"{name}: 1か月の投資金額は100円単位にしてください")
    if not 1 <= p["month_points"] <= 12:
        raise ValueError(f"{name}: 1日の点数は1〜12にしてください")
    if p["mode"] not in ("test", "live"):
        p["mode"] = "test"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(p.get("auto_time") or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"{name}: 自動実行の時刻は 09:30 のように入れてください")
    p["auto_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    sf = str(p.get("site_from") or "").strip()
    if sf:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", sf)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError(f"{name}: サイトの設定の時刻は 11:00 のように入れてください")
        sf = f"{int(m.group(1)):02d}:{m.group(2)}"
    p["site_from"] = sf
    return p


def save_settings(new):
    """new: 画面から来た値。パスワード類は空なら今のまま。
    投票パネルは new["panel"] (1〜3) と、そのパネルの値 (PANEL_DEFAULTS のキー) で変える"""
    with _lock:
        d = _read()
        panels = _panels(d)
        if "panel" not in new:  # 全体の設定 (投票パネルの保存のときは触らない。"cap" などが同じ名前なので)
            for k, v in DEFAULTS.items():
                if k in new:
                    d[k] = _typed(v, new[k])
        else:
            i = int(new["panel"])
            if not 1 <= i <= PANELS:
                raise ValueError("投票パネルの番号がおかしいです")
            p = panels[i - 1]
            for k, v in PANEL_DEFAULTS.items():
                if k in new:
                    p[k] = _typed(v, new[k])
        for i, p in enumerate(panels, 1):
            _check_panel(p, i)
        if d.get("mode") not in ("test", "live"):
            d["mode"] = "test"
        for k in LEGACY_PANEL_KEYS + ("cap", "month_cap"):  # 投票パネルに引き継いだ・やめた全体の上限は消す
            d.pop(k, None)
        d["panels"] = panels
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


def state_dates():
    try:
        return sorted(f[:-5] for f in os.listdir(STATE_DIR) if f.endswith(".json") and len(f) == 15)
    except OSError:
        return []


def day_tickets(st, date):
    """その日に買った舟券 (投票パネルの番号つき)。前の版の記録は投票パネル1として数える"""
    tickets = [dict(t, panel=int(t.get("panel") or 1)) for t in (st.get("tickets") or [])]
    known = {t["key"] for t in tickets}
    old = [k for k in st.get("bought", []) if k not in known]  # とても古い版の記録 (買い目のキーだけ)
    if old:
        rest = st.get("spent", 0) - sum(t.get("stake", 0) for t in tickets)
        each = max(100, rest // len(old) // 100 * 100) if rest > 0 else 100
        for k in old:
            d8, jcd, rno, boat = k.split("-")
            tickets.append({"key": k, "race_id": f"{d8}-{jcd}-{rno}", "jcd": jcd, "rno": int(rno), "venue": "",
                            "boat": boat, "stake": each, "panel": 1, "old": True})
    return tickets

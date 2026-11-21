"""テレボート (ib.mbrace.or.jp) で、今日の予想の単勝買い目を買う。Playwright で Edge を動かす。

流れ: ログイン → 開催一覧 → (足りなければ入金) → 投票画面で期待値の高い順にベットリストへ → 投票入力完了
      → 購入確認画面で金額・投票用パスワードを入れて「投票する」
mode が "test" のときは、購入確認画面で入れるところまでで止める (入金もしない)。

守ること:
  - 1日の上限 (cap) を超えて買わない・入金しない。買えた分は state に記録して二重に買わない
  - 操作ごとに 1.5〜2 秒あけ、文字は1文字ずつ打つ (速いとテレボートが「連続して操作された」エラーを出す)
  - パスワード類はログ・画面写真の保存先に書かない
"""
import datetime as dt
import json
import math
import os
import random
import re
import time
import urllib.error
import urllib.request

import store

TOP_URL = os.environ.get("TELEBOAT_URL", "https://ib.mbrace.or.jp/")  # テストのときだけ差し替える
CHECK_URL = os.environ.get("TELEBOAT_CHECK_URL", "https://lindwurm310.github.io/boat-race-db/data/check/{date}.json")  # 答え合わせ (翌朝できる)
PRED_URL = os.environ.get("TELEBOAT_PRED_URL", "https://lindwurm310.github.io/boat-race-db/data/predictions/{date}.json")
MARGIN_MIN = 3  # 締切まで3分を切ったレースは買わない
VERSION = "2026-11-21b"  # ログの最初に出す (更新が入ったかを確かめるため)
# テレボートの画面に出る場名 (予想の「琵琶湖」は画面では「びわこ」)
JYO = dict(zip([f"{i:02d}" for i in range(1, 25)], "桐生 戸田 江戸川 平和島 多摩川 浜名湖 蒲郡 常滑 津 三国 びわこ 住之江 尼崎 鳴門 丸亀 児島 宮島 徳山 下関 若松 芦屋 福岡 唐津 大村".split()))


class Stopped(Exception):
    pass


class BotError(Exception):
    pass


# ---------------- 予想 → 買い目 ----------------
def fetch_check(date):
    """その日の答え合わせ (着順・単勝配当)。まだ無ければ None"""
    req = urllib.request.Request(CHECK_URL.format(date=date) + f"?t={int(time.time())}",
                                 headers={"Cache-Control": "no-cache", "User-Agent": "boatrace-teleboat"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch_predictions(date):
    req = urllib.request.Request(PRED_URL.format(date=date) + f"?t={int(time.time())}",
                                 headers={"Cache-Control": "no-cache", "User-Agent": "boatrace-teleboat"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


STRATEGIES = {
    "site": "サイトの設定 (予想サイトの「おすすめ舟券」で決めた条件)",
    "value": "回収率重視 (締切11:00以降・期待値1.1以上・1着確率10%以上のうち、期待値の上位3点。1点の金額は設定のとおり)",
    "model": "予想モデルの買い目を期待値の高い順",
    "hit": "的中率の高い順 (各レースの本命)",
    "roi": "回収率 (期待値) の高い順",
    "long": "高配当狙い (予想オッズが高い順)",
}


def _num(v):
    try:
        x = float(v)
        return x if x == x else None  # NaN を除く
    except (TypeError, ValueError):
        return None


def picks(rows, settings=None):
    """予想の行から、1点ずつの単勝の買い目を作る。settings の strategy で選び方を変える。
    各買い目: key, race_id, jcd, rno, venue, deadline, boat, prob (1着確率), odds (予想オッズ), ev (期待値), score (並べる値)"""
    s = settings or {}
    strategy = s.get("strategy", "value")
    if strategy == "value":  # 過去2年の検証で回収率が一番安定して高かった条件 (analysis/README.md)
        # 過去5年の検証 (analysis/osusume/README.md) で、その日の上位3点×300円が回収率と安定のバランスが一番良かった
        strategy, min_prob, min_ev = "roi", 0.10, 1.1
        min_odds, per_race = 0.0, 6
    elif strategy == "site":  # 予想サイトの「おすすめ舟券」と同じ: 全部の艇から、期待値の高い順 (1レースの点数は決めない)
        strategy, min_prob, min_ev = "roi", float(s.get("site_prob", 0.1) or 0), float(s.get("site_ev", 1.1) or 0)
        min_odds, per_race = 0.0, 6
    else:
        min_prob, min_ev = float(s.get("min_prob", 0) or 0), float(s.get("min_ev", 1.0) or 0)
        min_odds, per_race = float(s.get("min_odds", 1.0) or 0), int(s.get("per_race", 1) or 1)
    out = []
    for r in rows:
        _, jcd, rno = r["race_id"].split("-")
        cands = []
        for b in range(1, 7):
            prob, odds = _num(r.get(f"{b}号艇")), _num(r.get(f"{b}号艇の予想オッズ"))
            ev = prob * odds if prob is not None and odds is not None else None
            cands.append({"boat": str(b), "prob": prob, "odds": odds, "ev": ev})
        by_boat = {c["boat"]: c for c in cands}
        if strategy == "model":
            boats = str(r.get("単勝買い目") or "").split()
            evs = str(r.get("買い目の期待値") or "").split()
            chosen = []
            for i, b in enumerate(boats):
                c = dict(by_boat.get(b) or {"boat": b, "prob": None, "odds": None})
                c["ev"] = _num(evs[i]) if i < len(evs) else c.get("ev")
                c["score"] = c["ev"] or 0
                chosen.append(c)
        else:
            ok = [c for c in cands if c["prob"] is not None and c["prob"] >= min_prob
                  and (c["odds"] is None or c["odds"] >= min_odds)]
            if strategy == "hit":
                ok = sorted(ok, key=lambda c: -c["prob"])
                for c in ok:
                    c["score"] = c["prob"]
            else:
                ok = [c for c in ok if c["ev"] is not None and c["ev"] >= min_ev]
                key = "ev" if strategy == "roi" else "odds"
                ok = sorted(ok, key=lambda c: -c[key])
                for c in ok:
                    c["score"] = c[key]
            chosen = ok
        for c in chosen[:per_race]:
            out.append({"key": f"{r['race_id']}-{c['boat']}", "race_id": r["race_id"], "jcd": jcd, "rno": int(rno),
                        "venue": r.get("場", ""), "deadline": r.get("締切", ""), "boat": c["boat"],
                        "prob": c.get("prob"), "odds": c.get("odds"), "ev": c.get("ev") or 0.0, "score": c.get("score") or 0.0})
    return sorted(out, key=lambda p: p["deadline"])


VALUE_POINTS, VALUE_FROM = 3, "11:00"  # 「回収率重視」の点数・締切の時刻 (1点の金額は設定の stake)


def plan(all_picks, settings, state, balance=None, now=None):
    """締切前・まだ買っていない買い目から、上限 (と残高) に収まる分を期待値の高い順に選ぶ"""
    stake, cap = settings["stake"], settings["cap"]
    now = now or dt.datetime.now()
    cut = (now + dt.timedelta(minutes=MARGIN_MIN)).strftime("%H:%M")
    bought = set(state.get("bought", []))
    open_ = [p for p in all_picks if p["deadline"] > cut and p["key"] not in bought]
    frm = {"site": settings.get("site_from") or "", "value": VALUE_FROM}.get(settings.get("strategy"), "")
    late = [p for p in open_ if not frm or p["deadline"] >= frm]  # 締切がこの時刻より前のレースは除く
    by_ev = sorted(late, key=lambda p: -p.get("score", p["ev"]))  # 選び方の値の高い順
    room = max(0, cap - state.get("spent", 0))
    note = ""
    if settings.get("strategy") == "value":
        settings = dict(settings, site_budget=VALUE_POINTS * stake, site_points=VALUE_POINTS, site_from=VALUE_FROM)  # 1点 = 設定の金額
    if settings.get("strategy") in ("site", "value"):
        # サイトと同じ計算:
        #   点数を決めたとき: 1点 = 予算 ÷ 点数 (100円単位で切り捨て、最低100円)。点数 = min(点数, 予算/100)。
        #     候補が少ない日も1点の金額は上げない。今日買った分を差し引いても1点の金額は同じ
        #   点数を決めないとき: 残りの予算を候補数で割る。予算も決めないときは1点100円
        # 1日の上限 (cap) は超えない
        budget, points = int(settings.get("site_budget") or 0), int(settings.get("site_points") or 0)
        spent = state.get("spent", 0)
        n = len(by_ev)
        if budget and points:
            pts = min(points, budget // 100)
            stake = max(100, budget // pts // 100 * 100) if pts else 100
            n = min(n, max(0, pts - len(bought)), max(0, budget - spent) // stake)
        elif points:
            stake = 100
            n = min(n, max(0, points - len(bought)))
        elif budget:
            n = min(n, max(0, budget - spent) // 100)
            stake = max(100, (budget - spent) // n // 100 * 100) if n else 100
        else:
            stake = 100
        if n * stake > room:
            note = f"1日の上限 {cap:,}円 を超えるので {room // stake}点 に減らしました"
            n = room // stake
        want = by_ev[:n]
    else:
        want = by_ev[: room // stake]
    n = len(want) if balance is None else min(len(want), max(0, balance) // stake)
    return {
        "want": want,                 # 上限だけで選んだ分 (入金額を決める)
        "buy": want[:n],              # 残高にも収まる分
        "dropped": by_ev[len(want):],
        "closed": [p for p in all_picks if p["deadline"] <= cut and p["key"] not in bought],
        "bought": [p for p in all_picks if p["key"] in bought],
        "room": room, "balance": balance, "spent": state.get("spent", 0),
        "stake": stake, "note": note,
    }


def digits(s):
    v = re.sub(r"[^0-9]", "", s or "")
    return int(v) if v else None


# ---------------- 実行 ----------------
class Run:
    def __init__(self, settings, log, shot, stop_event, mode=None, only=None, stake=None):
        self.s = settings
        self.mode = mode or settings["mode"]
        self.only = set(only) if only is not None else None  # パネルで確かめた買い目のキー。これ以外は買わない
        self.only_stake = stake                               # 確かめたときの1点の金額。違ったら止める
        self.log = log            # log(str)
        self.shot_cb = shot       # shot(page, name)
        self.stop = stop_event
        self.page = None
        self.date = store.today()
        self.step = "準備中"

    # ---- 共通の操作 ----
    def check_stop(self):
        if self.stop.is_set():
            raise Stopped("停止しました")

    def pause(self, a=1.5, b=2.0):
        end = time.time() + random.uniform(a, b)
        while time.time() < end:
            self.check_stop()
            time.sleep(0.1)

    def wait(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.check_stop()
            time.sleep(0.2)

    def shot(self, name):
        try:
            self.shot_cb(self.page, name)
        except Exception as e:  # 写真が撮れなくても続ける
            self.log(f"(画面写真を撮れませんでした: {e})")

    def vis(self, sel):
        return self.page.locator(sel).locator("visible=true").first

    def is_vis(self, sel):
        try:
            return self.page.locator(sel).locator("visible=true").count() > 0
        except Exception:
            return False

    def popup_text(self):
        try:
            if self.is_vis("#errorPop"):
                return re.sub(r"\s+", "", self.page.locator("#errorPop").first.inner_text())
        except Exception:
            pass
        return ""

    def close_busy(self):
        """「直前の処理が完了する前に連続して操作された」が出ていたら閉じて 10 秒待つ"""
        t = self.popup_text()
        if "連続して操作" not in t:
            return False
        self.log("「連続して操作された」と出たので閉じて10秒待ちます")
        for sel in ("#errorPop #ok", "#errorPop #close", "#errorPop a"):
            if self.is_vis(sel):
                self.vis(sel).click()
                break
        self.wait(10)
        return True

    def click(self, sel, what=None, timeout=15000):
        self.pause()
        loc = self.vis(sel)
        try:
            loc.wait_for(state="visible", timeout=timeout)
        except Exception:
            raise BotError(f"{what or sel} が見つかりません")
        loc.click()

    def type_in(self, sel, value, what, secret=False):
        """1文字ずつ打つ (速すぎるとエラーになるので)"""
        self.pause(0.8, 1.2)
        loc = self.vis(sel)
        try:
            loc.wait_for(state="visible", timeout=15000)
        except Exception:
            raise BotError(f"{what}の欄が見つかりません")
        loc.click()
        loc.fill("")
        loc.press_sequentially(str(value), delay=random.randint(120, 200))
        loc.dispatch_event("change")
        self.log(f"{what}を入れました" + ("" if secret else f": {value}"))

    def wait_until(self, fn, what, sec=15):
        end = time.time() + sec
        while time.time() < end:
            self.check_stop()
            try:
                if fn():
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        raise BotError(what)

    def with_retry(self, fn, *a):
        try:
            return fn(*a)
        except BotError:
            if not self.close_busy():
                raise
            return fn(*a)

    # ---- 本体 ----
    def go(self, pw):
        self.log(f"開始 [版 {VERSION}] ({'テスト: 購入の直前で止めます' if self.mode == 'test' else '本番: 購入まで行います'})"
                 f"・1点{self.s['stake']}円・1日の上限{self.s['cap']:,}円")
        for k, label in (("memberNo", "加入者番号"), ("pin", "暗証番号"), ("authPassword", "認証用パスワード"), ("betPassword", "投票用パスワード")):
            if not store.get_secret(k):
                raise BotError(f"{label}が設定されていません (設定で保存してください)")
        self.step = "予想を読む"
        try:
            rows = fetch_predictions(self.date).get("rows", [])
        except Exception as e:
            raise BotError(f"今日 ({self.date}) の予想が読めませんでした: {e}")
        allp = picks(rows, self.s)
        if self.only is not None:
            gone = len(self.only - {x["key"] for x in allp})
            allp = [x for x in allp if x["key"] in self.only]
            self.log(f"確認した買い目 {len(self.only)}点だけを買います" + (f" (予想から消えた {gone}点は買いません)" if gone else ""))
        self.log(f"選び方: {STRATEGIES.get(self.s.get('strategy', 'value'))}")
        state = store.load_state(self.date)
        p = plan(allp, self.s, state)
        if self.s.get("strategy") == "value":
            self.s["stake"] = p["stake"]
            self.log(f"回収率重視: 締切{VALUE_FROM}以降のレースから期待値の上位{VALUE_POINTS}点を1点{p['stake']:,}円 (設定の1点の金額)")
            if p["note"]:
                self.log(p["note"])
        if self.s.get("strategy") == "site":
            self.s["stake"] = p["stake"]  # サイトの予算から決まる1点の金額
            self.log(f"サイトの設定: 期待値{self.s['site_ev']}以上・1着確率{round(self.s['site_prob'] * 100)}%以上・"
                     f"予算 {self.s['site_budget'] and format(self.s['site_budget'], ',') + '円' or '決めない'}・"
                     f"点数 {self.s['site_points'] or '決めない'}・締切 {self.s.get('site_from') or '決めない'}{'以降' if self.s.get('site_from') else ''} → 1点{p['stake']:,}円")
            if p["note"]:
                self.log(p["note"])
        if self.only_stake is not None and self.only_stake != self.s["stake"]:
            raise BotError(f"1点の金額が確認したとき ({self.only_stake:,}円) と今 ({self.s['stake']:,}円) で違うので止めます。もう一度確認してください")
        self.log(f"今日の買い目 {len(allp)}点・買える {len(p['want'])}点・締切済み {len(p['closed'])}点・"
                 f"買った {len(p['bought'])}点 ({state.get('spent', 0):,}円)")
        if not p["want"]:
            self.log("買う買い目がありません")
            return

        browser = self.launch(pw)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ja-JP")
            self.login(ctx)
            self.step = "開催一覧"
            self.wait_until(lambda: self.is_vis("#currentBetLimitAmount"), "ログイン後の画面が出ません", 40)
            self.shot("01_top")
            bal = self.balance()
            self.log(f"購入限度額 (残高) {bal:,}円")
            p = plan(allp, self.s, state, bal)
            need = len(p["want"]) * self.s["stake"]
            if bal < need:
                if self.mode != "live" or not self.s.get("deposit", True):
                    self.log(f"残高が {need - bal:,}円 足りません ({'テストなので入金しません' if self.mode != 'live' else '自動入金がオフ'})")
                    if self.mode != "live":
                        p["buy"] = p["want"]  # テストは残高に関係なく入れてみる
                else:
                    self.deposit_for(need, bal, state)
                    bal = self.balance()
                    p = plan(allp, self.s, state, bal)
            if not p["buy"]:
                self.log("残高が無いので買いません")
                return
            buy = sorted(p["buy"], key=lambda x: -x.get("score", x["ev"]))  # 選び方の値の高い順
            self.log("買う順: " + "、".join(f"{x['venue']}{x['rno']}R-{x['boat']}({x.get('score', x['ev']):.2f})" for x in buy))
            self.step = "投票画面"
            first = buy[0]
            self.click(f"#jyo{first['jcd']} a", f"開催一覧の{first['venue']}")
            self.wait_until(lambda: "/betcom/" in self.page.url or self.is_vis("#betkati1"), "投票画面が開きません", 30)
            added = []
            for x in buy:
                self.check_stop()
                try:
                    self.with_retry(self.add_bet, x)
                    added.append(x)
                    self.log(f"ベットリストに入れました {x['venue']}{x['rno']}R 単勝{x['boat']} {self.s['stake']}円")
                except BotError as e:
                    self.log(f"入れられませんでした {x['venue']}{x['rno']}R 単勝{x['boat']}: {e}")
                    self.close_popups()
            self.shot("10_betlist")
            if not added:
                raise BotError("ベットリストに1点も入りませんでした")
            count, total = self.bet_count(), self.total_amount()
            self.log(f"ベットリスト {count}点・合計 {total:,}円")
            if total != len(added) * self.s["stake"]:
                raise BotError(f"合計 {total:,}円 が入れた分 ({len(added) * self.s['stake']:,}円) と合わないので止めます")
            if total > p["room"]:
                raise BotError(f"合計 {total:,}円 が今日の残りの上限 {p['room']:,}円 を超えるので止めます")
            self.step = "投票入力完了"
            self.click("#betList .btnSubmit a", "「投票入力完了」")
            self.confirm(added, total, state)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    def launch(self, pw):
        args = {"headless": bool(self.s.get("headless", True))}
        if os.name == "nt":
            try:
                return pw.chromium.launch(channel="msedge", **args)
            except Exception as e:
                self.log(f"Edge を起動できなかったので Chrome で動かします ({e})")
                return pw.chromium.launch(channel="chrome", **args)
        exe = os.environ.get("TELEBOAT_CHROMIUM")  # テスト用
        return pw.chromium.launch(executable_path=exe, **args) if exe else pw.chromium.launch(**args)

    def login(self, ctx):
        self.step = "ログイン"
        page = ctx.new_page()
        self.page = page
        page.goto(TOP_URL, wait_until="domcontentloaded", timeout=60000)
        self.wait_until(lambda: self.is_vis("#memberNo"), "ログイン画面が出ません", 30)
        self.type_in("#memberNo", store.get_secret("memberNo"), "加入者番号", secret=True)
        self.type_in("#pin", store.get_secret("pin"), "暗証番号", secret=True)
        self.type_in("#authPassword", store.get_secret("authPassword"), "認証用パスワード", secret=True)
        self.pause()
        with ctx.expect_page(timeout=40000) as info:
            page.locator("#loginButton").click()
        self.log("ログインを押しました")
        popup = info.value
        popup.wait_for_load_state("domcontentloaded", timeout=60000)
        self.page = popup
        self.log("投票の画面が開きました")

    def balance(self):
        t = self.page.locator("#currentBetLimitAmount").first.inner_text()
        v = digits(t)
        if v is None:
            raise BotError("購入限度額が読めません")
        return v

    def close_popups(self):
        for sel in ("#errorPop #close", "#errorPop #ok", "#closeJyosel a"):
            try:
                if self.is_vis(sel):
                    self.vis(sel).click()
                    self.pause(1, 1.5)
            except Exception:
                pass

    # ---- 入金 ----
    def deposit_for(self, need, bal, state):
        # 入金は残高に反映されたのを確かめてから「今日の入金」に記録する。
        # 指示したがまだ反映されていない分は deposit_pending に置き、二重に入金しないよう上限の計算に含める。
        pend = state.get("deposit_pending")
        if pend:
            if bal >= pend.get("bal_before", 0) + pend["amount"]:
                state["deposit"] = state.get("deposit", 0) + pend["amount"]
                self.log(f"前回指示した {pend['amount']:,}円 の入金が残高に反映されていたので記録しました")
            elif time.time() - pend.get("at", 0) < 3600:
                self.log(f"前回指示した {pend['amount']:,}円 の入金がまだ反映されていないので、今は入金しません")
                return
            else:
                self.log(f"前回指示した {pend['amount']:,}円 の入金は1時間たっても反映されなかったので、入金できなかったものとして扱います")
            state.pop("deposit_pending", None)
            store.save_state(state, self.date)
        # 入金は1,000円単位。上限が1,000円単位でないとき (例: 900円) に切り捨てると0円になって入金できないので、
        # 残りの上限を1,000円単位に切り上げる (購入は別に上限で止めるので、上限を超えて買うことはない)
        left = self.s["cap"] - state.get("deposit", 0)
        room = math.ceil(left / 1000) * 1000 if left > 0 else 0
        dep = min(math.ceil((need - bal) / 1000) * 1000, room)
        if dep <= 0:
            self.log(f"今日はもう上限まで入金したので入金しません (今日の入金の記録 {state.get('deposit', 0):,}円・1日の上限 {self.s['cap']:,}円。"
                     "実際には入金されていないときは、パネルの「今日の入金記録を消す」で0円に戻せます)")
            return
        self.step = "入金"
        self.log(f"残高 {bal:,}円 が足りないので {dep:,}円 を入金します")
        self.open_charge()
        self.type_in("#chargeInstructAmt", dep // 1000, "入金額 (1,000円単位)")
        self.type_in("#chargeBetPassword", store.get_secret("betPassword"), "投票用パスワード", secret=True)
        self.shot("02_deposit")
        self.click("#executeCharge a", "「入金する」")
        self.wait_until(lambda: "入金を実行します" in self.popup_text(), "入金の確認が出ません", 20)
        self.click("#errorPop #ok", "入金確認の OK")
        # OK を押した時点ではまだ「指示中」。反映を確かめるまで今日の入金には数えない
        state["deposit_pending"] = {"amount": dep, "bal_before": bal, "at": time.time()}
        store.save_state(state, self.date)
        res = {}

        def done():
            if self.is_vis("#closeChargecomp a"):
                res["ok"] = True
                return True
            t = self.popup_text()
            if t and "よろしいですか" not in t:
                res["err"] = t
                return True
            return False
        self.wait_until(done, "入金の結果が出ません", 40)
        self.shot("03_deposit_result")
        if res.get("err"):
            state.pop("deposit_pending", None)
            store.save_state(state, self.date)
            raise BotError(f"入金できませんでした: {res['err'][:80]}")
        self.log(f"{dep:,}円 の入金を指示しました")
        self.click("#closeChargecomp a", "入金完了の「閉じる」")
        target = min(need, bal + dep)
        for i in range(60):  # 30秒ごと、最大30分
            if self.balance() >= target:
                state["deposit"] = state.get("deposit", 0) + dep
                state.pop("deposit_pending", None)
                store.save_state(state, self.date)
                self.log(f"残高に反映されました: {self.balance():,}円 (今日の入金 {state['deposit']:,}円)")
                return
            if i % 2 == 0:
                self.log("残高に反映されるのを待っています…")
            if self.is_vis("#updateBalanceBtn a"):
                self.click("#updateBalanceBtn a", "残高更新")
            self.wait(30)
        raise BotError("30分待っても入金が残高に反映されませんでした")

    def open_charge(self):
        """「入金・精算」のメニューを開いて「入金する」を押す (メニューの中にあり、ふだんは隠れている)"""
        self.pause()
        try:
            self.page.locator("#gnavi01").first.hover(timeout=5000)
        except Exception:
            pass
        if not self.is_vis("#charge a"):
            self.pause(0.8, 1.2)
            try:
                self.page.locator("#gnavi01").first.click(timeout=5000)
            except Exception:
                pass
        self.pause(0.8, 1.2)
        if self.is_vis("#charge a"):
            self.vis("#charge a").click()
        elif self.page.locator("#charge a").count():
            self.log("「入金する」が隠れたままなので、直接押します")
            self.page.locator("#charge a").first.evaluate("(a) => a.click()")
        else:
            raise BotError("「入金する」のメニューが見つかりません")
        self.wait_until(lambda: self.is_vis("#chargeInstructAmt"), "入金の画面が開きません", 15)

    # ---- 投票画面 ----
    def h2(self):
        try:
            return re.sub(r"\s", "", self.page.locator("#boatraceTitle h2").first.inner_text())
        except Exception:
            return ""

    def add_bet(self, x):
        before = self.bet_count()
        self.select_venue(x["jcd"], x["venue"])
        self.select_race(x["rno"])
        self.select_tab("betkati1", "単勝")
        self.select_tab("betway1", "通常投票")
        btn = self.page.locator(f"#regbtn_{x['boat']}_1").first
        self.wait_until(lambda: btn.count() > 0, "艇の番号のボタンがありません", 10)
        if "miss" in (btn.get_attribute("class") or ""):
            raise BotError("欠場のため入れられません")
        self.click(f"#regbtn_{x['boat']}_1 a", f"{x['boat']}号艇のボタン")
        self.type_in("#amount", self.s["stake"] // 100, "金額 (100円単位)")
        self.wait_until(lambda: "off" not in (self.page.locator("#regAmountBtn").first.get_attribute("class") or ""),
                        "「ベットリストに追加」が押せません", 10)
        self.click("#regAmountBtn a", "「ベットリストに追加」")
        self.wait_until(lambda: self.bet_count() > before, "ベットリストに入ったことを確かめられませんでした", 15)

    def select_venue(self, jcd, venue):
        venue = JYO.get(jcd, venue)  # 画面の表記に合わせる
        if self.h2() == venue:
            return
        self.click("#boatraceChnage a", "「場変更」")
        j = self.page.locator(f"#j{jcd}").first
        self.wait_until(lambda: j.count() > 0 and j.is_visible(), f"{venue}が場変更の一覧にありません", 15)
        if "end" in (j.locator("xpath=..").get_attribute("class") or ""):
            self.close_popups()
            raise BotError(f"{venue}は発売していません")
        self.pause()
        j.click()
        self.click("#jyoChange", "「変更する」")
        ok_at = [0.0]

        def switched():
            t = self.popup_text()
            if "連続して操作" in t:
                raise BotError("連続して操作された")
            if t and self.is_vis("#errorPop #ok") and time.time() - ok_at[0] > 3:
                ok_at[0] = time.time()
                self.log(f"場変更の確認に OK を押しました: {t[:60]}")
                self.vis("#errorPop #ok").click()
            return self.h2() == venue
        try:
            self.wait_until(switched, "", 20)
        except BotError as e:
            self.shot("err_venue")
            self.close_popups()
            raise BotError(f"{venue}に切り替わりませんでした (画面の場名: {self.h2() or 'なし'}) {e}")
        self.pause(1, 1.5)

    def select_race(self, rno):
        sel = f"#selRaceNo{rno:02d}"
        tab = self.page.locator(sel).first
        self.wait_until(lambda: tab.count() > 0, f"{rno}Rのタブがありません", 10)
        if "current" in (tab.get_attribute("class") or ""):
            return
        self.click(f"{sel} a", f"{rno}R")
        self.wait_until(lambda: "current" in (tab.get_attribute("class") or ""), f"{rno}Rが選べません (締切済みかもしれません)", 10)

    def select_tab(self, id_, what):
        tab = self.page.locator(f"#{id_}").first
        if "current" in (tab.get_attribute("class") or ""):
            return
        self.click(f"#{id_} a", what)
        self.wait_until(lambda: "current" in (tab.get_attribute("class") or ""), f"{what}が選べません", 10)

    def bet_count(self):
        try:
            return digits(self.page.locator(".inputCompletion .betNumber strong").first.inner_text(timeout=2000)) or 0
        except Exception:
            return 0

    def total_amount(self):
        try:
            return digits(self.page.locator("#totalAmount").first.inner_text(timeout=2000)) or 0
        except Exception:
            return 0

    # ---- 購入確認画面 ----
    def describe_page(self):
        """入力欄とボタンの一覧をログに残す (直すときの手がかり)"""
        return self.page.evaluate("""() => {
          const vis = (n) => n.offsetParent !== null;
          const near = (n) => ((n.closest('tr, li, dl, p, div') || n.parentElement || {}).innerText || '').replace(/\\s+/g, ' ').slice(0, 50);
          const d = (n) => `${n.tagName.toLowerCase()}#${n.id || '-'}.${String(n.className || '-').replace(/\\s+/g, '.')}` +
            `[${n.type || ''}${n.name ? ' name=' + n.name : ''}]「${n.type === 'password' ? '' : (n.value || n.textContent || '').trim().slice(0, 20)}」<${near(n)}>`;
          return {
            inputs: [...document.querySelectorAll('input, select, textarea')].filter((n) => n.type !== 'hidden' && vis(n)).map(d),
            buttons: [...document.querySelectorAll('a, button, input[type=button], input[type=submit]')]
              .filter((n) => vis(n) && (n.value || n.textContent || '').trim()).map(d).slice(0, 60),
            text: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 1500),
          };
        }""")

    def save_html(self, name):
        try:
            html = self.page.evaluate("""() => { const c = document.documentElement.cloneNode(true);
              c.querySelectorAll('input').forEach((n) => { if (n.type === 'password') n.removeAttribute('value'); });
              c.querySelectorAll('script').forEach((n) => n.remove()); return c.outerHTML; }""")
            self.shot_cb(None, name, html=html)
        except Exception as e:
            self.log(f"(画面の HTML を残せませんでした: {e})")

    def confirm(self, added, total, state):
        self.step = "購入確認"
        self.wait_until(lambda: "/betconf" in self.page.url or self.is_vis("input[type=password]"), "購入確認画面が開きません", 30)
        self.wait(2)
        self.shot("20_confirm")
        self.save_html("20_confirm")
        d = self.describe_page()
        self.log("購入確認画面の入力欄: " + " / ".join(d["inputs"]))
        self.log("購入確認画面のボタン: " + " / ".join(d["buttons"][:30]))
        text = re.sub(r"\s", "", d["text"])
        if f"{total:,}" not in text:
            raise BotError(f"合計 {total:,}円 が購入確認画面に見当たらないので止めます")
        amt = self.page.evaluate("""() => {
          const vis = (n) => n.offsetParent !== null;
          const c = [...document.querySelectorAll('input')].filter((n) => vis(n) && !['hidden','checkbox','radio','password','button','submit'].includes(n.type));
          const m = document.querySelector('input[name=betAmount]') || c.find((n) => /amount|amt|kingaku/i.test(n.id + ' ' + n.name + ' ' + n.className)) ||
                    c.find((n) => /金額/.test(((n.closest('tr, dl, li, div') || {}).innerText || '')));
          if (!m) return null;
          if (!m.id) m.id = 'byosou-amount';
          const after = ((m.parentElement || {}).innerText || '') + (m.nextSibling ? (m.nextSibling.textContent || '') : '');
          return {id: m.id, unit: /000\\s*円/.test(after) ? 1000 : /00\\s*円/.test(after) ? 100 : 1, after: after.slice(0, 20)};
        }""")
        if amt:
            if total % amt["unit"]:
                raise BotError(f"合計 {total}円 を {amt['unit']}円単位で入れられません")
            self.type_in(f"#{amt['id']}", total // amt["unit"], f"購入金額の確認 ({amt['unit']}円単位・欄の後ろ「{amt['after']}」)")
        else:
            self.log("購入金額の確認欄はありませんでした")
        pw_sel = self.page.evaluate("""() => {
          const vis = (n) => n.offsetParent !== null;
          const c = [...document.querySelectorAll('input[type=password]')].filter((n) => vis(n) &&
            n.getAttribute('tabindex') !== '-1' && !/dummy/i.test(n.name || ''));
          const m = document.querySelector('input[name=betPassword]') || c.find((n) => /pass/i.test(n.id + ' ' + n.name)) || c[0];
          if (!m) return null; if (!m.id) m.id = 'byosou-pass'; return '#' + m.id;
        }""")
        if not pw_sel:
            raise BotError("投票用パスワードの欄が見つかりません")
        self.type_in(pw_sel, store.get_secret("betPassword"), "投票用パスワード", secret=True)
        self.shot("21_confirm_filled")
        if self.mode != "live":
            self.log(f"テストなので、ここで止めます ({len(added)}点・{total:,}円 は買っていません)")
            return
        btn = self.page.evaluate("""() => {
          const vis = (n) => n.offsetParent !== null;
          const b = [...document.querySelectorAll('a, button, input[type=button], input[type=submit]')]
            .find((n) => vis(n) && /^投票(する|確定)$/.test((n.value || n.textContent || '').trim()));
          if (!b) return null; if (!b.id) b.id = 'byosou-buy'; return b.id;
        }""")
        if not btn:
            raise BotError("「投票する」ボタンが見つかりません")
        # 上限の最後の確かめ
        if state.get("spent", 0) + total > self.s["cap"]:
            raise BotError("今日の上限を超えるので止めます")
        self.log(f"{len(added)}点・{total:,}円 を購入します")
        self.click(f"#{btn}", "「投票する」")
        result = {}
        ok_at = [0.0]

        def record(note):
            """OK を押したら買ったものとして先に記録する (結果の判定に失敗しても、二重に買ったり上限を超えたりしないため)"""
            if result.get("recorded"):
                return
            result["recorded"] = True
            state["bought"] = sorted(set(state.get("bought", [])) | {x["key"] for x in added})
            state["spent"] = state.get("spent", 0) + total
            at = dt.datetime.now().strftime("%H:%M")
            state.setdefault("tickets", []).extend(
                {"key": x["key"], "race_id": x["race_id"], "venue": x["venue"], "rno": x["rno"], "boat": x["boat"],
                 "deadline": x["deadline"], "stake": self.s["stake"], "prob": x.get("prob"), "odds": x.get("odds"),
                 "ev": x.get("ev"), "strategy": self.s.get("strategy", "model"), "at": at} for x in added)
            store.save_state(state, self.date)
            self.log(f"{note}: {len(added)}点・{total:,}円 を今日の購入として記録しました (今日の合計 {state['spent']:,}円)")

        def finished():
            t = self.popup_text()
            if "連続して操作" in t and not result.get("recorded"):
                result["err"] = t
                return True
            if self.is_vis("#ok") and time.time() - ok_at[0] > 3 and not result.get("recorded"):
                ok_at[0] = time.time()
                self.log(f"購入の確認に OK を押しました: {t[:60]}")
                self.vis("#ok").click()
                record("購入を確定しました")
                return False
            if t and "エラー" in t:
                result["err"] = t
                return True
            if "/betcomp" in self.page.url:  # 投票完了の画面
                result["ok"] = True
                return True
            body = self.page.locator("body").inner_text()
            if re.search(r"受け付けました|受付けました|投票完了|購入しました|受付番号|投票を受付|投票受付", body):
                result["ok"] = True
                return True
            return False
        try:
            self.wait_until(finished, "購入の結果が出ません", 40)
        except BotError:
            if not result.get("recorded"):
                raise
            result["unsure"] = True
        finally:
            self.shot("30_result")
            self.save_html("30_result")
            self.log(f"購入後の画面: {self.page.url.split('?')[0]}")
        if result.get("err"):
            if result.get("recorded"):
                self.log("購入の後にエラーが出ました。テレボートの「照会」で買えたか確かめてください (今日の購入として記録したままにします)")
            raise BotError(f"購入できませんでした: {result['err'][:100]}")
        if result.get("unsure"):
            self.log("購入完了の画面は確かめられませんでしたが、確定の OK は押したので買えているはずです (テレボートの「照会」で確かめられます)")
        else:
            self.log(f"購入しました: {len(added)}点・{total:,}円 (今日の合計 {state['spent']:,}円)")

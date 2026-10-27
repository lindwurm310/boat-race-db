"""公式サイト (boatrace.jp) から当日の直前情報とオッズを取得する。

  直前情報 (beforeinfo): 展示タイム・チルト・体重・スタート展示 (進入コース・ST)・風速・波高
  単勝オッズ (oddstf)
  1 リクエストごとに 1 秒以上あける。
"""
import re
import time
import urllib.request

BASE = "https://www.boatrace.jp/owpc/pc/race/{page}?rno={rno}&jcd={jcd:02d}&hd={hd}"
UA = "Mozilla/5.0 (personal research; low-rate)"
INTERVAL = 1.0
_last = [0.0]


def get(page, jcd, rno, date):
    wait = INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    url = BASE.format(page=page, rno=rno, jcd=jcd, hd=date.replace("-", ""))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    finally:
        _last[0] = time.time()


def num(s):
    s = (s or "").replace("&nbsp;", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


RE_BOAT = re.compile(r'is-boatColor(\d) is-fs14" rowspan="4">\d</td>.*?toban=(\d+).*?'
                     r'<td rowspan="2">([\d.]*)kg</td>\s*<td rowspan="4">([^<]*)</td>\s*<td rowspan="4">([^<]*)</td>',
                     re.S)
RE_START = re.compile(r'table1_boatImage1Number is-type\d">(\d)</span>.*?table1_boatImage1Time[^>]*>([^<]*)</span>', re.S)


def parse_beforeinfo(html):
    """{"boats": {艇番: dict(racer_no, weight, exhibition_time, tilt)}, "start": {艇番: (コース, ST)}, wind_speed, wave_cm}
    展示前は exhibition_time が None。"""
    boats = {}
    for lane, toban, w, ex, tilt in RE_BOAT.findall(html):
        boats[int(lane)] = dict(racer_no=int(toban), weight=num(w), exhibition_time=num(ex), tilt=num(tilt))
    start = {}
    for course, (lane, st) in enumerate(RE_START.findall(html), 1):
        st = st.strip()
        start[int(lane)] = (course, -num(st[1:]) if st.startswith("F") else num(st))
    def unit(label):
        m = re.search(rf'weather1_bodyUnitLabelTitle">{label}</span>\s*<span class="weather1_bodyUnitLabelData">'
                      r'\s*([\d.]+)', html)
        return float(m.group(1)) if m else None
    return dict(boats=boats, start=start, wind_speed=unit("風速"), wave_cm=unit("波高"))


def parse_win_odds(html):
    """単勝オッズ {艇番: オッズ} (欠場などは入らない)。"""
    i, j = html.find("単勝オッズ"), html.find("複勝オッズ")
    if i < 0:
        return {}
    part = html[i:j if j > i else None]
    odds = {}
    for lane, o in re.findall(r'is-boatColor(\d)">\d</td>.*?<td class="oddsPoint[^"]*">([^<]*)</td>', part, re.S):
        v = num(o)
        if v:
            odds[int(lane)] = v
    return odds


def race_live(jcd, rno, date):
    b = parse_beforeinfo(get("beforeinfo", jcd, rno, date))
    b["odds"] = parse_win_odds(get("oddstf", jcd, rno, date))
    return b


# ---------------- 結果 (raceresult): 当日の答え合わせ用 ----------------
FIN_CODE = {"Ｆ": "F", "Ｌ": "L", "欠": "K"}  # それ以外の文字 (失・転・落・沈・妨・エ・不) は失格などとして S
BET_NAME = {"3連単": "３連単", "3連複": "３連複", "2連単": "２連単", "2連複": "２連複", "拡連複": "拡連複",
            "単勝": "単勝", "複勝": "複勝"}
RE_FIN = re.compile(r'<td class="is-fs14">([^<]*)</td>\s*<td class="is-fs14 is-fBold is-boatColor(\d)">')
RE_ST = re.compile(r'table1_boatImage1Number is-type\d">(\d)</span>.*?table1_boatImage1TimeInner[^>]*>\s*([^\s<]*)'
                   r'\s*([^<]*?)\s*</span>', re.S)


def parse_result(html):
    """結果のページから dict(finish={艇番: 着 (1〜6) か F/L/K/S}, st={艇番: (コース, "ST")}, technique,
    wind_speed, wave_cm, payouts=[(券種, 組, 払戻, 人気)])。まだ結果が出ていなければ None。"""
    finish = {}
    for f, lane in RE_FIN.findall(html):
        f = f.strip().translate(str.maketrans("１２３４５６", "123456"))
        finish[int(lane)] = f if f.isdigit() else FIN_CODE.get(f, "S")
    if not finish:
        return None
    st, technique = {}, None
    for course, (lane, t, tech) in enumerate(RE_ST.findall(html), 1):
        st[int(lane)] = (course, t)
        tech = tech.replace("&nbsp;", "").strip()
        technique = technique or tech or None
    pays = []
    i = html.find(">勝式<")
    part = html[i:html.find("</table>", i)] if i >= 0 else ""
    for body in part.split("<tbody>")[1:]:
        m = re.search(r'<td rowspan="\d+">\s*([^<\s]+)\s*</td>', body)
        if not m or m.group(1) not in BET_NAME:
            continue
        for tr in body.split("<tr")[1:]:
            nums = re.findall(r'numberSet1_number[^>]*>(\d)<', tr)
            p = re.search(r'is-payout1">&yen;([\d,]+)', tr)
            if nums and p:
                pop = re.search(r'</span></td>\s*<td>\s*(\d+)\s*</td>', tr)
                pays.append((BET_NAME[m.group(1)], "-".join(nums), int(p.group(1).replace(",", "")),
                             int(pop.group(1)) if pop else None))
    def unit(label):
        m = re.search(rf'weather1_bodyUnitLabelTitle">{label}</span>\s*<span class="weather1_bodyUnitLabelData">'
                      r'\s*([\d.]+)', html)
        return float(m.group(1)) if m else None
    return dict(finish=finish, st=st, technique=technique, wind_speed=unit("風速"), wave_cm=unit("波高"), payouts=pays)


def race_result(jcd, rno, date):
    return parse_result(get("raceresult", jcd, rno, date))

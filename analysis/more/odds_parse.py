"""boatrace.jp のオッズのページ (odds3t / odds3f / odds2tf / oddsk / oddstf) から、組み合わせごとのオッズを取り出す。

  parse(page, html) -> {"3連単": {"1-2-3": 21.1, ...}, ...}
  オッズが "欠場" などで数字でないときは None。拡連複・複勝は "下限-上限" の文字のまま。
  表の作り (列 = 1着の艇、行の左のセルに 2着・3着の艇が rowspan で並ぶ) を、ページのセルから組み立てる。
"""
import re

RE_TABLE = re.compile(r'<div class="title7">.*?title7_mainLabel">([^<]*)</span>.*?<table[^>]*>(.*?)</table>', re.S)
RE_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
RE_TD = re.compile(r'<t([dh])([^>]*)>(.*?)</t[dh]>', re.S)


def _text(s):
    return re.sub(r"<[^>]+>", "", s).replace("&nbsp;", "").strip()


def _value(s):
    try:
        return float(s)
    except ValueError:
        return s if re.fullmatch(r"[\d.]+-[\d.]+", s) else None


def parse_table(body, kind):
    head = re.search(r"<thead.*?</thead>", body, re.S)
    firsts = [int(m) for m in re.findall(r'<th class="is-boatColor(\d)"', head.group(0))] if head else []
    tb = re.sub(r"<thead.*?</thead>", "", body, flags=re.S)
    carry, out = {}, {}   # carry: 列 -> [(艇, あと何行続くか)]
    for tr in RE_TR.findall(tb):
        col, new = 0, []
        for _, attr, inner in RE_TD.findall(tr):
            if "oddsPoint" in attr:
                lab = [b for b, _ in carry.get(col, [])] + [b for b, _ in new]
                carry[col] = [(b, n - 1) for b, n in carry.get(col, []) + new if n > 1]
                combo = ([firsts[col]] if kind not in ("単勝", "複勝") else []) + lab
                sep = "-" if kind in ("3連単", "2連単") else "="
                out[sep.join(map(str, combo))] = _value(_text(inner))
                col, new = col + 1, []
            elif "is-boatColor" in attr and _text(inner).isdigit():
                rs = re.search(r'rowspan="(\d+)"', attr)
                new.append((int(_text(inner)), int(rs.group(1)) if rs else 1))
    return out


def parse(html):
    """ページの中の表ごとに {券種: {組み合わせ: オッズ}}。締切時オッズでないページ (締切前) は "time" に更新時刻。"""
    html = re.sub(r"\s+", " ", html)
    res = {}
    for title, body in RE_TABLE.findall(html):
        kind = title.replace("オッズ", "").strip()
        res[kind] = parse_table(body, kind)
    m = re.search(r'tab4_time">([^<]*)<', html)
    res["time"] = m.group(1).strip() if m else None
    return res

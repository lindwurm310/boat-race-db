"""宣伝用のページと投稿文を作る。GitHub Pages の jisseki/ (実戦成績) と sns/ (X の投稿文) に置く。

  python analysis/promo.py --out site              predictions の予想・答え合わせから作る

作るもの
  site/jisseki/index.html  公開した予想の実戦成績。外れも含めた全日・通算・公開時刻の記録へのリンク・注意書き
  site/jisseki/og.png      実戦成績の画像 (1200x630)。ページの OGP 画像なので、X に URL を貼るとこの画像が出る
  site/sns/index.html      X に投稿する文 (夕方の記事告知・朝の無料予想・夜の結果・週のまとめ)。コピーと「Xで投稿」ボタン
  site/sns/latest.json     同じ投稿文 (ほかのツールから読む用)

検証 (過去10年を予想し直したもの) と実戦 (レース前に公開した予想) は、どのページ・投稿でも分けて書く。
X は画面の自動操作による投稿を禁止しているので、投稿は「Xで投稿」ボタン (投稿画面が文入りで開く) で人が押す。
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import seiseki  # noqa: E402

SITE = "https://lindwurm310.github.io/boat-race-db"
NOTE = "https://note.com/lindwurm310"
REPO = "https://github.com/lindwurm310/boat-race-db"
FONTS = os.path.join(ROOT, "note", "design", "fonts")
WEEK = "月火水木金土日"
TAGS = "#ボートレース予想 #競艇予想"
FOOT = "※過去の成績は将来を保証しません。舟券は20歳から"
FREE_START = "2026-11-03"  # 朝の無料予想を X に出し始めた日 (これより前の日は夜の結果に載せない)
FREE_FROM = "09:30"  # 朝の無料予想は、締切がこの時刻以降のレースから選ぶ (朝8時台に投稿する)
# 日ごとの補足 (実戦成績のページに出す)
NOTES = {
    "2026-10-26": "公開初日。予想の公開が16時台になった (その日の結果は使っていないモデルの予想)。",
}


def jp(d):
    d = dt.date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.month}/{d.day}({WEEK[d.weekday()]})"


def pct(x):
    return f"{x * 100:.1f}%"


def read(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"単勝買い目": str, "買い目の期待値": str})


def days(pred):
    """答え合わせ済みの日ごとの成績 (YYYYMMDD_check.csv)。古い順。"""
    out = []
    for f in sorted(os.listdir(pred)) if os.path.isdir(pred) else []:
        m = re.fullmatch(r"(\d{8})_check\.csv", f)
        if not m:
            continue
        c = read(os.path.join(pred, f))
        if "単勝点数" not in c or c.empty:
            continue
        d = dt.datetime.strptime(m[1], "%Y%m%d").date().isoformat()
        k, hit, got = int(c["単勝点数"].sum()), int(c["単勝的中"].sum()), int(c["単勝払戻"].sum())
        best = c[c["単勝払戻"] > 0].sort_values("単勝払戻", ascending=False)
        b = best.iloc[0] if len(best) else None
        out.append(dict(date=d, races=len(c), fav=float(c["本命1着"].astype(str).eq("True").mean()),
                        n=k, hit=hit, cost=k * 100, got=got,
                        best=(f"{b['場']}{int(b['R'])}R {str(b['結果'])[0]}号艇 {int(b['単勝払戻']):,}円" if b is not None else ""),
                        check=c))
    return out


def total(ds):
    n, hit, cost, got = (sum(d[k] for d in ds) for k in ("n", "hit", "cost", "got"))
    races = sum(d["races"] for d in ds)
    fav = sum(d["fav"] * d["races"] for d in ds) / races if races else 0
    return dict(n=n, hit=hit, cost=cost, got=got, rate=got / cost if cost else 0, races=races, fav=fav,
                first=ds[0]["date"] if ds else "", last=ds[-1]["date"] if ds else "", days=len(ds))


def backtest():
    s = seiseki.saved()
    if not s:
        return None
    p = s["periods"]["全期間"]
    r = {x["項目"]: x for x in p["rows"]}
    tan = r.get("単勝の買い目 (期待値1以上)")
    years = [y for y in s.get("years", []) if y.get("単勝点数")]
    return dict(races=r["本命の1着"]["レース"], rate=tan["回収率"], n=tan["点数"], frm=p["from"], to=p["to"],
                years=years, over=sum(y["単勝回収率"] >= 1 for y in years))


# ---------- 投稿文 ----------

def free_pick(p):
    """朝の無料予想: 単勝の買い目が1艇だけのレースから、その艇の1着確率が一番高いもの (締切 FREE_FROM 以降)。"""
    if "単勝買い目" not in p:
        return None
    b = p[p["単勝買い目"].fillna("").str.strip().str.fullmatch(r"\d")]
    b = b[b["締切"].astype(str) >= FREE_FROM]
    if b.empty:
        return None
    b = b.assign(prob=[r[f"{int(r['単勝買い目'])}号艇"] for _, r in b.iterrows()])
    r = b.sort_values(["prob", "締切"], ascending=[False, True]).iloc[0]
    return dict(race_id=r["race_id"], venue=r["場"], R=int(r["R"]), lane=int(r["単勝買い目"]), deadline=r["締切"],
                prob=float(r["prob"]), ev=str(r.get("買い目の期待値", "")).split()[0],
                picks=int(p["単勝買い目"].fillna("").str.split().str.len().sum()))


def intent(text):
    return "https://x.com/intent/tweet?text=" + urllib.parse.quote(text)


def jisseki_url(d):
    return f"{SITE}/jisseki/?d={d.replace('-', '')}"  # 日付をつけると X が新しい画像を読み直す


def posts(pred, out, ds, today):
    items = []
    t = total(ds)
    bt = backtest()
    # 夕方: 明日の Note 記事の告知 (note-daily が作った latest.json)
    lj = os.path.join(out, "note", "latest.json")
    if os.path.exists(lj):
        with open(lj, encoding="utf-8") as f:
            m = json.load(f)
        if m["date"] > today:
            text = (f"【明日{jp(m['date'])}の予想を公開しました】\n"
                    f"{'・'.join(m['venues'][:6])}{'ほか' if len(m['venues']) > 6 else ''} 全{m['races']}レース\n"
                    f"単勝の買い目 {m['picks']}点を締切順に。外れも含めて翌日に全部答え合わせします\n\n"
                    f"{NOTE}\n\n{TAGS}\n{FOOT}")
            items.append(dict(kind="夕方", when=f"{jp(today)} 17時ごろ (記事の公開後)", text=text))
    # 朝: 今日の無料予想
    pp = os.path.join(pred, today.replace("-", "") + ".csv")
    if os.path.exists(pp):
        fp = free_pick(read(pp))
        if fp:
            text = (f"【今日の無料予想】{jp(today)}\n"
                    f"{fp['venue']}{fp['R']}R（{fp['deadline']}締切）単勝 {fp['lane']}号艇\n"
                    f"1着の確率 {fp['prob'] * 100:.0f}%・期待値 {fp['ev']}\n\n"
                    f"今日の単勝の買い目は全{fp['picks']}点。全部はnoteで\n{NOTE}\n\n"
                    f"#{fp['venue']} {TAGS}\n{FOOT}")
            items.append(dict(kind="朝", when=f"{jp(today)} 8時台", text=text, free=fp))
    # 夜: いちばん新しい答え合わせの日の結果
    if ds:
        d = ds[-1]
        lines = [f"【結果】{jp(d['date'])} 単勝の買い目 {d['n']}点中{d['hit']}本的中、回収率{pct(d['got'] / d['cost']) if d['cost'] else '-'}"]
        if d["best"]:
            lines.append(f"最高配当: {d['best']}")
        dp = os.path.join(pred, d["date"].replace("-", "") + ".csv")
        fp = free_pick(read(dp)) if d["date"] >= FREE_START and os.path.exists(dp) else None
        if fp is not None:
            row = d["check"][d["check"]["race_id"] == fp["race_id"]]
            if len(row):
                won = str(row.iloc[0]["結果"])[:1] == str(fp["lane"])
                pay = int(row.iloc[0]["単勝払戻"]) if won else 0
                lines.append(f"朝の無料予想 {fp['venue']}{fp['R']}R {fp['lane']}号艇: " + (f"的中 {pay:,}円" if won else "外れ"))
        if d["cost"] and d["got"] < d["cost"]:
            lines.append("1日ごとのぶれは大きいので、通算で見てください")
        lines += ["", f"{jp(t['first'])}からの通算: {t['n']}点 回収率{pct(t['rate'])}",
                  f"外れも含めた全件: {jisseki_url(d['date'])}", "", TAGS]
        if weight("\n".join(lines)) > 280:  # 長すぎるときは補足の行を外す
            lines = [x for x in lines if not x.startswith("1日ごとのぶれ")]
        items.append(dict(kind="夜", when=f"{jp(d['date'])} 22時半以降 (答え合わせの後)", text="\n".join(lines)))
        # 週のまとめ: 直近7日 (月曜に投稿)
        start = (dt.date.fromisoformat(d["date"]) - dt.timedelta(days=6)).isoformat()
        w = total([x for x in ds if x["date"] >= start])
        text = (f"【1週間の実戦成績】{jp(w['first'])}〜{jp(w['last'])}\n"
                f"単勝の買い目 {w['n']}点中{w['hit']}本 回収率{pct(w['rate'])}\n"
                f"本命の1着率 {pct(w['fav'])}（{w['races']:,}レース）\n")
        if bt:
            text += f"過去10年の検証は{pct(bt['rate'])}。実戦の数字を毎週そのまま報告します\n"
        text += f"{jisseki_url(d['date'])}\n\n{TAGS}\n{FOOT}"
        items.append(dict(kind="週", when="月曜の夜", text=text))
    for it in items:
        if weight(it["text"]) > 280:
            print(f"注意: {it['kind']}の投稿文が280を超えています ({weight(it['text'])})")
    return items


# ---------- 画像 (OGP) ----------

def og_image(ds, bt, path):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    W, H, S = 1200, 630, 2
    img = Image.new("RGB", (W * S, H * S), "#f7f5ef")
    d = ImageDraw.Draw(img)

    def f(name, size):
        return ImageFont.truetype(os.path.join(FONTS, name), size * S)

    ink, sub, green, red = "#1f2328", "#5b6068", "#0b7a5a", "#c2412d"
    t = total(ds)
    d.rectangle([0, 0, W * S, 14 * S], fill=green)
    d.text((60 * S, 50 * S), "実戦成績（レース前に公開した予想・外れも全件）", font=f("ZenKakuGothicNew-Bold.ttf", 34), fill=ink)
    d.text((60 * S, 100 * S), f"{jp(t['first'])}〜{jp(t['last'])}　{t['days']}日・{t['races']:,}レース", font=f("ZenKakuGothicNew-Regular.ttf", 26), fill=sub)
    d.text((60 * S, 160 * S), "単勝の買い目 回収率", font=f("ZenKakuGothicNew-Bold.ttf", 28), fill=sub)
    d.text((56 * S, 196 * S), pct(t["rate"]), font=f("IBMPlexMono-Bold.ttf", 96), fill=green if t["rate"] >= 1 else red)
    d.text((60 * S, 330 * S), f"{t['n']:,}点中 {t['hit']:,}本的中", font=f("ZenKakuGothicNew-Bold.ttf", 28), fill=ink)
    d.text((60 * S, 372 * S), f"1点100円: 投資{t['cost']:,}円", font=f("ZenKakuGothicNew-Regular.ttf", 24), fill=sub)
    d.text((60 * S, 404 * S), f"　　　　　払戻{t['got']:,}円", font=f("ZenKakuGothicNew-Regular.ttf", 24), fill=sub)
    if bt:
        d.text((60 * S, 470 * S), f"参考: 過去10年の検証（別集計） {pct(bt['rate'])}", font=f("ZenKakuGothicNew-Regular.ttf", 24), fill=sub)
    d.text((60 * S, 560 * S), "※過去の成績は将来の利益を保証しません。舟券は20歳から", font=f("ZenKakuGothicNew-Regular.ttf", 20), fill=sub)
    # 通算の収支 (円) の折れ線
    x0, y0, x1, y1 = 600 * S, 170 * S, 1140 * S, 520 * S
    d.text((x0, 130 * S), "通算の収支（1点100円）", font=f("ZenKakuGothicNew-Bold.ttf", 24), fill=ink)
    cum, s = [0], 0
    for x in ds:
        s += x["got"] - x["cost"]
        cum.append(s)
    lo, hi = min(cum + [0]), max(cum + [0])
    pad = max(500, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad

    def Y(v):
        return y1 - (v - lo) / (hi - lo) * (y1 - y0)

    def X(i):
        return x0 + i / max(1, len(cum) - 1) * (x1 - x0)

    d.line([x0, Y(0), x1, Y(0)], fill="#b9b4a8", width=2 * S)
    d.text((x0, Y(0) + 6 * S), "0円", font=f("ZenKakuGothicNew-Regular.ttf", 18), fill=sub)
    d.line([(X(i), Y(v)) for i, v in enumerate(cum)], fill=green if s >= 0 else red, width=5 * S, joint="curve")
    d.ellipse([X(len(cum) - 1) - 8 * S, Y(s) - 8 * S, X(len(cum) - 1) + 8 * S, Y(s) + 8 * S], fill=green if s >= 0 else red)
    d.text((x1, 130 * S), f"{s:+,}円", font=f("ZenKakuGothicNew-Black.ttf", 30), fill=green if s >= 0 else red, anchor="ra")
    img.resize((W, H), Image.LANCZOS).save(path, optimize=True)
    return True


# ---------- ページ ----------

CSS = """
:root{--bg:#f7f5ef;--card:#fff;--fg:#1f2328;--sub:#5b6068;--line:#e3dfd4;--good:#0b7a5a;--bad:#c2412d;--accent:#0b7a5a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15171a;--card:#1d2024;--fg:#e8e8e8;--sub:#a3a8b0;
--line:#33373d;--good:#4fc3a1;--bad:#f08a75;--accent:#4fc3a1}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;
max-width:860px;margin:0 auto;padding:16px;line-height:1.7}
h1{font-size:1.5em;margin:.4em 0}h2{font-size:1.15em;margin:1.6em 0 .5em;border-left:4px solid var(--accent);padding-left:8px}
.sub{color:var(--sub);font-size:.92em}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.card b{display:block;font-size:1.7em;font-variant-numeric:tabular-nums}
.good{color:var(--good)}.bad{color:var(--bad)}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child,td.l{text-align:left}
a{color:var(--accent)}
svg{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
.note{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;font-size:.92em}
.post{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:12px 0}
.post pre{white-space:pre-wrap;font-family:inherit;margin:8px 0;font-size:15px}
.btns{display:flex;gap:8px;flex-wrap:wrap}
.btns a,.btns button{font-size:15px;padding:8px 14px;border-radius:8px;border:1px solid var(--accent);cursor:pointer;
text-decoration:none;background:var(--accent);color:#fff}
.btns button{background:transparent;color:var(--accent)}
.len{color:var(--sub);font-size:13px;align-self:center}
"""

FOOTER = """<h2>注意</h2><div class="note"><ul>
<li>ここの数字は過去の結果です。これからの的中や回収率を約束するものではありません。1日・1週間では100%を下回ることもよくあります。</li>
<li>回収率は1点100円・締切時の確定配当で計算しています。</li>
<li>舟券の購入は20歳からです。無理のない金額で、ご自身の判断でお楽しみください。</li>
<li>ギャンブルのことで悩んだら、お住まいの都道府県の精神保健福祉センターや、ボートレースの「依存症に関する相談窓口」に相談できます。</li>
</ul></div>"""


def page(title, desc, body, og=None, url=None):
    meta = [f'<meta name="description" content="{html.escape(desc)}">',
            f'<meta property="og:title" content="{html.escape(title)}">',
            f'<meta property="og:description" content="{html.escape(desc)}">', '<meta property="og:type" content="website">']
    if og:
        meta += [f'<meta property="og:image" content="{og}">', '<meta name="twitter:card" content="summary_large_image">']
    if url:
        meta.append(f'<meta property="og:url" content="{url}">')
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
{chr(10).join(meta)}
<style>{CSS}</style></head><body>
{body}
</body></html>
"""


def svg_chart(ds):
    cum, s = [0], 0
    for x in ds:
        s += x["got"] - x["cost"]
        cum.append(s)
    W, H, L, R, T, B = 800, 260, 70, 20, 20, 40
    lo, hi = min(cum + [0]), max(cum + [0])
    pad = max(500, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad

    def X(i):
        return L + i / max(1, len(cum) - 1) * (W - L - R)

    def Y(v):
        return T + (hi - v) / (hi - lo) * (H - T - B)

    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(cum))
    labels = "".join(f'<text x="{X(i + 1):.1f}" y="{H - 14}" text-anchor="middle" font-size="12" fill="currentColor" opacity=".7">'
                     f'{jp(x["date"])[:-3]}</text>' for i, x in enumerate(ds) if len(ds) <= 14 or i % max(1, len(ds) // 10) == 0)
    color = "var(--good)" if s >= 0 else "var(--bad)"
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="通算の収支の推移" style="color:var(--fg)">'
            f'<line x1="{L}" x2="{W - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="currentColor" opacity=".35"/>'
            f'<text x="{L - 8}" y="{Y(0) + 4:.1f}" text-anchor="end" font-size="12" fill="currentColor" opacity=".7">0円</text>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round"/>'
            f'<circle cx="{X(len(cum) - 1):.1f}" cy="{Y(s):.1f}" r="5" fill="{color}"/>'
            f'<text x="{X(len(cum) - 1) - 8:.1f}" y="{Y(s) - 10:.1f}" text-anchor="end" font-size="15" font-weight="bold" fill="{color}">{s:+,}円</text>'
            f'{labels}</svg>')


def jisseki_page(ds, bt):
    t = total(ds)
    cls = "good" if t["rate"] >= 1 else "bad"
    rows = []
    for x in reversed(ds):
        r = x["got"] / x["cost"] if x["cost"] else 0
        ymd = x["date"].replace("-", "")
        rows.append(f"<tr><td>{jp(x['date'])}</td><td>{x['races']}</td><td>{pct(x['fav'])}</td><td>{x['n']}</td><td>{x['hit']}</td>"
                    f"<td class=\"{'good' if r >= 1 else 'bad'}\">{pct(r) if x['cost'] else '-'}</td>"
                    f"<td>{x['got'] - x['cost']:+,}円</td><td class=\"l\">{html.escape(x['best'])}</td>"
                    f"<td class=\"l\"><a href=\"../history/{ymd}.csv\">予想</a> ・ <a href=\"../history/{ymd}_check.csv\">結果</a> ・ "
                    f"<a href=\"{REPO}/commits/gh-pages/history/{ymd}.csv\">公開時刻</a></td></tr>")
    notes = "".join(f"<li>{jp(k)}: {html.escape(v)}</li>" for k, v in NOTES.items() if any(x["date"] == k for x in ds))
    btxt = ""
    if bt:
        yrows = "".join(f"<tr><td>{y['年']}</td><td>{y['レース']:,}</td><td>{y['単勝点数']:,}</td>"
                        f"<td class=\"{'good' if y['単勝回収率'] >= 1 else 'bad'}\">{pct(y['単勝回収率'])}</td></tr>"
                        for y in sorted(bt["years"], key=lambda y: y["年"], reverse=True))
        btxt = (f"<h2>参考: 過去10年の検証（実戦とは別の集計）</h2>"
                f"<p>予想する日より前のデータだけで学習したモデルで、{bt['frm']}〜{bt['to']}の{bt['races']:,}レースを予想し直した結果です。"
                f"単勝の買い目は{bt['n']:,}点で回収率<b>{pct(bt['rate'])}</b>、{len(bt['years'])}年中{bt['over']}年で100%以上でした。"
                f"レース前に公開した予想ではないので、上の実戦成績とは分けて見てください。</p>"
                f"<div class=\"tw\"><table><thead><tr><th>年</th><th>レース</th><th>単勝の買い目</th><th>回収率</th></tr></thead>"
                f"<tbody>{yrows}</tbody></table></div>")
    body = f"""<h1>実戦成績</h1>
<p class="sub">55万レースのデータベースからみる統計学の予想 ・ {jp(t['first'])}〜{jp(t['last'])} ・ 毎晩更新</p>
<p>レース前に公開した予想を、外れも含めて全部答え合わせした結果です。買い方は「単勝の買い目（予想した確率 × 予想オッズの期待値が1以上の艇）」です。</p>
<div class="cards">
<div class="card">単勝の買い目 回収率<b class="{cls}">{pct(t['rate'])}</b></div>
<div class="card">点数 / 的中<b>{t['n']:,} / {t['hit']:,}</b></div>
<div class="card">通算の収支（1点100円）<b class="{cls}">{t['got'] - t['cost']:+,}円</b></div>
<div class="card">本命の1着率<b>{pct(t['fav'])}</b><span class="sub">{t['races']:,}レース</span></div>
</div>
<h2>通算の収支の推移</h2>
{svg_chart(ds)}
<h2>日ごとの成績（新しい順・全日）</h2>
<div class="tw"><table><thead><tr><th>日付</th><th>レース</th><th>本命1着率</th><th>単勝点数</th><th>的中</th><th>回収率</th><th>収支</th>
<th>最高配当</th><th>記録</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table></div>
<p class="sub">「公開時刻」は GitHub に予想ファイルを置いた時刻の記録です。予想がレース前に出ていたことを誰でも確かめられます。</p>
{f'<div class="note"><ul>{notes}</ul></div>' if notes else ''}
{btxt}
<h2>予想を見る</h2>
<p>毎日の予想（単勝の買い目・本命が堅いレース）は <a href="{NOTE}">note</a> で前日17時ごろに公開しています。
全レースの予想と答え合わせは <a href="../">予想ページ</a> で見られます。</p>
{FOOTER}"""
    desc = f"レース前に公開した予想の全件の答え合わせ。単勝の買い目 {t['n']:,}点 回収率{pct(t['rate'])}（{jp(t['first'])}〜{jp(t['last'])}）。"
    return page("実戦成績 | 55万レースのデータベースからみる統計学の予想", desc, body, og=f"{SITE}/jisseki/og.png", url=f"{SITE}/jisseki/")


def sns_page(items, made):
    blocks = []
    for i, it in enumerate(items):
        blocks.append(f"""<div class="post"><b>{html.escape(it['kind'])}</b> <span class="sub">投稿の目安: {html.escape(it['when'])}</span>
<pre id="p{i}">{html.escape(it['text'])}</pre>
<div class="btns"><a href="{html.escape(intent(it['text']))}" target="_blank" rel="noopener">Xで投稿</a>
<button onclick="cp('p{i}',this)">コピー</button><span class="len">{weight(it['text'])}/280</span></div></div>""")
    body = f"""<h1>X の投稿文</h1>
<p class="sub">{made} 更新 ・ 夕方（17時ごろ）・朝（6時ごろ）・夜（22時半ごろ）に作り直します</p>
<p>「Xで投稿」を押すと、文が入った状態で X の投稿画面が開きます。確かめて「ポスト」を押してください。
URL を入れた投稿には、実戦成績の画像が自動で付きます。</p>
{''.join(blocks) or '<p>まだ投稿文がありません。</p>'}
<h2>決まりごと</h2><div class="note"><ul>
<li>負けた日も同じ形で出す。当たった日だけ出すと信用を失い、景品表示法の上でも問題になりやすい。</li>
<li>「必ず」「絶対」「稼げる」「的中保証」は書かない。検証（過去10年）と実戦の数字は分けて書く。</li>
<li>自動フォロー・大量のリプライや DM はしない（X のスパム扱いになる）。X の有料広告は使わない。</li>
</ul></div>
<script>
function cp(id,b){{navigator.clipboard.writeText(document.getElementById(id).textContent).then(()=>{{b.textContent='コピーしました'}},()=>{{b.textContent='コピーできませんでした'}})}}
</script>"""
    return page("X の投稿文", "毎日の X の投稿文", body)


def weight(text):
    """X の文字数 (日本語は2、URL は23として数える)。"""
    n = 0
    for part in re.split(r"(https?://\S+)", text):
        if part.startswith("http"):
            n += 23
        else:
            n += sum(1 if ord(c) < 0x1100 else 2 for c in part)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", default=os.path.join(ROOT, "predictions"))
    ap.add_argument("--out", default=os.path.join(ROOT, "site"))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    ds = days(a.pred)
    bt = backtest()
    os.makedirs(os.path.join(a.out, "jisseki"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "sns"), exist_ok=True)
    if ds:
        og_image(ds, bt, os.path.join(a.out, "jisseki", "og.png"))
        with open(os.path.join(a.out, "jisseki", "index.html"), "w", encoding="utf-8") as f:
            f.write(jisseki_page(ds, bt))
    items = posts(a.pred, a.out, ds, a.today)
    made = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(a.out, "sns", "index.html"), "w", encoding="utf-8") as f:
        f.write(sns_page(items, made))
    with open(os.path.join(a.out, "sns", "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"made": made, "posts": [{k: v for k, v in it.items() if k != "free"} for it in items]},
                  f, ensure_ascii=False, indent=1)
    t = total(ds)
    print(f"{a.out}/jisseki: {t['days']}日 単勝 {t['n']}点 回収率 {pct(t['rate'])} / sns: 投稿文 {len(items)}件")


if __name__ == "__main__":
    main()

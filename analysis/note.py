"""Note.com に載せる毎日の予想記事を、決まった書式 (analysis/note/template.md) で作る。

  python analysis/note.py --date 2026-11-01      predictions/20261101.csv の予想から記事を作る
  python analysis/note.py --date tomorrow        明日の分 (today / tomorrow も可)

出力は predictions/note/YYYYMMDD.md (記事の元)、YYYYMMDD.png (見出し画像) と YYYYMMDD.html (ブラウザで開いて「本文をコピー」→ Note の
編集画面に貼り付ける用)。Note には公式の投稿 API がないので、公開は人が Note の画面で行う:
  1. HTML を開いて「タイトルをコピー」「本文をコピー」→ Note の新規記事に貼る
  2. 「（ここから有料エリア）」の位置で「有料エリア設定」のラインを引き、価格 (既定 300円) を確かめて公開
GitHub Actions (.github/workflows/yosou-check-today.yml) が毎晩、当日の答え合わせのあとに明日の分を作り、
GitHub Pages の note/ に置く。
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import string
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import seiseki  # noqa: E402

TEMPLATE = os.path.join(HERE, "note", "template.md")
WEEK = "月火水木金土日"
PRICE = 300  # 記事の価格 (円)
TAGS = ["ボートレース", "競艇", "ボートレース予想", "競艇予想", "データ分析"]  # Note のハッシュタグ (5つ)
# 有料ラインの位置。Note の「有料エリア設定」でこの行のところにラインを引く
PAYWALL = "---\n\n**（ここから有料エリア）**\n\n---"


def jp_date(d):
    return f"{d.month}月{d.day}日({WEEK[d.weekday()]})"


def pct(x):
    return f"{x * 100:.1f}%"


def read(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"単勝買い目": str, "買い目の期待値": str})


def backtest_block():
    """過去10年の検証 (analysis/backtest/seiseki.json) の主な数字。"""
    s = seiseki.saved()
    if not s:
        return "（検証の集計がありません）", "", ""
    p = s["periods"]["全期間"]
    r = {x["項目"]: x for x in p["rows"]}
    fav, top3, tan, tri5 = (r.get(k) for k in ("本命の1着", "本命の3着以内", "単勝の買い目 (期待値1以上)", "確率の上位5点"))
    lines = [f"- 検証したレース数: **{fav['レース']:,}レース**"]
    lines.append(f"- 本命の1着率: **{pct(fav['的中率'])}**（3着以内 {pct(top3['的中率'])}）")
    if tan:
        lines.append(f"- 単勝の買い目（期待値1以上）: {tan['点数']:,}点で的中率 {pct(tan['的中率'])}、"
                     f"**回収率 {pct(tan['回収率'])}**")
    years = [y for y in s.get("years", []) if y.get("単勝点数")]
    if years:
        rates = [y["単勝回収率"] for y in years]
        over = sum(x >= 1 for x in rates)
        ys = sorted(str(y["年"]) for y in years)
        lines.append(f"- 単勝の買い目の年ごとの回収率: {ys[0]}〜{ys[-1]}年の{len(years)}年中{over}年で100%以上"
                     f"（一番低い年 {pct(min(rates))}、一番高い年 {pct(max(rates))}）")
    if tri5:
        lines.append(f"- 参考: 3連単の確率上位5点は的中率 {pct(tri5['的中率'])}、回収率 {pct(tri5['回収率'])}")
    return "\n".join(lines), p["from"].replace("-", "/"), p["to"].replace("-", "/")


def recent_block(pred_dir, date):
    """公開を始めてから前日までの答え合わせ (predictions/log.csv と YYYYMMDD_check.csv)。"""
    log_path = os.path.join(pred_dir, "log.csv")
    if not os.path.exists(log_path):
        return "（まだ答え合わせした日がありません）"
    log = pd.read_csv(log_path, encoding="utf-8-sig")
    log = log[log["date"] < date.isoformat()].sort_values("date")
    if log.empty:
        return "（まだ答え合わせした日がありません）"
    out = []
    last = log.iloc[-1]
    d = dt.date.fromisoformat(last["date"])
    out.append(f"### {jp_date(d)}の結果")
    out.append(f"- {int(last['races'])}レース、本命の1着率 {pct(last['本命1着率'])}、3着以内 {pct(last['本命3着以内率'])}")
    if "単勝期待値買い_点数" in log and pd.notna(last.get("単勝期待値買い_点数")):
        k, hit, rate = int(last["単勝期待値買い_点数"]), int(last["単勝期待値買い_的中"]), last["単勝期待値買い_回収率"]
        out.append(f"- 単勝の買い目: {k}点で{hit}点的中、回収率 **{pct(rate)}**")
    hits = hit_list(pred_dir, d)
    if hits:
        out.append("")
        out.append("単勝の的中:")
        out += hits
    if "単勝期待値買い_点数" in log:
        t = log.dropna(subset=["単勝期待値買い_点数"])
        k = t["単勝期待値買い_点数"].sum()
        if k:
            got = (t["単勝期待値買い_回収率"] * t["単勝期待値買い_点数"] * 100).sum()
            first = dt.date.fromisoformat(t["date"].iloc[0])
            out.append("")
            out.append(f"### 公開を始めてからの累計（{jp_date(first)}〜{jp_date(d)}、{len(t)}日）")
            out.append(f"- 単勝の買い目: {int(k)}点で{int(t['単勝期待値買い_的中'].sum())}点的中、"
                       f"**回収率 {pct(got / (100 * k))}**（投資 {int(k) * 100:,}円 → 払戻 {round(got):,}円）")
            out.append(f"- 本命の1着率: {pct((t['本命1着率'] * t['races']).sum() / t['races'].sum())}"
                       f"（{int(t['races'].sum()):,}レース）")
    return "\n".join(out)


def hit_list(pred_dir, d):
    path = os.path.join(pred_dir, d.strftime("%Y%m%d") + "_check.csv")
    if not os.path.exists(path):
        return []
    c = read(path)
    if "単勝的中" not in c:
        return []
    c = c[c["単勝的中"] > 0]
    return [f"- {r['場']} {int(r['R'])}R　{r['結果'][0]}号艇　払戻 {int(r['単勝払戻']):,}円" for _, r in c.iterrows()]


def by_deadline(p):
    return p.sort_values(["締切", "場", "R"], kind="stable")


def picks_block(p):
    """単勝の買い目 (期待値1以上)。締切順に1行1レース「- 10:01　徳山4R　**2・3号艇**（1.07・1.00）」。"""
    if "単勝買い目" not in p:
        return "（この日は予想オッズのモデルがないので、単勝の買い目はありません）", 0, 0
    buy = by_deadline(p[p["単勝買い目"].fillna("").str.strip() != ""])
    if buy.empty:
        return "この日は期待値が1を超えた艇がありませんでした。", 0, 0
    out, n = [], 0
    for _, r in buy.iterrows():
        lanes = str(r["単勝買い目"]).split()
        evs = str(r.get("買い目の期待値", "")).split()
        n += len(lanes)
        out.append(f"- {r['締切']}　{r['場']}{int(r['R'])}R　**{'・'.join(lanes)}号艇**（{'・'.join(evs)}）")
    return "\n".join(out), len(buy), n


SOLID_MIN = 0.7  # 本命が堅いレース: 本命の1着確率がこれ以上


def solid_block(p):
    """本命が堅いレース。「- **08:35 鳴門1R**　◎1 79%　本線 1-2-3（5点 123・124・132・142・134）」。"""
    s = by_deadline(p[p["本命1着確率"] >= SOLID_MIN])
    if s.empty:
        return f"この日は本命の1着確率が{SOLID_MIN:.0%}以上のレースがありませんでした。", 0
    out = []
    for _, r in s.iterrows():
        five = "・".join(t.replace("-", "") for t in str(r["3連単上位5点"]).split())
        out.append(f"- **{r['締切']} {r['場']}{int(r['R'])}R**　◎{int(r['本命'])} {r['本命1着確率'] * 100:.0f}%　"
                   f"本線 {r['予想着順']}（5点 {five}）")
    return "\n".join(out), len(s)


def all_races_block(p):
    """場ごと (最初のレースが早い場から) に「- 1R 08:35　◎1 79%　1-2-3」。"""
    out = []
    for venue, g in p.groupby("場", sort=False):
        out.append(f"**{venue}**")
        out.append("")
        for _, r in g.sort_values("R").iterrows():
            out.append(f"- {int(r['R'])}R {r['締切']}　◎{int(r['本命'])} {r['本命1着確率'] * 100:.0f}%　{r['予想着順']}")
        out.append("")
    return "\n".join(out).rstrip()


def build(date, pred_dir, price=PRICE):
    path = os.path.join(pred_dir, date.strftime("%Y%m%d") + ".csv")
    if not os.path.exists(path):
        sys.exit(f"予想ファイルがありません: {path} (先に yosou.py predict --date {date})")
    p = read(path)
    first = p.groupby("場")["締切"].min().sort_values()  # 最初のレースが早い場から
    p["場"] = pd.Categorical(p["場"], categories=list(first.index), ordered=True)
    p = p.sort_values(["場", "R"])
    p["場"] = p["場"].astype(str)
    picks, pick_races, n = picks_block(p)
    solid, solid_n = solid_block(p)
    bt, bt_from, bt_to = backtest_block()
    v = dict(
        TITLE=f"【{jp_date(date)}】55万レースのデータベースからみる統計学の予想" + (f"（単勝{n}点）" if n else ""),
        DATE_JP=jp_date(date), RACES=len(p), VENUES="・".join(first.index), PRICE=price,
        MADE_AT=f"{jp_date(made)} {made:%H}時" if (made := dt.datetime.now()).date() < date else "当日の朝",
        LATER="番組表があとから出た場は、この記事には入っていません。" if made.date() < date else "",
        BACKTEST=bt, BT_FROM=bt_from, BT_TO=bt_to, RECENT=recent_block(pred_dir, date), PAYWALL=PAYWALL,
        PICKS=picks, PICK_RACES=pick_races, PICK_N=n, SOLID=solid, SOLID_N=solid_n, ALL_RACES=all_races_block(p))
    with open(TEMPLATE, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)  # ひな形の説明は記事に入れない
    meta = dict(date=date.isoformat(), title=v["TITLE"], price=price, tags=TAGS, venues=list(first.index),
                races=len(p), picks=n, paid_line_after="（ここから有料エリア）",
                made_at=dt.datetime.now().isoformat(timespec="minutes"))
    return string.Template(text).substitute(v), v["TITLE"], meta


def inline(s):
    s = html.escape(s)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def header_image(date, path):
    """見出し画像 (1280x670、競艇新聞ふう) を note/design/make_header.py で作る。Pillow がなければ作らない。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "note", "design"))
        import make_header
    except ImportError as e:
        print("見出し画像は作りませんでした:", e)
        return False
    s = seiseki.saved()
    sub = ""
    if s:
        tan = {x["項目"]: x for x in s["periods"]["全期間"]["rows"]}.get("単勝の買い目 (期待値1以上)")
        if tan:
            sub = f"過去10年 単勝の買い目 回収率{tan['回収率'] * 100:.1f}%"
    make_header.style_shinbun(date, make_header.DEFAULT_TITLE, sub).save(path, optimize=True)
    return True


def to_html(md, title, image=None):
    """Note の編集画面に貼り付けられる HTML (見出し・太字・箇条書きだけ使う。Note は表に対応していない)。"""
    body, in_list = [], False
    for line in md.splitlines():
        if line.startswith("# "):
            continue  # タイトルは別の欄に入れる
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("### "):
            body.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line == "---":
            body.append("<hr>")
        elif line == "**（ここから有料エリア）**":
            body.append(f'<p class="paywall">{inline(line)}</p>')
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
    if in_list:
        body.append("</ul>")
    t = html.escape(title)
    img = (f'<p class="head"><img src="{image}" alt="見出し画像"><br>'
           f'<a href="{image}" download>見出し画像を保存</a>（Noteの「見出し画像」に設定）</p>\n') if image else ""
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t}</title>
<style>
:root{{--bg:#fff;--fg:#222;--sub:#666;--accent:#0b7a5a;--line:#d33}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181a;--fg:#e8e8e8;--sub:#aaa;--accent:#4fc3a1;--line:#f77}}}}
body{{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Hiragino Sans",sans-serif;
max-width:720px;margin:0 auto;padding:16px;line-height:1.8}}
.bar{{position:sticky;top:0;background:var(--bg);padding:8px 0;display:flex;gap:8px;flex-wrap:wrap;
border-bottom:1px solid var(--sub)}}
button{{font-size:15px;padding:8px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);
color:#fff;cursor:pointer}}
#msg{{color:var(--sub);align-self:center;font-size:14px}}
.title{{font-size:1.4em;font-weight:bold;margin:16px 0}}
.head img{{width:100%;height:auto;border-radius:6px}}
.paywall{{color:var(--line);font-weight:bold;text-align:center}}
</style></head><body>
<div class="bar"><button onclick="copyTitle()">タイトルをコピー</button>
<button onclick="copyBody()">本文をコピー</button><span id="msg"></span></div>
{img}<div class="title" id="title">{t}</div>
<div id="body">
{chr(10).join(body)}
</div>
<script>
function done(m){{document.getElementById('msg').textContent=m}}
function copyTitle(){{navigator.clipboard.writeText(document.getElementById('title').textContent)
.then(()=>done('タイトルをコピーしました'),()=>done('コピーできませんでした。選択してコピーしてください'))}}
function copyBody(){{const el=document.getElementById('body');
try{{const item=new ClipboardItem({{'text/html':new Blob([el.innerHTML],{{type:'text/html'}}),
'text/plain':new Blob([el.innerText],{{type:'text/plain'}})}});
navigator.clipboard.write([item]).then(()=>done('本文をコピーしました。Noteの本文に貼り付けてください'),fallback)}}
catch(e){{fallback()}}
function fallback(){{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();
s.removeAllRanges();s.addRange(r);document.execCommand('copy');s.removeAllRanges();done('本文をコピーしました')}}}}
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="tomorrow", help="記事にする日 (YYYY-MM-DD / today / tomorrow)")
    ap.add_argument("--pred", default=os.path.join(ROOT, "predictions"))
    ap.add_argument("--out", help="出力先 (既定: <pred>/note)")
    ap.add_argument("--price", type=int, default=PRICE, help="記事の価格 (円)")
    a = ap.parse_args()
    rel = {"today": 0, "tomorrow": 1}
    date = dt.date.today() + dt.timedelta(days=rel[a.date]) if a.date in rel else dt.date.fromisoformat(a.date)
    md, title, meta = build(date, a.pred, a.price)
    out = a.out or os.path.join(a.pred, "note")
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, date.strftime("%Y%m%d"))
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    image = os.path.basename(base) + ".png" if header_image(date, base + ".png") else None
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(to_html(md, title, image))
    stem = os.path.basename(base)
    meta |= dict(html=stem + ".html", md=stem + ".md", image=image)
    with open(os.path.join(out, "latest.json"), "w", encoding="utf-8") as f:  # 17時の自動投稿が読む
        json.dump(meta, f, ensure_ascii=False, indent=1)
    write_index(out)
    print(f"{title}\n→ {base}.md / {base}.html")


def write_index(out):
    """note/index.html: 作った記事の一覧 (新しい順)。"""
    days = sorted((f[:8] for f in os.listdir(out) if re.fullmatch(r"\d{8}\.html", f)), reverse=True)
    items = "\n".join(f'<li><a href="{d}.html">{jp_date(dt.date(int(d[:4]), int(d[4:6]), int(d[6:])))}</a>'
                      f' ・ <a href="{d}.md">md</a></li>' for d in days)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Note記事の下書き</title>
<style>:root{{--bg:#fff;--fg:#222}}@media (prefers-color-scheme:dark){{:root{{--bg:#16181a;--fg:#e8e8e8}}}}
body{{background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:16px;
line-height:2}}a{{color:inherit}}</style></head><body><h1>Note記事の下書き</h1><ul>
{items}
</ul></body></html>
""")


if __name__ == "__main__":
    main()

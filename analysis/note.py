"""Note.com に載せる毎日の予想記事を、決まった書式 (analysis/note/template.md) で作る。

  python analysis/note.py --date 2026-11-01      predictions/20261101.csv の予想から記事を作る
  python analysis/note.py --date tomorrow        明日の分 (today / tomorrow も可)

出力は predictions/note/YYYYMMDD.md (記事の元)、YYYYMMDD.png (見出し画像) と YYYYMMDD.html (ブラウザで開いて「本文をコピー」→ Note の
編集画面に貼り付ける用)。Note には公式の投稿 API がないので、公開は人が Note の画面で行う:
  1. HTML を開いて「タイトルをコピー」「本文をコピー」→ Note の新規記事に貼る
  2. 「（ここから有料エリア）」の位置で「有料エリア設定」のラインを引き、価格 (既定 100円) を確かめて公開
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
PRICE = 100  # 記事の価格 (円)
HEADER_STYLE = "hot"  # 見出し画像のデザイン (note/design/make_header.py の STYLES)
TAGS = ["ボートレース", "競艇", "ボートレース予想", "競艇予想", "データ分析"]  # Note のハッシュタグ (5つ)
# 有料ラインの位置。Note の「有料エリア設定」でこの行のところにラインを引く
PAYWALL = "---\n\n**（ここから有料エリア）**\n\n---"
# 有料部分の「絞り込み3点」(analysis/osusume/ の検証で、過去5年の回収率 124.3%)。サイトのおすすめ舟券と同じ選び方
SHIBORI_EV = 1.1      # 期待値 (1着確率 × 予想オッズ) の下限
SHIBORI_P = 0.10      # 1着確率の下限
SHIBORI_FROM = "11:00"  # この時刻以降に締切のレースだけ
SHIBORI_N = 3         # 期待値の高い順に何点
SHIBORI_BET = 300     # 1点の金額 (円)


def jp_date(d):
    return f"{d.month}月{d.day}日({WEEK[d.weekday()]})"


def pct(x):
    return f"{x * 100:.1f}%"


def read(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"単勝買い目": str, "買い目の期待値": str})


def by_deadline(p):
    return p.sort_values(["締切", "場", "R"], kind="stable")


def all_races_block(p):
    """場ごと (最初のレースが早い場から) に「- 1R 08:35　◎1 79%　1-2-3　単勝 2・3」。"""
    out = []
    for venue, g in p.groupby("場", sort=False):
        out.append(f"### {venue}")
        out.append("")
        for _, r in g.sort_values("R").iterrows():
            tan = str(r.get("単勝買い目", "") or "").replace("nan", "").split()
            out.append(f"- {int(r['R'])}R {r['締切']}　◎{int(r['本命'])} {r['本命1着確率'] * 100:.0f}%　{r['予想着順']}"
                       + (f"　単勝 {'・'.join(tan)}" if tan else ""))
        out.append("")
    return "\n".join(out).rstrip()


def shibori(p):
    """絞り込み3点: 締切 SHIBORI_FROM 以降・期待値 SHIBORI_EV 以上・1着確率 SHIBORI_P 以上の単勝を、期待値の高い順に SHIBORI_N 点。"""
    if "1号艇の予想オッズ" not in p:
        return pd.DataFrame()
    rows = []
    for _, r in p.iterrows():
        if str(r["締切"]) < SHIBORI_FROM:
            continue
        for l in range(1, 7):
            prob, odds = r.get(f"{l}号艇"), r.get(f"{l}号艇の予想オッズ")
            if pd.isna(prob) or pd.isna(odds):
                continue
            ev = float(prob) * float(odds)
            if ev >= SHIBORI_EV and prob >= SHIBORI_P:
                rows.append(dict(race_id=r["race_id"], 場=r["場"], R=int(r["R"]), 締切=r["締切"], 艇=l,
                                 確率=float(prob), オッズ=float(odds), 期待値=ev))
    if not rows:
        return pd.DataFrame()
    b = pd.DataFrame(rows).sort_values(["期待値", "締切"], ascending=[False, True]).head(SHIBORI_N)
    return b.sort_values(["締切", "場", "R", "艇"])


def shibori_block(p):
    b = shibori(p)
    if b.empty:
        return "この日は条件に合う艇がありませんでした。買い目なしの日です。", 0
    out = []
    for _, r in b.iterrows():
        out.append(f"- **{r['締切']}　{r['場']}{r['R']}R　単勝 {r['艇']}号艇**　{SHIBORI_BET}円"
                   f"（1着の確率 {r['確率'] * 100:.0f}%・予想オッズ {r['オッズ']:.1f}倍・期待値 {r['期待値']:.2f}）")
    return "\n".join(out), len(b)


def shibori_result(pred_dir, d):
    """d の日の絞り込み3点を、答え合わせ (YYYYMMDD_check.csv) で採点する。(点数, 的中, 払戻) / データがなければ None。"""
    pp = os.path.join(pred_dir, d.strftime("%Y%m%d") + ".csv")
    cp = os.path.join(pred_dir, d.strftime("%Y%m%d") + "_check.csv")
    if not (os.path.exists(pp) and os.path.exists(cp)):
        return None
    c = read(cp)
    if "単勝配当" not in c:  # 単勝の払戻を記録していない日 (2026-11-08 まで) は数えない
        return None
    c = c.set_index("race_id")
    b = shibori(read(pp))
    n = hit = got = 0
    for _, r in b.iterrows():
        if r["race_id"] not in c.index:
            continue
        x = c.loc[r["race_id"]]
        n += 1
        if str(x["結果"])[:1] == str(r["艇"]) and pd.notna(x.get("単勝配当")):
            hit += 1
            got += int(x["単勝配当"]) * SHIBORI_BET // 100
    return n, hit, got


def recent_block(pred_dir, date):
    """公開した予想で絞り込み3点を買った場合の、前日と累計。"""
    log_path = os.path.join(pred_dir, "log.csv")
    if not os.path.exists(log_path):
        return ""
    days = sorted(x for x in pd.read_csv(log_path, encoding="utf-8-sig")["date"] if x < date.isoformat())
    res = [(dt.date.fromisoformat(x), r) for x in days if (r := shibori_result(pred_dir, dt.date.fromisoformat(x)))]
    res = [(d, r) for d, r in res if r[0]]
    if not res:
        return ""
    d, (n, hit, got) = res[-1]
    out = ["## 公開した予想で同じように買うと", "",
           f"- {jp_date(d)}: {n}点で{hit}点的中、{n * SHIBORI_BET:,}円 → {got:,}円（回収率 **{pct(got / (n * SHIBORI_BET))}**）"]
    if len(res) > 1:
        tn, th, tg = (sum(r[i] for _, r in res) for i in range(3))
        out.append(f"- {jp_date(res[0][0])}からの{len(res)}日: {tn}点で{th}点的中、{tn * SHIBORI_BET:,}円 → {tg:,}円"
                   f"（回収率 **{pct(tg / (tn * SHIBORI_BET))}**）")
    out.append("")
    out.append("日数が少ないうちは、回収率は大きく上下します。")
    return "\n".join(out)


def build(date, pred_dir, price=PRICE):
    path = os.path.join(pred_dir, date.strftime("%Y%m%d") + ".csv")
    if not os.path.exists(path):
        sys.exit(f"予想ファイルがありません: {path} (先に yosou.py predict --date {date})")
    p = read(path)
    first = p.groupby("場")["締切"].min().sort_values()  # 最初のレースが早い場から
    p["場"] = pd.Categorical(p["場"], categories=list(first.index), ordered=True)
    p = p.sort_values(["場", "R"])
    p["場"] = p["場"].astype(str)
    sh, sh_n = shibori_block(p)
    made = dt.datetime.now()
    v = dict(
        TITLE=f"【{jp_date(date)}】全{len(p)}レースの予想は無料｜過去5年の検証で回収率124%の単勝3点",
        DATE_JP=jp_date(date), RACES=len(p), VENUES="・".join(first.index), PRICE=price,
        LATER="番組表があとから出た場は入っていません。" if made.date() < date else "",
        RECENT=recent_block(pred_dir, date), PAYWALL=PAYWALL, ALL_RACES=all_races_block(p),
        SHIBORI=sh, SH_EV=SHIBORI_EV, SH_P=f"{SHIBORI_P:.0%}", SH_FROM=SHIBORI_FROM, SH_BET=SHIBORI_BET,
        SH_DAY=f"{SHIBORI_BET * SHIBORI_N:,}")
    with open(TEMPLATE, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)  # ひな形の説明は記事に入れない
    text = re.sub(r"\n{3,}", "\n\n", string.Template(text).substitute(v))  # RECENT が空のときの空行
    meta = dict(date=date.isoformat(), title=v["TITLE"], price=price, tags=TAGS, venues=list(first.index),
                races=len(p), picks=sh_n, paid_line_after="（ここから有料エリア）",
                made_at=made.isoformat(timespec="minutes"))
    return text, v["TITLE"], meta


def inline(s):
    s = html.escape(s)
    s = re.sub(r"\[(.+?)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)  # [文字](URL)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def best_hit(pred_dir, date):
    """直近の答え合わせで実際に当たった一番高い配当の一言 (例「10/30 唐津9R 3連単1点 3,470円的中」)。なければ空。"""
    log_path = os.path.join(pred_dir, "log.csv")
    if not os.path.exists(log_path):
        return ""
    days = sorted(d for d in pd.read_csv(log_path, encoding="utf-8-sig")["date"] if d < date.isoformat())
    if not days:
        return ""
    d = dt.date.fromisoformat(days[-1])
    path = os.path.join(pred_dir, d.strftime("%Y%m%d") + "_check.csv")
    if not os.path.exists(path):
        return ""
    c = read(path)
    hits = []  # (配当, 一言)
    for _, r in c.iterrows():
        where = f"{d.month}/{d.day} {r['場']}{int(r['R'])}R"
        if r.get("単勝的中", 0) > 0 and r.get("単勝払戻", 0) > 0:
            hits.append((int(r["単勝払戻"]), f"{where} 単勝 {int(r['単勝払戻']):,}円的中"))
        if bool(r.get("予想着順的中")) and pd.notna(r.get("三連単配当")):
            hits.append((int(r["三連単配当"]), f"{where} 3連単1点 {int(r['三連単配当']):,}円的中"))
    return max(hits)[1] if hits else ""


def header_image(date, path, pred_dir, races=None):
    """見出し画像 (1280x670) を note/design/make_header.py で作る。Pillow がなければ作らない。
    hot スタイルの小見出しは、直近の答え合わせで実際に当たった一番高い配当 (利益を約束する言葉は入れない)。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "note", "design"))
        import make_header
    except ImportError as e:
        print("見出し画像は作りませんでした:", e)
        return False
    if HEADER_STYLE == "hot":
        sub = best_hit(pred_dir, date)
    else:
        s = seiseki.saved()
        sub = ""
        if s:
            tan = {x["項目"]: x for x in s["periods"]["全期間"]["rows"]}.get("単勝の買い目 (期待値1以上)")
            if tan:
                sub = f"過去10年 単勝の買い目 回収率{tan['回収率'] * 100:.1f}%"
    args = (date, make_header.DEFAULT_TITLE, sub)
    img = make_header.STYLES[HEADER_STYLE](*args, races=races) if HEADER_STYLE == "hot" else make_header.STYLES[HEADER_STYLE](*args)
    img.save(path, optimize=True)
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
    image = os.path.basename(base) + ".png" if header_image(date, base + ".png", a.pred, meta["races"]) else None
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

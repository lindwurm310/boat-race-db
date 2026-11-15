"""note.com に明日の予想記事を投稿する（Microsoft Edge を Playwright で操作する）。

使い方（BoatRaceNotePost フォルダで）:
  py note_post.py login        最初に1回。専用の Edge が開くので note.com にログインして閉じる
  py note_post.py post --dry   試運転。「投稿する」の直前で止めて画面を保存する
  py note_post.py post         本番。公開まで行い、最後に OK/SKIP/NG の1行を出す

準備: py -m pip install playwright   （ブラウザはパソコンの Edge を使うので追加の取得は不要）
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import traceback
import urllib.request

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

BASE = "https://lindwurm310.github.io/boat-race-db/note/"
CREATOR = "lindwurm310"
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "BoatRaceNotePost", "edge-profile")
LOGDIR = os.path.join(HERE, "log")
JST = dt.timezone(dt.timedelta(hours=9))


def say(msg):
    print(msg, flush=True)


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "BoatRaceNotePost"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8")


def tomorrow():
    return (dt.datetime.now(JST) + dt.timedelta(days=1)).strftime("%Y-%m-%d")


def already_published(title):
    try:
        j = json.loads(get(f"https://note.com/api/v2/creators/{CREATOR}/contents?kind=note&page=1"))
        for n in j["data"]["contents"]:
            if n.get("name") == title:
                return n.get("noteUrl")
    except Exception as e:  # API が読めないときは重複チェックなしで進めない
        raise RuntimeError(f"note の公開済みチェックに失敗: {e}")
    return None


def article_body(html_name):
    page = get(BASE + html_name)
    m = re.search(r'<div id="body">\s*(.*?)\s*</div>\s*<script>', page, re.S)
    if not m:
        raise RuntimeError("記事ページから本文を取り出せない")
    return m.group(1)


def browser(p):
    return p.chromium.launch_persistent_context(
        PROFILE, channel="msedge", headless=False, viewport={"width": 1280, "height": 900},
        locale="ja-JP", permissions=["clipboard-read", "clipboard-write"],
        args=["--disable-blink-features=AutomationControlled"])


def shot(page, name):
    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, f"{dt.datetime.now(JST):%Y%m%d_%H%M%S}_{name}.png")
    try:
        page.screenshot(path=path, full_page=True)
    except Exception:
        pass
    return path


def click_text(page, *texts, timeout=15000):
    """ボタンやリンクを表示文字で探して押す（候補を順に試す）。"""
    last = None
    for t in texts:
        loc = page.get_by_role("button", name=t)
        try:
            loc.first.wait_for(state="visible", timeout=timeout)
            loc.first.click()
            return
        except PWTimeout as e:
            last = e
        loc = page.get_by_text(t, exact=True)
        try:
            loc.first.wait_for(state="visible", timeout=3000)
            loc.first.click()
            return
        except PWTimeout as e:
            last = e
    raise RuntimeError(f"ボタンが見つからない: {texts}") from last


def _try_file_button(page, loc, img_path):
    """ボタンを押してファイル選択が出たら画像を渡す。出なければ False。"""
    try:
        with page.expect_file_chooser(timeout=6000) as fc:
            loc.click()
            up = page.get_by_text(re.compile("画像をアップロード|アップロード"))
            try:
                up.first.wait_for(state="visible", timeout=2500)
                up.first.click()
            except PWTimeout:
                pass
        fc.value.set_files(img_path)
        return True
    except PWTimeout:
        page.keyboard.press("Escape")
        return False


def set_header_image(page, img_path):
    # エディタ上部（タイトル欄より上）のアイコンだけのボタン → ファイル選択
    done = False
    for sel in ['button[aria-label*="画像"]', 'button:has-text("画像を追加")']:
        loc = page.locator(sel)
        if loc.count() and _try_file_button(page, loc.first, img_path):
            done = True
            break
    if not done:
        title = page.locator('textarea[placeholder*="タイトル"], [data-placeholder*="タイトル"]').first
        ty = title.bounding_box()["y"]
        btns = page.locator("button")
        for i in range(btns.count()):
            b = btns.nth(i)
            try:
                box = b.bounding_box()
                if not box or not b.is_visible() or box["y"] + box["height"] > ty + 5:
                    continue
                if (b.inner_text() or "").strip():
                    continue
            except Exception:
                continue
            if _try_file_button(page, b, img_path):
                done = True
                break
    if not done:
        raise RuntimeError("見出し画像のボタンが見つからない")
    # トリミング画面が出たらそのまま保存
    try:
        click_text(page, "保存", "適用", "完了", timeout=15000)
    except RuntimeError:
        pass
    page.wait_for_timeout(3000)


def set_title(page, title):
    loc = page.locator('textarea[placeholder*="タイトル"], [data-placeholder*="タイトル"]')
    loc.first.wait_for(state="visible", timeout=30000)
    loc.first.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.insert_text(title)  # 本物の入力として届ける（fill だと note が入力済みと認識しないことがある）
    page.wait_for_timeout(500)


def paste_body(page, body_html):
    ed = page.locator(".ProseMirror[contenteditable=true]").first
    ed.wait_for(state="visible", timeout=30000)
    ed.click()
    # クリップボードに HTML を入れて、本物の Ctrl+V で貼り付ける
    page.evaluate("""async (html) => {
        const tmp = document.createElement('div'); tmp.innerHTML = html;
        await navigator.clipboard.write([new ClipboardItem({
            'text/html': new Blob([html], {type: 'text/html'}),
            'text/plain': new Blob([tmp.innerText], {type: 'text/plain'})})]);
    }""", body_html)
    ed.click()
    page.keyboard.press("Control+V")
    page.wait_for_timeout(4000)
    text = ed.inner_text()
    if "ここから有料エリア" not in text or len(text) < 500:
        raise RuntimeError(f"本文の貼り付けに失敗（{len(text)}文字）")


def save_draft(page):
    page.wait_for_timeout(3000)
    try:
        click_text(page, "下書き保存", timeout=5000)
        page.wait_for_timeout(3000)
    except RuntimeError:
        pass


def set_tags(page, tags):
    inp = page.locator('input[placeholder*="ハッシュタグ"]').first
    try:
        inp.wait_for(state="visible", timeout=5000)
    except PWTimeout:  # placeholder が無い版：「ハッシュタグ」見出しの直後の input
        inp = page.locator('xpath=//*[contains(normalize-space(text()),"ハッシュタグ")]/following::input[1]').first
        inp.wait_for(state="visible", timeout=15000)
    for t in tags:
        inp.click()
        inp.fill(t)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)


def set_paid(page, price):
    click_text(page, "有料")
    page.wait_for_timeout(1000)
    # 価格欄には name も placeholder も無いので、「価格」という見出しの直後の input を探す
    price_in = page.locator('xpath=//*[normalize-space(text())="価格"]/following::input[1]').first
    price_in.wait_for(state="visible", timeout=15000)
    if price_in.input_value().strip() != str(price):
        price_in.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(str(price))
        page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    if price_in.input_value().strip() != str(price):
        raise RuntimeError(f"価格が {price} 円にならない（{price_in.input_value()}）")


def set_paid_line(page):
    """有料ラインを「（ここから有料エリア）」の下の区切り線のあと（「◯月◯日の予想」見出しの直前）に置く。"""
    idx = page.evaluate("""() => {
        const btns = [...document.querySelectorAll('button')].filter(b => /ラインをこの場所に変更/.test(b.innerText));
        const all = [...document.querySelectorAll('body *')];
        const pay = all.find(e => e.children.length === 0 && /ここから有料エリア/.test(e.textContent));
        if (!pay || !btns.length) return -1;
        const head = all.find(e => /^H[1-6]$/.test(e.tagName) && /\\d+月\\d+日.*の予想/.test(e.textContent)
                         && (pay.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING));
        const after = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
        let pick = -1;
        btns.forEach((b, i) => { if (after(pay, b) && (!head || after(b, head))) pick = i; });
        if (pick < 0) pick = btns.findIndex(b => after(pay, b));
        return pick;
    }""")
    if idx < 0:
        raise RuntimeError("有料ラインの位置ボタンが見つからない")
    btn = page.get_by_role("button", name=re.compile("ラインをこの場所に変更")).nth(idx)
    btn.scroll_into_view_if_needed()
    btn.click()
    page.wait_for_timeout(1000)


def post(dry):
    date = tomorrow()
    j = json.loads(get(BASE + "latest.json"))
    if j.get("date") != date:
        return f"SKIP {date} 明日の記事がまだできていない（latest.json は {j.get('date')}）"
    url = already_published(j["title"])
    if url and not (dry and os.environ.get("NOTE_FORCE") == "1"):  # 試運転だけ NOTE_FORCE=1 で重複チェックを飛ばせる
        return f"OK {date} {url}"
    body = article_body(j["html"])
    img = os.path.join(HERE, "header.png")
    with open(img, "wb") as f:
        f.write(get(BASE + j["image"], binary=True))

    with sync_playwright() as p:
        ctx = browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto("https://editor.note.com/new", wait_until="domcontentloaded")
            if "login" in page.url:
                return f"NG {date} note にログインしていない（py note_post.py login で一度ログインしてください）"
            page.wait_for_load_state("networkidle")
            set_title(page, j["title"])
            paste_body(page, body)
            set_header_image(page, img)
            save_draft(page)
            say("下書きを作りました")
            click_text(page, "公開に進む")
            page.wait_for_timeout(2000)
            if page.get_by_text("タイトル、本文を入力してください").count():
                raise RuntimeError("note がタイトル・本文を入力済みと認識していない")
            page.wait_for_load_state("networkidle")
            set_paid(page, j["price"])
            set_tags(page, j["tags"])
            click_text(page, "有料エリア設定")
            page.wait_for_timeout(2000)
            set_paid_line(page)
            shot(page, "before_publish")
            if dry:
                return f"SKIP {date} 試運転のため「投稿する」の直前で止めました（画面は log に保存）"
            click_text(page, "投稿する", "公開する")
            # 公開完了のダイアログや記事ページを待つ
            for _ in range(30):
                page.wait_for_timeout(2000)
                url = already_published(j["title"])
                if url:
                    shot(page, "published")
                    return f"OK {date} {url}"
            return f"NG {date} 投稿を押したが公開を確認できない"
        except Exception as e:
            path = shot(page, "error")
            traceback.print_exc()
            return f"NG {date} {e}（画面: {path}）"
        finally:
            ctx.close()


def login():
    with sync_playwright() as p:
        ctx = browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://note.com/login")
        say("開いた Edge で note.com にログインしてください。ログインできたら、この Edge のウィンドウを閉じます。")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["login", "post"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd == "login":
        login()
        return
    try:
        line = post(a.dry)
    except Exception as e:
        line = f"NG {tomorrow()} {e}"
    say(line)
    sys.exit(0 if line.startswith(("OK", "SKIP")) else 1)


if __name__ == "__main__":
    main()

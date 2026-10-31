# Note 見出し画像・アイコン

「AI感」を減らすために、生成AIで作った絵は使わず、紙・罫線・判子・手描きの線・枠番の6色といった
「人が手で作ったもの」に見える要素だけで組み立てています。グラデーションや光る効果、近未来っぽい
グラフィックも使っていません。

## 見出し画像（1280×670）

| 案 | ファイル | 雰囲気 |
|---|---|---|
| **D 大きな文字＋水面（毎日の記事で使用）** | `out/header_race_YYYYMMDD.png` | 「ボートレース予想」を高さ150pxで大きく、日付は赤地に白抜き、下に水面を走る枠番6色の札。Note の一覧で幅300px前後に縮んでも読める |
| A 方眼ノート | `out/header_note_YYYYMMDD.png` | 方眼紙に手書き文字、赤ペンで1号艇に丸。研究ノートを見せる感じ |
| B 競艇新聞 | `out/header_shinbun_YYYYMMDD.png` | 明朝の大見出し、二重罫、朱色の日付印。予想紙・専門紙っぽい |
| C 枠色ストライプ | `out/header_simple_YYYYMMDD.png` | 白地に枠番6色の帯と大きな日付。いちばんすっきり |

## アイコン（800×800、Noteでは丸く切り抜かれます）

- `out/icon_stripe.png` 枠番6色のストライプ（小さく表示されても目立つ）
- `out/icon_hanko.png` 朱色の「統」の判子
- `out/icon_graph.png` 方眼紙に手描きの棒グラフ（1号艇だけ高い。形だけのイメージ）
- `out/icon_one.png` 1号艇の「1」
- `*_maru.png` は丸く切り抜いたときの見え方の確認用、`icons_preview.png` は4案の比較

## 毎日の作り方

```
pip install pillow
python make_header.py --style race --date 2026-11-02 --sub "過去10年 単勝の買い目 回収率107.5%"
```

- `--style` は `race` / `note` / `shinbun` / `simple` / `all`（毎日の記事 `analysis/note.py` は `race` を使う）
- `--date` を省くと今日の日付。曜日は自動で入ります
- `--title` でタイトルを変えられます（改行は `\n`）。`race` は画像の文字を「ボートレース予想」に固定し、長いタイトルは記事タイトル側に任せます
- `--sub` は小見出し。案Bは省くと「1号艇の1着率は54.5%。…」が入ります
- フォントは `fonts/` に同梱（すべて Google Fonts の SIL Open Font License。商用利用可）

図中の「1号艇の1着率 54.5%」は analysis/README.md の検証A（2026年7〜10月、17,964レース）の値です。

# Note への毎日17時の自動投稿

1. 毎日 16:00（予備 16:30）に GitHub Actions の `note-daily` が明日の予想をして、記事の本文・見出し画像・
   `latest.json`（タイトル・価格300円・ハッシュタグ5つ）を GitHub Pages の `note/` に置く。
2. 毎日 17:00 にあきさんのパソコンのタスク `BoatRaceNotePost` が `note_post.bat` を動かす。
   まず `note_post.py`（Python + Playwright。専用プロファイルの Microsoft Edge を操作）が note.com に
   下書き作成 → タイトル・本文・見出し画像 → 有料300円・タグ5つ・有料ライン → 投稿 まで行う（Claude は使わない）。
   公開できたら、Claude Code（`claude -p`）が `x_prompt.md` の手順で Edge の X（@BoatRace__B）に投稿とフォローをする。
3. 結果は `log/YYYY-MM-DD.txt` に残る（`OK` / `SKIP` / `NG`、`XOK` / `XNG`、`FOLLOW` / `FNG`）。19:00 まで30分ごとにもう一度動く
   （note は1日3回、X は1日2回まで。終わっていればすぐ終わる）。note_post.py の画面写真は `log/*.png`。
4. GitHub の定時実行は数時間遅れることがあるため、毎日 15:47 に Claude の定時タスクが `note-daily` を起動する。

## 準備（一度だけ）

1. このリポジトリをパソコンに置く（`git pull` で最新にしておく）。
2. Claude Code を最新にし（`claude --version` が 2.1.302 以上）、`/login` でログインしておく（API キーでは Chrome 連携が使えない）。
3. Microsoft Edge に Claude 拡張（Chrome ウェブストアの「Claude」）を入れて Claude にログインし、note.com にも Edge でログインしたままにしておく。Chrome 側の Claude 拡張はオフにして、つながるブラウザを Edge だけにする（`claude` で `/chrome` →「Select browser…」で Edge を選んでもよい）。
4. リポジトリのフォルダの PowerShell で `powershell -ExecutionPolicy Bypass -File .\note\post\install_task.ps1` を実行する。
5. `py -m pip install playwright` のあと `py note_post.py login` で、開いた Edge で note.com に一度ログインする。
6. 試運転: `.\note\post\note_post.bat test` を実行する。note の「投稿する」の直前で止まる（X は動かない）。log の写真を確かめる。
   試運転で止められたり許可を聞かれたりした操作は、`/permissions` や Edge の Claude 拡張の設定で許可しておく。

## 注意

- 17:00 にパソコンが起動していて、Edge が開いている必要がある（スリープ中は動かない。起動したら遅れて動く）。
- 16:00 の時点で番組表が出ていない場は記事に入らない（記事にその旨を書く）。翌朝の予想ページには足される。
- `latest.json` の日付が明日でないとき（記事ができていない）や、同じタイトルの記事が公開済みのときは投稿しない。
- note には公式の投稿 API がなく、画面を自動操作している。note の画面が変わると止まることがある。
- 有料設定は「公開に進む」の画面でしか保存できない。本人情報の入力を求められたら止まる。
- 「返金申請の受け付け」は初期設定（オフ）のまま。
- やめるとき: `Unregister-ScheduledTask -TaskName "BoatRaceNotePost" -Confirm:$false`

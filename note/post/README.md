# Note への毎日17時の自動投稿

1. 毎日 16:00（予備 16:30）に GitHub Actions の `note-daily` が明日の予想をして、記事の本文・見出し画像・
   `latest.json`（タイトル・価格300円・ハッシュタグ5つ）を GitHub Pages の `note/` に置く。
2. 毎日 17:00 にあきさんのパソコンのタスク `BoatRaceNotePost` が `note_post.bat` を動かす。
   Claude Code が Chrome（Claude in Chrome 拡張）で note.com を操作し、`post_prompt.md` の手順で
   下書き作成 → 見出し画像 → 本文 → 有料300円・有料ライン・タグ → 投稿 まで行う。
3. 結果は `note/post/log/YYYYMMDD.txt` に残る（`OK` / `SKIP` / `NG`）。

## 準備（一度だけ）

1. このリポジトリをパソコンに置く（`git pull` で最新にしておく）。
2. Claude Code を最新にし（`claude --version` が 2.1.302 以上）、`/login` でログインしておく（API キーでは Chrome 連携が使えない）。
3. Chrome に Claude in Chrome 拡張を入れて、note.com にログインしたままにしておく。
4. リポジトリのフォルダの PowerShell で `powershell -ExecutionPolicy Bypass -File .\note\post\install_task.ps1` を実行する。
5. 試運転: `.\note\post\note_post.bat test` を実行する。「投稿する」の直前で止まるので、Chrome の画面と log を確かめる。
   試運転で止められたり許可を聞かれたりした操作は、`/permissions` や Chrome 拡張の設定で許可しておく。

## 注意

- 17:00 にパソコンが起動していて、Chrome が開いている必要がある（スリープ中は動かない。起動したら遅れて動く）。
- 16:00 の時点で番組表が出ていない場は記事に入らない（記事にその旨を書く）。翌朝の予想ページには足される。
- `latest.json` の日付が明日でないとき（記事ができていない）や、同じタイトルの記事が公開済みのときは投稿しない。
- note には公式の投稿 API がなく、画面を自動操作している。note の画面が変わると止まることがある。
- 有料設定は「公開に進む」の画面でしか保存できない。本人情報の入力を求められたら止まる。
- 「返金申請の受け付け」は初期設定（オフ）のまま。
- やめるとき: `Unregister-ScheduledTask -TaskName "BoatRaceNotePost" -Confirm:$false`

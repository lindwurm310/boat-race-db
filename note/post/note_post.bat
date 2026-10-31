@echo off
rem 毎日17時に Note へ明日の予想記事を投稿する (タスクスケジューラから呼ばれる)。
rem 条件: パソコンが起動していて、Chrome が開き、note.com にログイン済みで、Claude in Chrome 拡張がつながっていること。
rem 試運転: note_post.bat test  (「投稿する」の直前で止める)
chcp 65001 >nul
cd /d "%~dp0\..\.."
if not exist "note\post\log" mkdir "note\post\log"
set LOG=note\post\log\%date:~0,4%%date:~5,2%%date:~8,2%.txt
set ASK=note\post\post_prompt.md の指示どおりに、明日の予想記事を note.com に有料300円で投稿してください。投稿先は note.com のあきさん (lindwurm310) のアカウントで、公開まですることをあきさんが依頼しています。
if /i "%1"=="test" set ASK=note\post\post_prompt.md の手順 1〜3 を、明日の予想記事について試運転してください。手順3の「投稿する」は押さずに止め、画面の設定 (有料・300円・有料ラインの位置・タグ) を報告してください。
echo ==== %date% %time% %1 >> "%LOG%"
claude -p "%ASK%" --chrome --permission-mode auto --permission-prompts none --allowedTools "mcp__claude-in-chrome__*" "WebFetch" "Read" >> "%LOG%" 2>&1
echo ==== exit %errorlevel% >> "%LOG%"

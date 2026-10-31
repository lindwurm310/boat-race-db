@echo off
rem 毎日17時に Note へ明日の予想記事を投稿する (タスクスケジューラから呼ばれる)。
rem 条件: パソコンが起動していて、Chrome が開き、note.com にログイン済みで、Claude in Chrome 拡張がつながっていること。
chcp 65001 >nul
cd /d "%~dp0\..\.."
if not exist "note\post\log" mkdir "note\post\log"
set LOG=note\post\log\%date:~0,4%%date:~5,2%%date:~8,2%.txt
echo ==== %date% %time% >> "%LOG%"
claude -p "note\post\post_prompt.md の指示どおりに、明日の予想記事を Note に投稿してください。" --chrome --allowedTools "mcp__claude-in-chrome__*" "WebFetch" "Read" >> "%LOG%" 2>&1
echo ==== exit %errorlevel% >> "%LOG%"

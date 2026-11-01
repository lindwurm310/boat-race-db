@echo off
rem 毎日17時に Note へ明日の予想記事を投稿する (タスクスケジューラから17:00〜19:00に30分ごとに呼ばれる)。
rem 記事ができていない・投稿済みのときは Claude を呼ばずに終わる。
rem 条件: パソコンが起動していて、Chrome が開き、note.com にログイン済みで、Claude in Chrome 拡張がつながっていること。
rem 試運転: note_post.bat test  (「投稿する」の直前で止める)
chcp 65001 >nul
cd /d "%~dp0"
set PATH=%USERPROFILE%\.local\bin;%PATH%
if exist "post_prompt.md" (set PROMPT_FILE=post_prompt.md) else (set PROMPT_FILE=note\post\post_prompt.md)
if not exist "log" mkdir "log"
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).AddDays(1).ToString('yyyy-MM-dd')"') do set TOMORROW=%%d
set LOG=log\%TOMORROW%.txt
if /i not "%1"=="test" if exist "log\posted_%TOMORROW%.txt" exit /b 0
powershell -NoProfile -Command "try { $j = Invoke-RestMethod 'https://lindwurm310.github.io/boat-race-db/note/latest.json'; if ($j.date -eq '%TOMORROW%') { exit 0 } else { exit 1 } } catch { exit 1 }"
if errorlevel 1 (
  echo %date% %time% SKIP %TOMORROW% 記事がまだできていない >> "%LOG%"
  exit /b 0
)
set ASK=%PROMPT_FILE% の指示どおりに、明日 (%TOMORROW%) の予想記事を note.com に有料300円で投稿してください。投稿先は note.com のあきさん (lindwurm310) のアカウントで、公開まですることをあきさんが依頼しています。
if /i "%1"=="test" set ASK=%PROMPT_FILE% の手順 1〜3 を、明日 (%TOMORROW%) の予想記事について試運転してください。手順3の「投稿する」は押さずに止め、画面の設定 (有料・300円・有料ラインの位置・タグ) を報告してください。
echo ==== %date% %time% %1 >> "%LOG%"
claude -p "%ASK%" --chrome --permission-mode auto --permission-prompts none --allowedTools "mcp__claude-in-chrome__*" "WebFetch" "Read" >> "%LOG%" 2>&1
echo ==== exit %errorlevel% >> "%LOG%"
if /i not "%1"=="test" findstr /c:"OK %TOMORROW%" "%LOG%" >nul && echo posted> "log\posted_%TOMORROW%.txt"

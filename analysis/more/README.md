# 追加データの収集（2026-11-28〜）

これまで DB に無かったデータを集める仕組み。どれも GitHub Actions で動かし、結果は Release に置く（DB にはまだ入れていない）。
アクセスは 1 ジョブ 1 秒に 1 回まで。

| データ | 期間 | スクリプト | Actions | 置き場所 (Release) |
|---|---|---|---|---|
| 確定オッズ（3連単120・3連複20・2連単30・2連複15・拡連複15・単勝・複勝） | 2021-10-25〜2026-11-27（公式サイトは2020年ごろから残っている） | `collect_odds.py`（表の読み方は `odds_parse.py`） | `odds-history`（`odds_run.json` を push） | `odds-history` の `odds_<開始日>.jsonl.gz` |
| 締切前のオッズの動き（締切の 30・20・10・5・3・1 分前、全券種） | 毎日（main に入ってから。過去の分は残っていない） | `odds_live.py` | `odds-live`（毎日3回の予定実行） | `odds-live` の `odds_live_<日付>_<1〜3>.jsonl.gz` |
| オリジナル展示（一周・まわり足・直線など）と確定票数（全券種） | 2024年3月末ごろ〜（BOATCAST に残っている分） | `collect_boatcast.py` | `boatcast-history`（`bc_run.json` を push） | `boatcast-history` の `bc_<開始日>.jsonl.gz` |
| ファン手帳（レーサー期別成績。コース別の1〜6着・ST・F/L・能力指数・身長・出身地など） | 2001年後期〜2027年前期の52期 | `fan.py` | `more-data`（`more_run.txt` を push） | `more-data` の `fan.csv.gz` |
| 潮位（気象庁の推算潮位。毎時と満潮・干潮） | 2002〜2027年、海水・汽水の19場 | `tide.py` | `more-data` | `more-data` の `tide_hourly.csv.gz`・`tide_hilo.csv.gz` |
| 気温・水温・風向（直前情報のページから） | 2022-10-25〜2026-10-24（`bi-history` と同じ） | `parse_weather.py` | `more-data` | `more-data` の `weather_bi.csv.gz` |

## 注意
- 確定オッズは「締切時オッズ」。買うときに見えるのは締切前のオッズなので、回収率の検証で使うときは `odds-live` の締切前のものとの差に気をつける
- ファン手帳の期の成績は、算出期間（`calc_from`〜`calc_to`）より後のレースにだけ使う。期の途中のレースに使うと先の成績が混ざる
- 潮位は近い観測地点の推算値（`tide.py` の `VENUE_STATION`）。桐生・戸田・多摩川・びわこ・芦屋は淡水なので無し
- BOATCAST のオリジナル展示は場によって測る項目が違う（例: 徳山は一周・まわり足だけ）。1行目に項目の数、2行目に項目名がある
- BOATCAST の確定票数は、その券種の票数を組み合わせごとに並べたもの（`hyousu3` は 1着の艇ごとの行に、3連単20組と3連複などが続く）。読み方は集め終わってから決める
- 時間切れで残ったレースは、同じ `*_run.json` をもう一度 push すると続きから取る

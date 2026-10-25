# boat-race-db

ボートレース公式サイトのダウンロードデータ（競走成績 K / 番組表 B）を取得し、SQLite にまとめる。

- `fetch.py` : www1.mbrace.or.jp/od2 から日別 LZH を取得（1秒間隔）
- `build_db.py` : LZH を解析して `boatrace.sqlite` を作成
- GitHub Actions `fetch-and-build` : 年ごとに取得 → DB 作成 → Release に `boatrace.sqlite.gz` を添付

## テーブル
| テーブル | 内容 |
|---|---|
| races | レース (race_id = YYYYMMDD-場コード-R番号)。天候・風・波・決まり手・締切時刻 |
| results | 着順・選手・モーター/ボート・展示・進入・ST・タイム |
| payouts | 払戻金（単勝〜３連複、人気） |
| entries | 番組表の出走表（級別・勝率・2連率・モーター/ボート成績） |
| venues | 場コードと場名 |
| parse_issues | 解析できなかった行 |

## レース予想

`analysis/` にレース予想・買い方の検証と、毎日の予想・答え合わせのツール（Windows 対応）がある。Windows では `install.bat` をダブルクリックでインストールでき、boatrace.jp のページにおすすめを出す Chrome 拡張機能（`chrome-extension/`）もある。結果と使い方は [analysis/README.md](analysis/README.md)。

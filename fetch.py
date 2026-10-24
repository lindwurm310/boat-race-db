"""boatrace.jp 公式ダウンロード (www1.mbrace.or.jp/od2) から LZH を取得する。

  K = 競走成績, B = 番組表。1日1ファイル (全場分)。
  使い方: python3 fetch.py --kind K B --start 2002-01-01 --end 2026-10-23 --out raw
  既に取得済みのファイルと、存在しないと分かっている日 (missing.txt) は飛ばす。
"""
import argparse
import datetime as dt
import os
import time
import urllib.error
import urllib.request

BASE = "https://www1.mbrace.or.jp/od2/{kind}/{ym}/{k}{ymd}.lzh"
UA = "Mozilla/5.0 (personal research; low-rate downloader)"


def url_for(kind, d):
    return BASE.format(kind=kind, ym=d.strftime("%Y%m"), k=kind.lower(), ymd=d.strftime("%y%m%d"))


def path_for(out, kind, d):
    return os.path.join(out, kind, d.strftime("%Y"), f"{kind.lower()}{d.strftime('%y%m%d')}.lzh")


def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i == retries - 1:
                raise
        time.sleep(5 * (i + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", nargs="+", default=["K", "B"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    ap.add_argument("--out", default="raw")
    ap.add_argument("--interval", type=float, default=1.0, help="リクエスト間隔(秒)")
    a = ap.parse_args()

    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    for kind in a.kind:
        missing_path = os.path.join(a.out, kind, "missing.txt")
        os.makedirs(os.path.dirname(missing_path), exist_ok=True)
        missing = set(open(missing_path).read().split()) if os.path.exists(missing_path) else set()
        got = miss = 0
        d = start
        while d <= end:
            p = path_for(a.out, kind, d)
            key = d.isoformat()
            if not os.path.exists(p) and key not in missing:
                data = fetch(url_for(kind, d))
                if data and data[2:5] == b"-lh":
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p + ".part", "wb") as f:
                        f.write(data)
                    os.replace(p + ".part", p)
                    got += 1
                else:
                    # 404、またはエラーページ (LZHでない) は「その日のデータなし」として記録
                    with open(missing_path, "a") as f:
                        f.write(key + "\n")
                    miss += 1
                time.sleep(a.interval)
                if (got + miss) % 100 == 0:
                    print(f"{kind} {key} got={got} missing={miss}", flush=True)
            d += dt.timedelta(days=1)
        print(f"{kind} done got={got} missing={miss}", flush=True)


if __name__ == "__main__":
    main()

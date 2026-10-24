"""取得済み LZH (K=競走成績, B=番組表) を解析して SQLite に登録する。

  使い方: python3 build_db.py --raw raw --db boatrace.sqlite
  何度実行してもよい (取り込み済みファイルは飛ばす。--rebuild で作り直し)。
  解析できなかった行は parse_issues テーブルに残す。
"""
import argparse
import glob
import io
import os
import re
import sqlite3
import unicodedata

import lhafile

SCHEMA = """
CREATE TABLE IF NOT EXISTS loaded_files (name TEXT PRIMARY KEY, kind TEXT, races INTEGER);
CREATE TABLE IF NOT EXISTS venues (jcd INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS races (
  race_id TEXT PRIMARY KEY,          -- YYYYMMDD-場コード2桁-レース番号2桁
  race_date TEXT, jcd INTEGER, rno INTEGER,
  meeting_title TEXT, day_no INTEGER,
  race_type TEXT, distance INTEGER,
  weather TEXT, wind_dir TEXT, wind_speed INTEGER, wave_cm INTEGER,
  technique TEXT,                    -- 決まり手
  -- 番組表(B)由来
  program_title TEXT, deadline TEXT
);
CREATE TABLE IF NOT EXISTS results (
  race_id TEXT, lane INTEGER, racer_no INTEGER, racer_name TEXT,
  motor_no INTEGER, boat_no INTEGER, exhibition_time REAL, course INTEGER,
  start_timing TEXT, race_time TEXT,
  finish_raw TEXT,                   -- 着順の原文 (01, F, L0, K0, S1 等)
  finish INTEGER,                    -- 数字の着順のみ。失格・欠場等は NULL
  PRIMARY KEY (race_id, lane)
);
CREATE TABLE IF NOT EXISTS payouts (
  race_id TEXT, bet_type TEXT, combination TEXT, payout INTEGER, popularity INTEGER
);
CREATE TABLE IF NOT EXISTS entries (  -- 番組表(B)の出走表
  race_id TEXT, lane INTEGER, racer_no INTEGER, racer_name TEXT, age INTEGER,
  branch TEXT, weight INTEGER, class TEXT,
  nat_win_rate REAL, nat_2_rate REAL, loc_win_rate REAL, loc_2_rate REAL,
  motor_no INTEGER, motor_2_rate REAL, boat_no INTEGER, boat_2_rate REAL,
  series_results TEXT,
  PRIMARY KEY (race_id, lane)
);
CREATE TABLE IF NOT EXISTS parse_issues (file TEXT, line TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS ix_races_date ON races(race_date);
CREATE INDEX IF NOT EXISTS ix_results_racer ON results(racer_no);
CREATE INDEX IF NOT EXISTS ix_entries_racer ON entries(racer_no);
CREATE INDEX IF NOT EXISTS ix_payouts_race ON payouts(race_id);
"""

BET_TYPES = ["単勝", "複勝", "２連単", "２連複", "拡連複", "３連単", "３連複"]


def z2h(s):
    """全角英数字・記号を半角に (カナはそのまま)。"""
    return unicodedata.normalize("NFKC", s) if s else s


def clean(s):
    return z2h(s).replace("　", " ").strip() if s is not None else None


def num(s, f=int):
    try:
        return f(z2h(s).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def read_lzh(path):
    lf = lhafile.Lhafile(path)
    for info in lf.infolist():
        yield info.filename, lf.read(info.filename).decode("cp932", errors="replace")


def split_blocks(text, kind):
    """STARTK ... 01KBGN ... 01KEND ... FINALK を場ごとのブロックに分ける。"""
    blocks = re.findall(rf"^(\d\d){kind}BGN\s*$(.*?)^\1{kind}END", text, re.M | re.S)
    return [(int(j), body) for j, body in blocks]


def race_id(date, jcd, rno):
    return f"{date.replace('-', '')}-{jcd:02d}-{rno:02d}"


# ---------------- 競走成績 (K) ----------------
RE_K_RACE = re.compile(
    r"^\s*(\d{1,2})R\s+(.*?)\s+H(\d{3,4})m\s+(\S+)\s+風\s+(\S+)\s+(\d+)m\s+波\s+(\d+)cm")
RE_K_ROW = re.compile(r"^\s{1,3}(\S{1,2})\s+([1-6])\s+(\d{4})\s(.+)$")
RE_PAY = re.compile(r"(\d(?:-\d){0,2})\s+(\d+)(?:\s+人気\s+(\d+))?")
RE_DAY = re.compile(r"第\s*(\S+)\s*日")


def parse_k(date, jcd, body, issues):
    lines = body.splitlines()
    head = clean(lines[0]) if lines else ""
    title = None
    m = re.match(r"^\S+\s*\[成績\]\s+\S+\s+(.*?)\s+第\s*\d+\s*日", head)
    if m:
        title = m.group(1).strip()
    venue = None
    m = re.match(r"^(.+?)\s*\[成績\]", head)
    if m:
        venue = m.group(1).replace(" ", "")
    day_no = None
    m = RE_DAY.search(head)
    if m:
        day_no = num(m.group(1))

    races, results, payouts = [], [], []
    cur = None
    bet = None
    in_rows = False
    for raw in lines:
        line = z2h(raw).replace("　", " ") if raw else raw
        m = RE_K_RACE.match(line)
        if m:
            rno = int(m.group(1))
            cur = race_id(date, jcd, rno)
            races.append(dict(race_id=cur, race_date=date, jcd=jcd, rno=rno, meeting_title=title,
                              day_no=day_no, race_type=m.group(2).strip(), distance=int(m.group(3)),
                              weather=m.group(4), wind_dir=m.group(5), wind_speed=int(m.group(6)),
                              wave_cm=int(m.group(7)), technique=None))
            in_rows, bet = False, None
            continue
        if cur is None:
            continue
        if "ﾚｰｽﾀｲﾑ" in raw or "レースタイム" in line:
            t = re.search(r"(?:ﾚｰｽﾀｲﾑ|レースタイム)\s+(\S+)", line)
            if t:
                races[-1]["technique"] = t.group(1)
            continue
        if line.startswith("-----"):
            in_rows = True
            continue
        if in_rows:
            m = RE_K_ROW.match(raw)
            if m:
                results.append(parse_k_row(cur, raw, issues))
                continue
            in_rows = False
        # 払戻
        s = line.strip()
        if not s:
            continue
        for b in BET_TYPES:
            if s.startswith(z2h(b)):
                bet = b
                s = s[len(z2h(b)):]
                break
        else:
            if bet is None:
                continue
        found = RE_PAY.findall(s)
        if found:
            for combo, pay, pop in found:
                payouts.append((cur, bet, combo, int(pay), num(pop)))
        elif bet and s and not s.startswith("返還"):
            issues.append((None, raw, f"payout-unparsed {cur}"))
    return venue, races, results, payouts


def parse_k_row(rid, raw, issues):
    # 着 艇 登番 選手名(全角) | モーター ボート 展示 進入 ST タイム
    # 選手名は全角空白混じりなので、ASCII空白で区切って名前より後ろを数値列として読む
    m = re.match(r"^\s*(\S+)\s+([1-6])\s+(\d{4})\s(.*)$", raw)
    finish_raw, lane, racer_no, rest = m.groups()
    toks = [t for t in rest.split(" ") if t]  # 名前は全角なので ASCII 空白では切れない
    name = toks[0] if toks else ""
    nums = toks[1:]
    motor = boat = course = None
    exh = st = rtime = None
    if len(nums) >= 2:
        motor, boat = num(nums[0]), num(nums[1])
    if len(nums) >= 3:
        exh = num(nums[2], float)
    if len(nums) >= 4:
        course = num(nums[3])
    if len(nums) >= 5:
        st = nums[4]
    if len(nums) >= 6:
        rtime = nums[5]
    fr = z2h(finish_raw)
    return (rid, int(lane), int(racer_no), clean(name), motor, boat, exh, course, st, rtime,
            fr, int(fr) if fr.isdigit() else None)


# ---------------- 番組表 (B) ----------------
RE_B_RACE = re.compile(r"^\s*(\d{1,2})R\s+(.*?)\s+H(\d{3,4})m(?:.*?(\d{1,2}:\d{2}))?")
RE_B_ROW = re.compile(
    r"^([1-6]) (\d{4})(.{4})(\d{2})(.{2})(\d{2})([AB][12])\s*"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s?(.*)$")


def parse_b(date, jcd, body, issues):
    races, entries = [], []
    cur = None
    for raw in body.splitlines():
        line = z2h(raw).replace("　", " ")
        m = RE_B_RACE.match(line)
        if m:
            rno = int(m.group(1))
            cur = race_id(date, jcd, rno)
            races.append((cur, date, jcd, rno, m.group(2).strip(), int(m.group(3)), m.group(4)))
            continue
        if cur is None:
            continue
        m = RE_B_ROW.match(raw)
        if m:
            g = m.groups()
            entries.append((cur, int(g[0]), int(g[1]), clean(g[2]), num(g[3]), clean(g[4]), num(g[5]),
                            g[6], num(g[7], float), num(g[8], float), num(g[9], float), num(g[10], float),
                            num(g[11]), num(g[12], float), num(g[13]), num(g[14], float), g[15].rstrip()))
        elif re.match(r"^[1-6] \d{4}", raw):
            issues.append((None, raw, f"entry-unparsed {cur}"))
    return races, entries


def date_from_name(name):
    m = re.search(r"[kb](\d{2})(\d{2})(\d{2})", name.lower())
    y = int(m.group(1))
    y += 2000 if y < 90 else 1900
    return f"{y:04d}-{m.group(2)}-{m.group(3)}"


def load_file(con, path, kind):
    name = os.path.basename(path)
    date = date_from_name(name)
    issues = []
    nraces = 0
    for _, text in read_lzh(path):
        for jcd, body in split_blocks(text, kind):
            if kind == "K":
                venue, races, results, payouts = parse_k(date, jcd, body, issues)
                if venue:
                    con.execute("INSERT OR IGNORE INTO venues VALUES (?,?)", (jcd, venue))
                for r in races:
                    con.execute(
                        """INSERT INTO races (race_id,race_date,jcd,rno,meeting_title,day_no,race_type,distance,
                           weather,wind_dir,wind_speed,wave_cm,technique) VALUES
                           (:race_id,:race_date,:jcd,:rno,:meeting_title,:day_no,:race_type,:distance,
                            :weather,:wind_dir,:wind_speed,:wave_cm,:technique)
                           ON CONFLICT(race_id) DO UPDATE SET meeting_title=excluded.meeting_title,
                           day_no=excluded.day_no, race_type=excluded.race_type, distance=excluded.distance,
                           weather=excluded.weather, wind_dir=excluded.wind_dir, wind_speed=excluded.wind_speed,
                           wave_cm=excluded.wave_cm, technique=excluded.technique""", r)
                con.executemany("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", results)
                con.executemany("DELETE FROM payouts WHERE race_id=?", [(r["race_id"],) for r in races])
                con.executemany("INSERT INTO payouts VALUES (?,?,?,?,?)", payouts)
                nraces += len(races)
            else:
                races, entries = parse_b(date, jcd, body, issues)
                for r in races:
                    con.execute(
                        """INSERT INTO races (race_id,race_date,jcd,rno,program_title,distance,deadline)
                           VALUES (?,?,?,?,?,?,?) ON CONFLICT(race_id) DO UPDATE SET
                           program_title=excluded.program_title, deadline=excluded.deadline""",
                        (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))
                con.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                entries)
                nraces += len(races)
    con.executemany("INSERT INTO parse_issues VALUES (?,?,?)", [(name, l, r) for _, l, r in issues])
    con.execute("INSERT OR REPLACE INTO loaded_files VALUES (?,?,?)", (name, kind, nraces))
    return nraces, len(issues)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="raw")
    ap.add_argument("--db", default="boatrace.sqlite")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--only", nargs="*", help="このファイル名だけ処理 (検証用)")
    a = ap.parse_args()
    if a.rebuild and os.path.exists(a.db):
        os.remove(a.db)
    con = sqlite3.connect(a.db)
    con.executescript(SCHEMA)
    done = {r[0] for r in con.execute("SELECT name FROM loaded_files")}
    # B を先に入れておくと K が同じ race_id 行に結果を上書き追加する
    for kind in ("B", "K"):
        files = sorted(glob.glob(os.path.join(a.raw, kind, "*", "*.lzh")))
        n = 0
        for p in files:
            name = os.path.basename(p)
            if a.only and name not in a.only:
                continue
            if name in done:
                continue
            try:
                load_file(con, p, kind)
            except Exception as e:  # 壊れたアーカイブ等は記録して続行
                con.execute("INSERT INTO parse_issues VALUES (?,?,?)", (name, None, f"file-error {e!r}"))
            n += 1
            if n % 200 == 0:
                con.commit()
                print(kind, name, n, flush=True)
        con.commit()
    con.close()


if __name__ == "__main__":
    main()

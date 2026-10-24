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
  series_results TEXT,             -- 今節成績 (着順の並び)
  early_race INTEGER,               -- 早見
  PRIMARY KEY (race_id, lane)
);
CREATE TABLE IF NOT EXISTS parse_issues (file TEXT, line TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS ix_races_date ON races(race_date);
CREATE INDEX IF NOT EXISTS ix_results_racer ON results(racer_no);
CREATE INDEX IF NOT EXISTS ix_entries_racer ON entries(racer_no);
CREATE INDEX IF NOT EXISTS ix_payouts_race ON payouts(race_id);
"""

BET_TYPES = ["単勝", "複勝", "２連単", "２連複", "拡連複", "３連単", "３連複", "連単", "連複"]
BET_ALIAS = {"連単": "２連単", "連複": "２連複"}  # 2001年頃までの表記


def z2h(s):
    """全角英数字・記号を半角に (カナはそのまま)。"""
    return unicodedata.normalize("NFKC", s) if s else s


def clean(s):
    return z2h(s).replace("　", " ").strip() if s is not None else None


def nospace(s):
    return re.sub(r"\s+", "", s) if s else s


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


def parse_header(lines, word):
    """場名・開催タイトル・日次をブロック冒頭から取る。"""
    nonblank = [l for l in lines if l.strip()]
    venue = title = day_no = None
    if nonblank:
        head = clean(nonblank[0])
        m = re.match(r"^(?:ボートレース)?(.+?)\s*(?:\[成績\]|\s{2,})", head)
        if m:
            venue = m.group(1).replace(" ", "")
        days = re.findall(r"第\s*(\d+)\s*日", head)  # タイトルにも「第」が入るので最後の一致
        if days:
            day_no = int(days[-1])
    for i, l in enumerate(nonblank):
        if word in l and "＊＊＊" in l and i + 1 < len(nonblank):
            title = clean(nonblank[i + 1])
            break
    return venue, title, day_no


# ---------------- 競走成績 (K) ----------------
# 古いファイルは天候が「は れ」、風向が「無 風」のように空白を含む
RE_K_RACE = re.compile(
    r"^\s*(\d{1,2})R\s+(.*?)\s+H(\d{3,4})m\s+(.+?)\s+風\s+(.+?)\s*(\d+)m\s+波\s+(\d+)cm")
RE_K_ROW = re.compile(r"^\s{1,3}(\S{1,2})\s+([1-6])\s+(\d{4})\s(.+)$")
RE_PAY = re.compile(r"(\d(?:-\d){0,2})\s+(\d+)(?:\s+人気\s+(\d+))?")
RE_DAY = re.compile(r"第\s*(\S+)\s*日")


def parse_k(date, jcd, body, issues):
    lines = body.splitlines()
    venue, title, day_no = parse_header(lines, "成績")
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
                              day_no=day_no, race_type=nospace(m.group(2)), distance=int(m.group(3)),
                              weather=nospace(m.group(4)), wind_dir=nospace(m.group(5)), wind_speed=int(m.group(6)),
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
        # 払戻 (古い形式では決まり手が連複の行末にある)
        if "決まり手" in line:
            t = re.search(r"決まり手\s+(\S+)", line)
            if t:
                races[-1]["technique"] = t.group(1)
            line = line[:line.index("決まり手")]
        s = line.strip()
        if not s:
            continue
        for b in BET_TYPES:
            if s.startswith(z2h(b)):
                bet = BET_ALIAS.get(b, b)
                s = s[len(z2h(b)):]
                break
        else:
            if bet is None:
                continue
        if "不成立" in s:
            continue
        sp = re.match(r"\s*特払い\s+(\d+)", s)
        if sp:
            payouts.append((cur, bet, "特払い", int(sp.group(1)), None))
            continue
        found = RE_PAY.findall(s)
        if found:
            for combo, pay, pop in found:
                payouts.append((cur, bet, combo, int(pay), num(pop)))
        elif bet and s and not s.startswith("返還"):
            issues.append((None, raw, f"payout-unparsed {cur}"))
    return venue, races, results, payouts


def fixed(raw, *cuts):
    """cp932 のバイト位置で切り出す (公式ファイルは全角=2バイトの固定長)。"""
    b = raw.encode("cp932", errors="replace")
    return [b[i:j].decode("cp932", errors="replace") for i, j in cuts]


def racer_name(s):
    """'山　本　　浩　輔' / '山本浩輔' → '山本 浩輔' (姓名の区切りは全角空白2つ以上)。"""
    s = s.replace("\u3000", " ").strip()
    parts = [p.replace(" ", "") for p in re.split(r" {2,}", s) if p.strip()]
    return " ".join(parts)


def text_or_none(s):
    s = z2h(s).strip()
    return s if re.search(r"\d", s) else None


def parse_k_row(rid, raw, issues):
    fin, lane, reg, name, motor, boat, exh, course, st, rtime = fixed(
        raw, (2, 4), (6, 7), (8, 12), (13, 29), (29, 32), (32, 37), (37, 43), (43, 47), (47, 55), (55, 70))
    fr = z2h(fin).strip()
    return (rid, int(lane), int(reg), racer_name(name), num(motor), num(boat), num(exh, float), num(course),
            text_or_none(st), text_or_none(rtime), fr, int(fr) if fr.isdigit() else None)


# ---------------- 番組表 (B) ----------------
RE_B_RACE = re.compile(r"^\s*(\d{1,2})R\s+(.*?)\s+H(\d{3,4})m(?:.*?(\d{1,2}:\d{2}))?")
B_COLS = [(0, 1), (2, 6), (6, 14), (14, 16), (16, 20), (20, 22), (22, 24), (24, 29), (29, 35), (35, 40),
          (40, 46), (46, 49), (49, 55), (55, 58), (58, 64), (65, 77), (77, 79)]


def parse_b(date, jcd, body, issues):
    races, entries = [], []
    cur = None
    lines = body.splitlines()
    _, title, day_no = parse_header(lines, "番組表")
    for raw in lines:
        line = z2h(raw).replace("　", " ")
        m = RE_B_RACE.match(line)
        if m:
            rno = int(m.group(1))
            cur = race_id(date, jcd, rno)
            races.append((cur, date, jcd, rno, nospace(m.group(2)), int(m.group(3)), m.group(4), title, day_no))
            continue
        if cur is None:
            continue
        if re.match(r"^[1-6] \d{4}", raw):
            try:
                g = fixed(raw, *B_COLS)
                entries.append((cur, int(g[0]), int(g[1]), racer_name(g[2]), num(g[3]), clean(g[4]), num(g[5]),
                                g[6].strip(), num(g[7], float), num(g[8], float), num(g[9], float),
                                num(g[10], float), num(g[11]), num(g[12], float), num(g[13]), num(g[14], float),
                                z2h(g[15]).rstrip(), num(g[16])))
            except ValueError:
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
                        """INSERT INTO races (race_id,race_date,jcd,rno,program_title,distance,deadline,
                           meeting_title,day_no) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(race_id) DO UPDATE SET
                           program_title=excluded.program_title, deadline=excluded.deadline,
                           meeting_title=coalesce(races.meeting_title, excluded.meeting_title),
                           day_no=coalesce(races.day_no, excluded.day_no)""", r)
                con.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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

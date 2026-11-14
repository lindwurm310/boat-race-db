"""追加の特徴量 (検証用)。

朝から使える (DB の成績・番組表だけで作れる):
  motor_*   モーターの今期 (その場のモーター入れ替えから) の成績。枠番から期待される着との差で見る
  boat_*    ボートの今期の成績 (同上)
  meet_*    選手の今節 (前日まで) の成績: 枠番補正した1着・3着内、ST の平均、展示タイムの差の平均
  venue_*   選手の当地の成績: 3着内、ST の平均 (1着の当地成績 venue_skill は今のモデルにすでにある)
  weight_d  体重 (番組表) のレース内の平均との差 (体重そのものは今のモデルにすでにある)

直前 (beforeinfo を集めたもの。締切の少し前にしか出ない):
  sx_*      スタート展示の進入コース・ST
  tilt*     チルト角
  wday_*    当日体重・調整重量
  parts_*   部品交換
"""
import numpy as np
import pandas as pd

from predict import history, history_window, parse_st


def add_meta(db, df):
    """features() の行に day_no・motor_no・boat_no・meeting を付ける。"""
    import sqlite3
    con = sqlite3.connect(db)
    lo, hi = df.race_id.min(), df.race_id.max()
    m = pd.read_sql_query("SELECT e.race_id, e.lane, e.motor_no, e.boat_no, r.day_no FROM entries e JOIN races r "
                          "USING (race_id) WHERE e.race_id >= ? AND e.race_id <= ?", con, params=(lo, hi))
    con.close()
    return df.merge(m, on=["race_id", "lane"], how="left")


def periods(df, col):
    """場ごとのモーター (ボート) の入れ替え日。番組表の2連率がほぼ全艇 0 の節の初日を入れ替えとみなす。"""
    d = df[["jcd", "race_date", "day_no", col]].copy()
    d["meet_start"] = pd.to_datetime(d["race_date"]) - pd.to_timedelta(d["day_no"].fillna(1) - 1, unit="D")
    first = d[d["day_no"] == 1].groupby(["jcd", "meet_start"])[col].agg(lambda s: (s == 0).mean())
    ren = first[first >= 0.9].reset_index()[["jcd", "meet_start"]]
    return ren


def period_id(df, col):
    ren = periods(df, col)
    t = pd.to_datetime(df["race_date"])
    out = pd.Series(0, index=df.index)
    for jcd, g in ren.groupby("jcd"):
        m = df["jcd"] == jcd
        out[m] = np.searchsorted(np.sort(g["meet_start"].values), t[m].values, side="right")
    return out


def add(df):
    """df: features() の結果に add_meta() を付けたもの。追加の列の名前のリストを返す。"""
    done = df["finish_raw"].notna()
    lane_base = df[done].groupby("lane")["win"].mean()
    t3_base = df[done].assign(t3=df["finish"] <= 3).groupby("lane")["t3"].mean()
    h = pd.DataFrame(index=df.index)
    h["n"] = done.astype(int)
    h["w"] = df["win"] * done
    h["t3"] = ((df["finish"] <= 3) & done).astype(int)
    h["ew"] = df["lane"].map(lane_base) * h["n"]
    h["e3"] = df["lane"].map(t3_base) * h["n"]
    st = df["start_timing"].map(parse_st)
    h["st_n"] = st.notna().astype(int)
    h["st_s"] = st.fillna(0)
    ex = df["exhibition_time"].where(df["exhibition_time"] > 0)
    exd = ex - ex.groupby(df["race_id"]).transform("min")
    h["ex_n"] = exd.notna().astype(int)
    h["ex_s"] = exd.fillna(0)
    h["race_date"] = df["race_date"]
    for c in ["jcd", "racer_no", "motor_no", "boat_no"]:
        h[c] = df[c]
    h["mp"] = period_id(df, "motor_2_rate")
    h["bp"] = period_id(df, "boat_2_rate")
    meet_start = (pd.to_datetime(df["race_date"]) - pd.to_timedelta(df["day_no"].fillna(1) - 1, unit="D"))
    h["meet"] = meet_start.dt.strftime("%Y%m%d")
    cols = ["n", "w", "t3", "ew", "e3", "st_n", "st_s", "ex_n", "ex_s"]
    new = []
    k = 10

    def skill(r, name, kk=k):
        df[f"{name}_w"] = (r["w"] - r["ew"]) / (r["n"] + kk)
        df[f"{name}_t3"] = (r["t3"] - r["e3"]) / (r["n"] + kk)
        df[f"{name}_n"] = r["n"]
        new.extend([f"{name}_w", f"{name}_t3", f"{name}_n"])

    st_mean = st.mean()
    # モーター・ボートの今期の成績 (乗った選手は節ごとに違う)
    r = history(h, ["jcd", "motor_no", "mp"], cols)
    skill(r, "motor")
    df["motor_ex"] = (r["ex_s"] + 5 * 0.1) / (r["ex_n"] + 5)  # 展示タイムの1位との差の平均
    new.append("motor_ex")
    r = history_window(h, ["jcd", "motor_no", "mp"], cols, 60)
    skill(r, "motor60")
    r = history(h, ["jcd", "boat_no", "bp"], cols)
    skill(r, "boat")
    # 今節 (前日まで)。同じ節のあいだは同じモーター・ボートなので、モーターの節間成績でもある
    r = history(h, ["racer_no", "jcd", "meet"], cols)
    skill(r, "meet", 3)
    df["meet_st"] = (r["st_s"] + 3 * st_mean) / (r["st_n"] + 3)
    df["meet_ex"] = (r["ex_s"] + 3 * 0.1) / (r["ex_n"] + 3)
    new += ["meet_st", "meet_ex"]
    # 当地
    r = history(h, ["racer_no", "jcd"], cols)
    df["venue_t3"] = (r["t3"] - r["e3"]) / (r["n"] + 20)
    df["venue_st"] = (r["st_s"] + 5 * st_mean) / (r["st_n"] + 5)
    new += ["venue_t3", "venue_st"]
    g = df.groupby("race_id")
    df["weight_d"] = df["weight"] - g["weight"].transform("mean")
    new.append("weight_d")
    for c in ["motor_w", "motor_t3", "motor_ex", "boat_t3", "meet_w", "meet_t3", "meet_st", "meet_ex", "venue_t3",
              "venue_st"]:
        df[c + "_d"] = df[c] - g[c].transform("mean")
        new.append(c + "_d")
    return new


# ---------------- 直前情報 (beforeinfo) ----------------
def add_late(df, bi):
    """bi: parse.py の表 (race_id, lane, ...)。追加の列の名前を dict(グループ: 列) で返す。"""
    df2 = df.merge(bi, on=["race_id", "lane"], how="left", suffixes=("", "_bi"))
    for c in df2.columns.difference(df.columns):
        df[c] = df2[c].values
    g = df.groupby("race_id")
    df["sx_course_diff"] = df["sx_course"] - df["lane"]            # 枠なりからのずれ (前づけ・後退)
    df["sx_st_d"] = df["sx_st"] - g["sx_st"].transform("mean")
    df["sx_st_rk"] = g["sx_st"].rank(method="min")
    df["sx_st_vs_avg"] = df["sx_st"] - df["st_avg"]                # いつもの ST との差
    srt = df.sort_values(["race_id", "sx_course"])
    df["sx_st_vs_inner"] = (srt["sx_st"] - srt.groupby("race_id")["sx_st"].shift(1)).reindex(df.index)
    df["sx_in_st"] = df["sx_st"].where(df["sx_course"] == 1).groupby(df["race_id"]).transform("max")
    df["sx_moved"] = g["sx_course_diff"].transform(lambda s: (s.abs() > 0).sum())
    df["tilt_d"] = df["tilt"] - g["tilt"].transform("mean")
    df["wday_diff"] = df["wday"] - df["weight"]                     # 番組表の体重からの増減
    df["wday_d"] = df["wday"] - g["wday"].transform("mean")
    return {"sx": ["sx_course", "sx_course_diff", "sx_st", "sx_st_d", "sx_st_rk", "sx_st_vs_avg", "sx_st_vs_inner",
                   "sx_in_st", "sx_moved"],
            "tilt": ["tilt", "tilt_d"],
            "wday": ["wday", "wday_diff", "wday_d", "adj_weight"],
            "parts": ["parts_n", "parts_ring", "parts_carb", "parts_piston", "parts_cyl", "parts_elec", "parts_gear",
                      "prop_new"]}

"""DB からレース結果を予測し、券種ごとの買い方を的中率・回収率で比べる。

  使い方:
    python3 analysis/predict.py --db boatrace.sqlite \
        --warmup 2026-01-01 --train 2026-01-15 --test 2026-07-01 --end 2026-12-31 --out report_2026

  - 学習期間 [train, test) で学習し、テスト期間 [test, end] は一切見ずに評価する (時系列で分割)
  - warmup 以降のデータは選手のコース別成績を貯めるためだけに使う
  - 1 点 100 円で買ったとして、払戻 (payouts) から回収率を計算。欠場・F・L の艇を含む券は返還 (100 円戻る)
  - 「前日」モデルは番組表の情報だけ、「直前」モデルは展示タイムと風・波も使う
"""
import argparse
import itertools
import json
import os
import sqlite3

import lightgbm as lgb
import numpy as np
import pandas as pd

CLASS = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
BETS = ["単勝", "複勝", "２連単", "２連複", "拡連複", "３連単", "３連複"]


def load(db, start, end):
    con = sqlite3.connect(db)
    lo, hi = start.replace("-", ""), end.replace("-", "") + "~"
    q = """
    SELECT e.race_id, r.race_date, r.jcd, r.rno, r.race_type, r.wind_speed, r.wave_cm,
           e.lane, e.racer_no, e.age, e.weight, e.class, e.nat_win_rate, e.nat_2_rate,
           e.loc_win_rate, e.loc_2_rate, e.motor_2_rate, e.boat_2_rate, e.series_results,
           r.wind_dir, x.exhibition_time, x.course, x.start_timing, x.finish_raw, x.finish
    FROM entries e JOIN races r USING (race_id)
    LEFT JOIN results x ON x.race_id = e.race_id AND x.lane = e.lane
    WHERE e.race_id >= ? AND e.race_id < ?"""
    df = pd.read_sql_query(q, con, params=(lo, hi))
    pay = pd.read_sql_query("SELECT * FROM payouts WHERE race_id >= ? AND race_id < ?", con, params=(lo, hi))
    con.close()
    return df, pay


def series_stats(s):
    d = [int(c) for c in (s or "") if c in "123456"]
    return (np.mean(d) if d else np.nan, len(d), sum(c in "FL" for c in (s or "")))


def parse_st(s):
    """'0.15' → 0.15, 'F0.02' → -0.02 (フライング), 'L…' や欠場は NaN。"""
    if not isinstance(s, str) or not s:
        return np.nan
    if s[0] == "F":
        return -float(s[1:]) if s[1:] else np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def history(df, keys, cols, query_dates=None):
    """keys ごとに cols の合計を日付順に積み上げ、各行に「その日より前」の累計を付ける。
    query_dates を渡すと、各行の日付の代わりにその日付より前の累計を返す。"""
    d = df.groupby(keys + ["race_date"])[cols].sum().reset_index().sort_values("race_date")
    d[cols] = d.groupby(keys)[cols].cumsum()
    d["_t"] = pd.to_datetime(d["race_date"])
    left = df[keys + ["race_date"]].copy()
    left["_t"] = pd.to_datetime(left["race_date"] if query_dates is None else query_dates)
    left["_i"] = np.arange(len(left))
    m = pd.merge_asof(left.sort_values("_t"), d.drop(columns="race_date").sort_values("_t"), on="_t", by=keys,
                      allow_exact_matches=False)
    return m.sort_values("_i")[cols].fillna(0).set_index(df.index)


def features(df, require_result=True):
    """require_result=False なら結果が無いレース (これから行われるレース) も残す。"""
    df = df.copy()
    n = df.groupby("race_id")["lane"].transform("count")
    keep = n == 6
    if require_result:  # 6艇そろい、結果が付いているレースだけ使う
        keep &= df.groupby("race_id")["finish_raw"].transform(lambda s: s.notna().all())
    df = df[keep].copy()
    df["cls"] = df["class"].map(CLASS)
    st = df["series_results"].map(series_stats)
    df["series_avg"] = [a for a, _, _ in st]
    df["series_n"] = [b for _, b, _ in st]
    df["series_fl"] = [c for _, _, c in st]
    df["refund"] = df["finish_raw"].fillna("").str[0].isin(["K", "F", "L"])
    df["win"] = (df["finish"] == 1).astype(int)
    df["final"] = df["race_type"].fillna("").str.contains("優勝").astype(int)

    # ---- 過去の結果から作る選手の特徴 (その日より前のレースだけを使う) ----
    done = df["finish_raw"].notna()
    st_v = df["start_timing"].map(parse_st)
    h = pd.DataFrame(index=df.index)
    h["n"] = done.astype(int)
    h["w"] = df["win"] * done
    h["t2"] = (df["finish"] <= 2).astype(int) * done
    h["t3"] = (df["finish"] <= 3).astype(int) * done
    h["st_n"] = st_v.notna().astype(int)
    h["st_s"] = st_v.fillna(0)
    h["st_s2"] = (st_v ** 2).fillna(0)
    h["f"] = df["finish_raw"].fillna("").str.startswith("F").astype(int)
    h["cc"] = ((df["course"] != df["lane"]) & df["course"].notna()).astype(int)
    h["out_n"] = h["n"] * (df["lane"] > 1)
    h["out_w"] = h["w"] * (df["lane"] > 1)
    h[["racer_no", "lane", "race_date"]] = df[["racer_no", "lane", "race_date"]]
    lane_base = df[done].groupby("lane")[["win"]].mean()["win"]
    t2_base = df[done].assign(t2=df["finish"] <= 2).groupby("lane")["t2"].mean()
    t3_base = df[done].assign(t3=df["finish"] <= 3).groupby("lane")["t3"].mean()

    hl = history(h, ["racer_no", "lane"], ["n", "w", "t2", "t3"])
    k = 10  # 平滑化: 実績が少ない選手は枠番の平均に寄せる
    df["racer_lane_n"] = hl["n"]
    df["racer_lane_win"] = (hl["w"] + k * df["lane"].map(lane_base)) / (hl["n"] + k)
    df["racer_lane_t2"] = (hl["t2"] + k * df["lane"].map(t2_base)) / (hl["n"] + k)
    df["racer_lane_t3"] = (hl["t3"] + k * df["lane"].map(t3_base)) / (hl["n"] + k)
    hr = history(h, ["racer_no"], ["n", "st_n", "st_s", "st_s2", "f", "cc", "out_n", "out_w"])
    st_mean = (st_v.mean() if st_v.notna().any() else 0.16)
    df["st_avg"] = (hr["st_s"] + 5 * st_mean) / (hr["st_n"] + 5)
    df["st_sd"] = np.sqrt(np.maximum((hr["st_s2"] + 5 * 0.0025) / (hr["st_n"] + 5) - df["st_avg"] ** 2, 0))
    df["f_rate"] = (hr["f"] + 0.2) / (hr["n"] + 20)
    df["course_change"] = (hr["cc"] + 1) / (hr["n"] + 20)
    df["out_win"] = (hr["out_w"] + 10 * 0.12) / (hr["out_n"] + 10)
    df["racer_n"] = hr["n"]

    g = df.groupby("race_id")
    for c in ["nat_win_rate", "loc_win_rate", "motor_2_rate", "boat_2_rate", "cls", "nat_2_rate", "st_avg",
              "racer_lane_win", "out_win"]:
        df[c + "_d"] = df[c] - g[c].transform("mean")
        df[c + "_rk"] = g[c].rank(ascending=c == "st_avg", method="min")
    df["ex_d"] = df["exhibition_time"] - g["exhibition_time"].transform("min")
    df["ex_rk"] = g["exhibition_time"].rank(method="min")

    # ---- レース内の並び (イン艇と攻める艇の関係) ----
    def at_lane(c, l):
        return df[c].where(df["lane"] == l).groupby(df["race_id"]).transform("max")
    df["vs_in"] = df["nat_win_rate"] - at_lane("nat_win_rate", 1)
    df["in_st"] = at_lane("st_avg", 1)
    df["in_lane_win"] = at_lane("racer_lane_win", 1)
    df["atk_st"] = df["st_avg"].where(df["lane"].between(2, 4)).groupby(df["race_id"]).transform("min")
    df["atk_out_win"] = df["out_win"].where(df["lane"].between(2, 4)).groupby(df["race_id"]).transform("max")
    srt = df.sort_values(["race_id", "lane"])
    inner = srt.groupby("race_id")["st_avg"].shift(1)
    df["st_vs_inner"] = (srt["st_avg"] - inner).reindex(df.index)
    df["st_vs_in"] = df["st_avg"] - df["in_st"]
    df["n_a1"] = g["cls"].transform(lambda s: (s == 4).sum())
    df["wind_dir_c"] = df["wind_dir"].astype("category").cat.codes
    racer_features(df, h)
    return df


def history_window(df, keys, cols, days):
    """history() の直近 days 日だけ版 (その日より前 days 日間の合計)。"""
    now = history(df, keys, cols)
    past = history(df, keys, cols, query_dates=pd.to_datetime(df["race_date"]) - pd.Timedelta(days=days))
    return now - past.values


def racer_features(df, h):
    """v4: 選手ごとの重み (枠の有利不利を除いた強さ・最近の調子・場との相性) と並び (直接対決・隣の艇)。"""
    lane_base = df[df["finish_raw"].notna()].groupby("lane")["win"].mean()
    t3_base = df[df["finish_raw"].notna()].assign(t3=df["finish"] <= 3).groupby("lane")["t3"].mean()
    h = h.copy()
    h["jcd"] = df["jcd"]
    # 枠番から期待される 1着・3着内 との差 (外枠で勝つほど高く評価される)
    h["ew"] = (df["lane"].map(lane_base) * h["n"]).values
    h["e3"] = (df["lane"].map(t3_base) * h["n"]).values
    cols = ["n", "w", "t3", "ew", "e3"]
    k = 20
    for name, days in [("all", None), ("365", 365), ("90", 90)]:
        r = history(h, ["racer_no"], cols) if days is None else history_window(h, ["racer_no"], cols, days)
        df[f"skill_w_{name}"] = (r["w"] - r["ew"]) / (r["n"] + k)
        df[f"skill_t3_{name}"] = (r["t3"] - r["e3"]) / (r["n"] + k)
        df[f"n_{name}"] = r["n"]
    r = history_window(h, ["racer_no", "lane"], ["n", "w"], 365)
    df["racer_lane_win_365"] = (r["w"] + 10 * df["lane"].map(lane_base)) / (r["n"] + 10)
    r = history(h, ["racer_no", "jcd"], cols)
    df["venue_skill"] = (r["w"] - r["ew"]) / (r["n"] + k)
    df["venue_n"] = r["n"]
    h2h(df)
    g = df.groupby("race_id")
    for c in ["skill_w_all", "skill_w_365", "skill_w_90", "skill_t3_365", "venue_skill", "h2h"]:
        df[c + "_d"] = df[c] - g[c].transform("mean")
        df[c + "_rk"] = g[c].rank(ascending=False, method="min")
    # 並び: 内側・外側の艇の強さ
    srt = df.sort_values(["race_id", "lane"])
    for c, name in [("racer_lane_win", "lane_win"), ("skill_w_365", "skill"), ("out_win", "out_win")]:
        df[f"inner_{name}"] = srt.groupby("race_id")[c].shift(1).reindex(df.index)
        df[f"outer_{name}"] = srt.groupby("race_id")[c].shift(-1).reindex(df.index)


def h2h(df):
    """今日の相手との過去の直接対決 (同じレースでどちらが先にゴールしたか) の勝率を平均する。"""
    srt = df.sort_values(["race_date", "race_id", "lane"])
    racer = srt["racer_no"].values.reshape(-1, 6)
    fin = srt["finish"].fillna(7).values.reshape(-1, 6)
    done = srt["finish_raw"].notna().values.reshape(-1, 6).all(1)
    absent = srt["finish_raw"].fillna("").str.startswith("K").values.reshape(-1, 6)
    dates = srt["race_date"].values.reshape(-1, 6)[:, 0]
    met, ahead = {}, {}
    score = np.zeros((len(racer), 6)); count = np.zeros((len(racer), 6))
    pairs = [(i, j) for i in range(6) for j in range(6) if i != j]
    start = 0
    while start < len(racer):
        end = start
        while end < len(racer) and dates[end] == dates[start]:
            end += 1
        for r in range(start, end):           # その日のレースは前日までの成績で計算
            for i, j in pairs:
                key = (racer[r, i], racer[r, j])
                m = met.get(key, 0)
                score[r, i] += (ahead.get(key, 0) + 1) / (m + 2)
                count[r, i] += m
        for r in range(start, end):           # その日の結果を足す
            if not done[r]:
                continue
            for i, j in pairs:
                if absent[r, i] or absent[r, j]:
                    continue
                key = (racer[r, i], racer[r, j])
                met[key] = met.get(key, 0) + 1
                if fin[r, i] < fin[r, j]:
                    ahead[key] = ahead.get(key, 0) + 1
        start = end
    df["h2h"] = pd.Series((score / 5).ravel(), index=srt.index).reindex(df.index)
    df["h2h_n"] = pd.Series(count.ravel(), index=srt.index).reindex(df.index)


V1_FEATS = ["lane", "jcd", "rno", "age", "weight", "cls", "nat_win_rate", "nat_2_rate", "loc_win_rate",
            "loc_2_rate", "motor_2_rate", "boat_2_rate", "series_avg", "series_n", "series_fl", "vs_in", "final",
            "racer_lane_win", "racer_lane_n"] + [c + s for c in ["nat_win_rate", "loc_win_rate", "motor_2_rate",
                                                                 "boat_2_rate", "cls", "nat_2_rate"]
                                                 for s in ("_d", "_rk")]
# v2: 外れの分析 (イン艇がスタートで負けてまくられる/差される) を受けて、スタート力と並びの特徴を追加
BASE_FEATS = V1_FEATS + ["racer_lane_t2", "racer_lane_t3", "st_avg", "st_sd", "f_rate", "course_change", "out_win",
                         "racer_n", "in_st", "in_lane_win", "atk_st", "atk_out_win", "st_vs_inner", "st_vs_in", "n_a1"
                         ] + [c + s for c in ["st_avg", "racer_lane_win", "out_win"] for s in ("_d", "_rk")]
LATE = ["exhibition_time", "ex_d", "ex_rk", "wind_speed", "wave_cm", "wind_dir_c"]
LATE_FEATS = BASE_FEATS + LATE
V4_EXTRA = ([f"{a}_{b}" for a in ("skill_w", "skill_t3", "n") for b in ("all", "365", "90")] +
            ["racer_lane_win_365", "venue_skill", "venue_n", "h2h", "h2h_n"] +
            [c + s for c in ["skill_w_all", "skill_w_365", "skill_w_90", "skill_t3_365", "venue_skill", "h2h"]
             for s in ("_d", "_rk")] +
            [f"{a}_{b}" for a in ("inner", "outer") for b in ("lane_win", "skill", "out_win")])
V4_FEATS = BASE_FEATS + V4_EXTRA
# (名前, 特徴量, 学習方法)。"v3" = 1着は softmax、2・3着は Plackett-Luce の2モデルを組み合わせる
MODELS = {"v1前日": (V1_FEATS, "binary"), "v1直前": (V1_FEATS + LATE[:-1], "binary"),
          "v2前日": (BASE_FEATS, "binary"), "v2直前": (LATE_FEATS, "binary"),
          "v3前日": (BASE_FEATS, "v3"), "v3直前": (LATE_FEATS, "v3")}


def fit(train, val, feats, objective="pl", stages=1):
    """objective="binary": 艇ごとに1着かどうか (v1/v2)。
    objective="pl": レース内の順位を Plackett-Luce で学習。stages=1 ならレース内 softmax で1着だけ、
    stages=3 なら 2・3着の情報も使う。"""
    params = dict(learning_rate=0.03, num_leaves=31, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1)
    if objective == "binary":
        params["objective"] = "binary"
        dt = lgb.Dataset(train[feats], train["win"], categorical_feature=cats(feats))
        dv = lgb.Dataset(val[feats], val["win"], categorical_feature=cats(feats), reference=dt)
        m = lgb.train(params, dt, 3000, valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])
        m.softmax = False
        return m
    train, val = race_sorted(train), race_sorted(val)
    ft, fv = train["finish"].values.reshape(-1, 6), val["finish"].values.reshape(-1, 6)
    params["objective"] = lambda preds, ds: pl_grad(preds, ft, stages)
    dt = lgb.Dataset(train[feats], train["win"], categorical_feature=cats(feats), free_raw_data=False)
    dv = lgb.Dataset(val[feats], val["win"], categorical_feature=cats(feats), reference=dt)
    feval = lambda preds, ds: ("win_logloss", win_logloss(preds, fv), False)
    m = lgb.train(params, dt, 3000, valid_sets=[dv], feval=feval,
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    m.softmax = True
    return m


def cats(feats):
    return [c for c in ("jcd", "racer_no") if c in feats]


def race_sorted(df):
    return df.sort_values(["race_id", "lane"])


def pl_grad(preds, finish, stages=3):
    """Plackett-Luce (上位 stages 着まで) の負の対数尤度の勾配とヘッセ行列の対角。"""
    s = preds.reshape(-1, 6)
    g = np.zeros_like(s); h = np.zeros_like(s)
    for k in range(1, stages + 1):
        remain = ~(finish < k)            # まだ着が決まっていない艇 (着外・失格の艇も残す)
        target = finish == k
        ok = target.any(1)
        e = np.where(remain, np.exp(s - s.max(1, keepdims=True)), 0)
        p = e / e.sum(1, keepdims=True)
        g[ok] += (p - target)[ok]
        h[ok] += (p * (1 - p))[ok]
    return g.ravel(), np.maximum(h.ravel(), 1e-6)


def softmax6(raw):
    s = raw.reshape(-1, 6)
    e = np.exp(s - s.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


def win_logloss(preds, finish):
    p = softmax6(preds); y = finish == 1; ok = y.any(1)
    return float(-np.log(np.clip(p[ok][y[ok]], 1e-12, 1)).mean())


def race_probs(df, model, feats):
    """各レース 6 艇の 1 着確率 (レース内で合計 1 に正規化)。行は race_id, 列は 1〜6 号艇。"""
    df = race_sorted(df)
    raw = model.predict(df[feats], num_iteration=model.best_iteration)
    if getattr(model, "softmax", False):
        p = softmax6(raw).ravel()
    else:
        p = raw
    w = df.assign(p=p).pivot(index="race_id", columns="lane", values="p")[[1, 2, 3, 4, 5, 6]]
    return w.div(w.sum(axis=1), axis=0)


class Predictor:
    """1着確率のモデルと (v3 では) 2・3着用のモデル、λ をまとめたもの。保存・読み込みもできる。"""

    def __init__(self, feats, kind):
        self.feats, self.kind = feats, kind
        self.win = self.order = None
        self.lam2 = self.lam3 = 1.0

    def fit(self, tr, va):
        if self.kind == "binary":
            self.win = fit(tr, va, self.feats, "binary")
        else:
            self.win = fit(tr, va, self.feats, "pl", stages=1)
            self.order = fit(tr, va, self.feats, "pl", stages=3)
        P, Q = self.probs(va)
        act = finish_order(va, P.index)
        ok = act.notna().all(axis=1).values
        _, self.lam2, self.lam3 = tune_lambda(P.values[ok], (act.values[ok] - 1).astype(int),
                                              None if Q is None else Q.values[ok])
        return self

    def probs(self, df):
        P = race_probs(df, self.win, self.feats)
        Q = race_probs(df, self.order, self.feats).loc[P.index] if self.order is not None else None
        return P, Q

    def orders(self, P, Q):
        return order_probs(P.values, self.lam2, self.lam3, None if Q is None else Q.values)

    def save(self, d):
        os.makedirs(d, exist_ok=True)
        self.win.save_model(os.path.join(d, "win.txt"), num_iteration=self.win.best_iteration)
        if self.order is not None:
            self.order.save_model(os.path.join(d, "order.txt"), num_iteration=self.order.best_iteration)
        meta = dict(feats=self.feats, kind=self.kind, lam2=float(self.lam2), lam3=float(self.lam3))
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, d):
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        p = cls(meta["feats"], meta["kind"])
        p.lam2, p.lam3 = meta["lam2"], meta["lam3"]
        p.win = lgb.Booster(model_file=os.path.join(d, "win.txt"))
        p.win.softmax = p.kind != "binary"
        p.win.best_iteration = 0
        if os.path.exists(os.path.join(d, "order.txt")):
            p.order = lgb.Booster(model_file=os.path.join(d, "order.txt"))
            p.order.softmax, p.order.best_iteration = True, 0
        return p


def finish_order(df, index):
    """各レースの 1〜3 着の艇番 (行 = race_id, 列 = 1,2,3)。"""
    return df[df.finish.between(1, 3)].pivot_table(index="race_id", columns="finish", values="lane",
                                                   aggfunc="first").reindex(index)[[1, 2, 3]]


PERMS3 = list(itertools.permutations(range(6), 3))
PERMS2 = list(itertools.permutations(range(6), 2))


def order_probs(P, lam2, lam3, Q=None):
    """Harville 型で 2連単・3連単の全組み合わせの確率を出す。
    1着は P、2・3着は Q (無ければ P) を p^λ で補正した強さで決まるとする。"""
    p1 = P
    Q = P if Q is None else Q
    p2 = Q ** lam2; p2 = p2 / p2.sum(1, keepdims=True)
    p3 = Q ** lam3; p3 = p3 / p3.sum(1, keepdims=True)
    tri = np.empty((len(P), len(PERMS3)))
    for k, (i, j, l) in enumerate(PERMS3):
        tri[:, k] = p1[:, i] * p2[:, j] / (1 - p2[:, i]) * p3[:, l] / (1 - p3[:, i] - p3[:, j])
    ex = np.empty((len(P), len(PERMS2)))
    for k, (i, j) in enumerate(PERMS2):
        ex[:, k] = p1[:, i] * p2[:, j] / (1 - p2[:, i])
    return ex, tri


def tune_lambda(P, actual, Q=None):
    """検証期間の 3連単の対数尤度が最大になる λ を探す。actual: (n,3) の 0 始まり着順の艇。"""
    idx = {t: k for k, t in enumerate(PERMS3)}
    col = np.array([idx.get(tuple(a), -1) for a in actual])
    ok = col >= 0
    best = None
    for l2 in np.arange(0.5, 1.41, 0.1):
        for l3 in np.arange(0.3, 1.41, 0.1):
            _, tri = order_probs(P[ok], l2, l3, None if Q is None else Q[ok])
            ll = np.log(tri[np.arange(ok.sum()), col[ok]]).mean()
            if best is None or ll > best[0]:
                best = (ll, round(l2, 2), round(l3, 2))
    return best


def tickets_from_probs(P, ex, tri):
    """各券種の全買い目と確率を作る (確率の高い順に並べる)。"""
    out = {}
    lanes = np.arange(1, 7)
    out["単勝"] = ([str(l) for l in lanes], P)
    # 複勝 (2着以内): P(i が 1 着) + P(i が 2 着)
    top2 = np.zeros_like(P)
    for k, (i, j) in enumerate(PERMS2):
        top2[:, i] += ex[:, k]; top2[:, j] += ex[:, k]
    out["複勝"] = ([str(l) for l in lanes], top2)
    out["２連単"] = ([f"{i+1}-{j+1}" for i, j in PERMS2], ex)
    pairs = list(itertools.combinations(range(6), 2))
    q = np.stack([ex[:, PERMS2.index((i, j))] + ex[:, PERMS2.index((j, i))] for i, j in pairs], 1)
    out["２連複"] = ([f"{i+1}-{j+1}" for i, j in pairs], q)
    # 拡連複 (2艇とも 3 着以内)
    w = np.zeros((len(P), len(pairs)))
    for k, t in enumerate(PERMS3):
        for a, b in itertools.combinations(sorted(t), 2):
            w[:, pairs.index((a, b))] += tri[:, k]
    out["拡連複"] = ([f"{i+1}-{j+1}" for i, j in pairs], w)
    out["３連単"] = ([f"{i+1}-{j+1}-{k+1}" for i, j, k in PERMS3], tri)
    trios = list(itertools.combinations(range(6), 3))
    t3 = np.zeros((len(P), len(trios)))
    for k, t in enumerate(PERMS3):
        t3[:, trios.index(tuple(sorted(t)))] += tri[:, k]
    out["３連複"] = ([f"{i+1}-{j+1}-{k+1}" for i, j, k in trios], t3)
    return out


class Book:
    """払戻表と返還判定。"""

    def __init__(self, pay, df):
        self.pay = {}
        for r in pay.itertuples(index=False):
            self.pay.setdefault((r.race_id, r.bet_type), {})[r.combination] = r.payout
        self.refund = df[df["refund"]].groupby("race_id")["lane"].apply(lambda s: set(map(str, s))).to_dict()

    def ret(self, rid, bet, combo):
        """100 円の券の払戻額。"""
        ref = self.refund.get(rid)
        if ref and any(b in ref for b in combo.split("-")):
            return 100
        d = self.pay.get((rid, bet))
        if not d:
            return 0
        if combo in d:
            return d[combo]
        return d.get("特払い", 0)


def simulate(book, rids, tk, strategies):
    """strategies: [(名前, 券種, 選び方関数(確率行)->買い目index のリスト, 対象レースのマスク)]"""
    rows = []
    for name, bet, pick, mask in strategies:
        combos, prob = tk[bet]
        stake = ret = hits = races = 0
        per_race = []
        for n in np.where(mask)[0]:
            rid = rids[n]
            if (rid, bet) not in book.pay:
                continue  # レース不成立など
            sel = pick(prob[n])
            if not len(sel):
                continue
            r = sum(book.ret(rid, bet, combos[k]) for k in sel)
            win = any(combos[k] in book.pay[(rid, bet)] for k in sel)
            stake += 100 * len(sel); ret += r; hits += win; races += 1
            per_race.append((100 * len(sel), r))
        if not races:
            continue
        pr = np.array(per_race)
        rng = np.random.default_rng(0)
        bs = [pr[i, 1].sum() / pr[i, 0].sum() for i in (rng.integers(0, len(pr), len(pr)) for _ in range(300))]
        rows.append(dict(strategy=name, bet=bet, races=races, tickets_per_race=round(stake / 100 / races, 2),
                         hit_rate=hits / races, return_rate=ret / stake,
                         rr_lo=np.percentile(bs, 2.5), rr_hi=np.percentile(bs, 97.5), profit=ret - stake))
    return pd.DataFrame(rows)


def topk(k):
    return lambda p: np.argsort(-p)[:k]


def fixed(idx):
    return lambda p: idx


def build_strategies(tk, P):
    n = len(P)
    allm = np.ones(n, bool)
    top = P.max(1)
    S = []
    S.append(("1号艇の単勝 (基準)", "単勝", fixed([0]), allm))
    S.append(("1号艇の複勝 (基準)", "複勝", fixed([0]), allm))
    S.append(("3連単 1-2-3 固定 (基準)", "３連単", fixed([tk["３連単"][0].index("1-2-3")]), allm))
    S.append(("3連単 全120点 (基準)", "３連単", lambda p: np.arange(120), allm))
    for bet, ks in [("単勝", [1, 2]), ("複勝", [1, 2]), ("２連単", [1, 2, 3, 5, 8]), ("２連複", [1, 2, 3, 5]),
                    ("拡連複", [1, 2, 3, 5]), ("３連単", [1, 3, 5, 10, 20, 30]), ("３連複", [1, 3, 5, 10])]:
        for k in ks:
            S.append((f"{bet} 予測上位{k}点", bet, topk(k), allm))
    # 本命が堅いレースだけ買う
    for th in [0.5, 0.6, 0.7, 0.8]:
        m = top >= th
        for bet, k in [("単勝", 1), ("複勝", 1), ("２連単", 1), ("２連単", 3), ("３連単", 1), ("３連単", 5),
                       ("３連複", 1), ("拡連複", 1)]:
            S.append((f"本命{int(th*100)}%以上のレースだけ {bet} 上位{k}点", bet, topk(k), m))
    # 荒れそうなレース (本命が弱い) だけ
    m = top < 0.35
    for bet, k in [("単勝", 1), ("２連単", 5), ("３連単", 10), ("３連複", 5)]:
        S.append((f"本命35%未満のレースだけ {bet} 上位{k}点", bet, topk(k), m))
    return S


def calibration(P, df):
    win = df.pivot(index="race_id", columns="lane", values="win").loc[P.index][[1, 2, 3, 4, 5, 6]].values
    p = P.values.ravel(); y = win.ravel()
    bins = np.array([0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.01])
    b = np.digitize(p, bins) - 1
    return pd.DataFrame([dict(bin=f"{bins[i]:.2f}-{min(bins[i+1],1):.2f}", n=int((b == i).sum()),
                              pred=p[b == i].mean(), actual=y[b == i].mean()) for i in range(len(bins) - 1)
                         if (b == i).sum()])


def metrics(P, tri, te):
    """テスト期間の当たり具合。"""
    act = finish_order(te, P.index)
    ok = act.notna().all(axis=1).values
    a = (act.values[ok] - 1).astype(int)
    p = P.values[ok]
    idx = {t: k for k, t in enumerate(PERMS3)}
    col = np.array([idx[tuple(x)] for x in a])
    t = tri[ok]
    best = np.array(PERMS3)[t.argmax(1)]
    n = np.arange(len(a))
    return dict(races=int(ok.sum()),
                win_top1=float((p.argmax(1) == a[:, 0]).mean()),       # 本命が1着
                lane1_wins=float((a[:, 0] == 0).mean()),                 # 参考: 1号艇の1着率
                win_logloss=float(-np.log(p[n, a[:, 0]]).mean()), uniform_logloss=float(np.log(6)),
                pos2_hit=float((best[:, 1] == a[:, 1]).mean()),          # 予想の2着が当たり
                pos3_hit=float((best[:, 2] == a[:, 2]).mean()),
                tri_top1=float((t.argmax(1) == col).mean()),             # 3連単1点目が的中
                tri_logloss=float(-np.log(t[n, col]).mean()), tri_uniform_logloss=float(np.log(120)))


def importance(model, feats):
    imp = pd.Series(model.feature_importance("gain"), index=feats).sort_values(ascending=False)
    return (imp / imp.sum()).round(4).head(15).to_dict()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--warmup", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--val-days", type=int, default=30, help="学習期間の最後の何日を早期打ち切り・λ調整用にするか")
    ap.add_argument("--out", required=True)
    ap.add_argument("--save-model-for", default="v3前日", help="このモデルを out/model に保存する")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    raw, pay = load(a.db, a.warmup, a.end)
    df = features(raw)
    val_start = (pd.Timestamp(a.test) - pd.Timedelta(days=a.val_days)).strftime("%Y-%m-%d")
    tr = df[(df.race_date >= a.train) & (df.race_date < val_start)]
    va = df[(df.race_date >= val_start) & (df.race_date < a.test)]
    te = df[(df.race_date >= a.test) & (df.race_date <= a.end)]
    print("races train/val/test", tr.race_id.nunique(), va.race_id.nunique(), te.race_id.nunique())

    summary = {"periods": dict(train=[a.train, val_start], val=[val_start, a.test], test=[a.test, a.end],
                               races=dict(train=int(tr.race_id.nunique()), val=int(va.race_id.nunique()),
                                          test=int(te.race_id.nunique())))}
    allres = []
    for label, (feats, kind) in MODELS.items():
        pr = Predictor(feats, kind).fit(tr, va)
        if label == a.save_model_for:
            pr.save(os.path.join(a.out, "model"))
        P, Q = pr.probs(te)
        ex, tri = pr.orders(P, Q)
        tk = tickets_from_probs(P.values, ex, tri)
        book = Book(pay[pay.race_id.isin(P.index)], te)
        res = simulate(book, list(P.index), tk, build_strategies(tk, P.values))
        res.insert(0, "model", label)
        allres.append(res)
        summary[label] = metrics(P, tri, te) | dict(lam2=pr.lam2, lam3=pr.lam3, iters=pr.win.best_iteration,
                                                     importance=importance(pr.win, feats))
        calibration(P, te).to_csv(f"{a.out}/calibration_{label}.csv", index=False)
        print(label, {k: v for k, v in summary[label].items() if k != "importance"}, flush=True)
    out = pd.concat(allres)
    out.to_csv(f"{a.out}/strategies.csv", index=False)
    with open(f"{a.out}/summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=float)
    pd.set_option("display.width", 250, "display.max_rows", 500, "display.max_colwidth", 40)
    print(out.sort_values("return_rate", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()

"""追加の情報と着順の相関。

  python analysis/extra/corr.py --cache extra/feats_....pkl --pred extra --out extra/corr.csv [--bi beforeinfo.csv.gz]

各特徴量について (予想した期間のレースだけ):
  rho_finish  着順とのスピアマン相関 (負 = 値が大きいほど上の着)。レース内の平均を引いた値で計算
  rho_win     1着かどうかとの相関 (同上)
  rho_resid   今の予想 (「なし」) の 1着確率を引いた残り (1着なら 1−p、それ以外は −p) との相関。
              今の予想がもう知っていることを除いても、1着と関係があるか
  win_q1 / win_q5  レース内の値で5つに分けたときの、一番小さい組と一番大きい組の1着率
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import feats  # noqa: E402


def rank_corr(x, y):
    ok = x.notna() & y.notna()
    if ok.sum() < 1000:
        return np.nan
    return float(np.corrcoef(x[ok].rank(), y[ok].rank())[0, 1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--pred", required=True, help="extra_bt.py の out (<年>_なし_pred.csv を読む)")
    ap.add_argument("--bi")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="なし")
    a = ap.parse_args()
    df = pd.read_pickle(a.cache)
    new = [c for c in df.columns if c.split("_")[0] in ("motor", "motor60", "boat", "meet", "venue")
           and c not in ("motor_2_rate", "boat_2_rate", "venue_skill")] + ["weight_d"]
    groups = {"モーター": [c for c in new if c.startswith("motor")], "ボート": [c for c in new if c.startswith("boat")],
              "今節": [c for c in new if c.startswith("meet")], "当地": [c for c in new if c.startswith("venue")],
              "体重": ["weight", "weight_d"],
              "(参考) 今の予想にあるもの": ["series_avg", "loc_win_rate", "venue_skill", "motor_2_rate", "boat_2_rate",
                                       "exhibition_time", "ex_d"]}
    if a.bi:
        late = feats.add_late(df, pd.read_csv(a.bi))
        groups |= {"スタート展示": late["sx"], "チルト": late["tilt"], "当日体重": late["wday"], "部品交換": late["parts"]}
    pr = pd.concat(pd.read_csv(f, encoding="utf-8-sig") for f in glob.glob(os.path.join(a.pred, f"*_{a.base}_pred.csv")))
    pl = pr.melt(id_vars="race_id", value_vars=[f"{l}号艇" for l in range(1, 7)], var_name="lane", value_name="p")
    pl["lane"] = pl["lane"].str[0].astype(int)
    d = df[df.race_id.isin(pr.race_id)].merge(pl, on=["race_id", "lane"], how="left")
    if a.bi:
        d = d[d.groupby("race_id")["sx_course"].transform(lambda s: s.notna().all())]
    d["resid"] = d["win"] - d["p"]
    fin = d["finish"].astype(float)
    g = d.groupby("race_id")
    rows = []
    for grp, cols in groups.items():
        for c in cols:
            if c not in d:
                continue
            x = d[c].astype(float)
            xd = x - g[c].transform("mean") if not c.endswith("_d") else x
            q = xd.groupby(d["race_id"]).rank(pct=True)
            bins = pd.cut(q, [0, .2, .4, .6, .8, 1.0], labels=False, include_lowest=True)
            wr = d["win"].groupby(bins).mean()
            rows.append(dict(group=grp, feature=c, n=int(x.notna().sum()), mean=x.mean(), sd=x.std(),
                             rho_finish=rank_corr(xd, fin), rho_win=rank_corr(xd, d["win"].astype(float)),
                             rho_resid=rank_corr(xd, d["resid"]),
                             win_q1=wr.get(0, np.nan), win_q5=wr.get(4, np.nan)))
    out = pd.DataFrame(rows)
    out.to_csv(a.out, index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 200, "display.max_rows", 200, "display.unicode.east_asian_width", True)
    print(f"{d.race_id.nunique()} レース")
    print(out.to_string(index=False, float_format=lambda v: f"{v:.3f}"))


if __name__ == "__main__":
    main()

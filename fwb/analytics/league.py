"""联赛层：积分榜、xG 积分榜（xPts）、近期状态。输入为规范 matches 表。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import poisson


def finished(matches: pd.DataFrame) -> pd.DataFrame:
    m = matches[(matches["status"] == "FINISHED") & matches["hg"].notna() & matches["ag"].notna()]
    return m.sort_values(["match_date", "home"]).reset_index(drop=True)


def xpts(hxg: np.ndarray, axg: np.ndarray, max_goals: int = 10) -> tuple[np.ndarray, np.ndarray]:
    """由双方 xG 计算期望积分（假设进球服从独立泊松分布）。"""
    k = np.arange(max_goals + 1)
    ph = poisson.pmf(k[None, :], np.asarray(hxg, float)[:, None])
    pa = poisson.pmf(k[None, :], np.asarray(axg, float)[:, None])
    joint = ph[:, :, None] * pa[:, None, :]
    home_win = np.tril(np.ones((max_goals + 1, max_goals + 1)), -1)
    p_h = (joint * home_win).sum(axis=(1, 2))
    p_a = (joint * home_win.T).sum(axis=(1, 2))
    p_d = 1 - p_h - p_a
    return 3 * p_h + p_d, 3 * p_a + p_d


def team_long(matches: pd.DataFrame) -> pd.DataFrame:
    """一场比赛拆成两行（主/客各一行），便于按球队聚合。"""
    m = finished(matches).copy()
    has_xg = m["hxg"].notna() & m["axg"].notna() if "hxg" in m else pd.Series(False, index=m.index)
    xp_h = pd.Series(np.nan, index=m.index)
    xp_a = pd.Series(np.nan, index=m.index)
    if has_xg.any():
        a, b = xpts(m.loc[has_xg, "hxg"].astype(float), m.loc[has_xg, "axg"].astype(float))
        xp_h.loc[has_xg], xp_a.loc[has_xg] = a, b
    home = pd.DataFrame({
        "league": m["league"], "season": m["season"], "match_date": m["match_date"], "team": m["home"],
        "opponent": m["away"], "venue": "主", "gf": m["hg"], "ga": m["ag"],
        "xgf": m.get("hxg"), "xga": m.get("axg"), "xpts": xp_h,
        "shots": m.get("h_shots"), "shots_against": m.get("a_shots"),
        "sot": m.get("h_sot"), "sot_against": m.get("a_sot"),
    })
    away = pd.DataFrame({
        "league": m["league"], "season": m["season"], "match_date": m["match_date"], "team": m["away"],
        "opponent": m["home"], "venue": "客", "gf": m["ag"], "ga": m["hg"],
        "xgf": m.get("axg"), "xga": m.get("hxg"), "xpts": xp_a,
        "shots": m.get("a_shots"), "shots_against": m.get("h_shots"),
        "sot": m.get("a_sot"), "sot_against": m.get("h_sot"),
    })
    long = pd.concat([home, away], ignore_index=True)
    for c in ("gf", "ga", "shots", "shots_against", "sot", "sot_against"):
        long[c] = pd.to_numeric(long[c], errors="coerce").astype(float)
    for c in ("xgf", "xga", "xpts"):
        long[c] = pd.to_numeric(long[c], errors="coerce").astype(float)
    long["result"] = np.select([long["gf"] > long["ga"], long["gf"] == long["ga"]], ["W", "D"], "L")
    long["pts"] = long["result"].map({"W": 3, "D": 1, "L": 0})
    # 与 xG 对比的"实际值"只取有 xG 的场次：co.uk 尚未收录的最新赛果（来自 openfootball/fdorg）没有 xG，
    # 若把它们的积分/进球也算进去，积分−xPts、终结超预期会被系统性放大
    has_xg = long["xgf"].notna() & long["xga"].notna()
    long["pts_x"] = long["pts"].where(has_xg)
    long["gf_x"] = long["gf"].where(has_xg)
    long["ga_x"] = long["ga"].where(has_xg)
    return long.sort_values(["match_date", "team"]).reset_index(drop=True)


def standings(matches: pd.DataFrame) -> pd.DataFrame:
    """积分榜。排序：积分 → 净胜球 → 进球（各联赛细则如相互战绩未实现，已在页面注明）。"""
    long = team_long(matches)
    if long.empty:
        return pd.DataFrame()
    g = long.groupby("team")
    t = pd.DataFrame({
        "赛": g.size(),
        "胜": g["result"].apply(lambda s: (s == "W").sum()),
        "平": g["result"].apply(lambda s: (s == "D").sum()),
        "负": g["result"].apply(lambda s: (s == "L").sum()),
        "进": g["gf"].sum().astype(int),
        "失": g["ga"].sum().astype(int),
        "积分": g["pts"].sum().astype(int),
        "xG": g["xgf"].sum(min_count=1),
        "xGA": g["xga"].sum(min_count=1),
        "xPts": g["xpts"].sum(min_count=1),
        "xG场次": g["xpts"].count(),
        "近5场": g["result"].apply(lambda s: "".join(s.tail(5))),
        "_pts_x": g["pts_x"].sum(min_count=1),
    })
    t["净胜"] = t["进"] - t["失"]
    t["xGD"] = t["xG"] - t["xGA"]
    t["积分-xPts"] = t["_pts_x"] - t["xPts"]  # 同口径：仅有 xG 的场次
    t = t.reset_index().sort_values(["积分", "净胜", "进", "team"], ascending=[False, False, False, True])
    t.insert(0, "排名", range(1, len(t) + 1))
    cols = ["排名", "team", "赛", "胜", "平", "负", "进", "失", "净胜", "积分", "近5场", "xG场次", "xG", "xGA", "xGD",
            "xPts", "积分-xPts"]
    return t[cols].rename(columns={"team": "球队"}).reset_index(drop=True)

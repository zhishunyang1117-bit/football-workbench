"""球队层（当赛季，基于 football-data.co.uk 的比赛级数据）：xG 走势、射门质量、终结与门将表现。"""

from __future__ import annotations

import pandas as pd

from fwb.analytics.league import team_long


def match_log(matches: pd.DataFrame, team: str, window: int = 5) -> pd.DataFrame:
    log = team_long(matches)
    log = log[log["team"] == team].sort_values("match_date").reset_index(drop=True)
    for c in ("xgf", "xga", "gf", "ga"):
        log[f"{c}_roll"] = log[c].rolling(window, min_periods=1).mean()
    return log


def league_profile(matches: pd.DataFrame) -> pd.DataFrame:
    """每队场均数据 + 终结/扑救超预期（进球−xG、xGA−失球）。"""
    long = team_long(matches)
    if long.empty:
        return pd.DataFrame()
    g = long.groupby("team")
    n = g.size()
    prof = pd.DataFrame({
        "场次": n,
        "场均xG": g["xgf"].mean(),
        "场均xGA": g["xga"].mean(),
        "场均射门": g["shots"].mean(),
        "场均被射门": g["shots_against"].mean(),
        "射正率": g["sot"].sum() / g["shots"].sum(),
        "每射xG": g["xgf"].sum() / g["shots"].sum(),
        "被射每射xG": g["xga"].sum() / g["shots_against"].sum(),
        # 同口径：只比较有 xG 的场次（见 league.team_long 的 gf_x/ga_x）
        "终结超预期": g["gf_x"].sum(min_count=1) - g["xgf"].sum(min_count=1),
        "防守超预期": g["xga"].sum(min_count=1) - g["ga_x"].sum(min_count=1),
    })
    return prof.reset_index().rename(columns={"team": "球队"})

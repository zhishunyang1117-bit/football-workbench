"""质量门：写入前校验，失败抛 QualityError（CI 中不提交，保持上一版本）。"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from fwb.settings import Season
from fwb.storage import QualityError


def check_matches(df: pd.DataFrame, league: str, season: str) -> list[str]:
    """返回警告列表；致命问题直接抛 QualityError。"""
    warnings: list[str] = []
    if df.empty:
        return warnings
    tag = f"{league}/{season}"
    dup = df.duplicated(["league", "season", "home", "away"]).sum()
    if dup:
        raise QualityError(f"{tag}: 主键重复 {dup} 行")
    teams = set(df["home"]) | set(df["away"])
    if len(teams) > 20:
        raise QualityError(f"{tag}: 出现 {len(teams)} 支球队（>20），多半是队名未统一：{sorted(teams)}")
    fin = df[df["status"] == "FINISHED"]
    if (fin["hg"].isna() | fin["ag"].isna()).any():
        raise QualityError(f"{tag}: 已完赛比赛缺少比分")
    if ((fin["hg"] < 0) | (fin["ag"] < 0)).any():
        raise QualityError(f"{tag}: 出现负数比分")
    s = Season(season)
    lo, hi = date(s.start_year, 7, 1), date(s.start_year + 1, 7, 31)
    bad_dates = df[(df["match_date"] < lo) | (df["match_date"] > hi)]
    if len(bad_dates):
        warnings.append(f"{tag}: {len(bad_dates)} 场比赛日期超出赛季窗口 {lo}~{hi}")
    per_team = pd.concat([fin["home"], fin["away"]]).value_counts()
    if len(per_team) and per_team.max() > 2 * (len(teams) - 1):
        raise QualityError(f"{tag}: 球队完赛场次超过双循环上限")
    odds = [c for c in df.columns if c.startswith("odds_")]
    if odds:
        o = df[odds].astype(float)
        if ((o <= 1.0) & o.notna()).any().any():
            warnings.append(f"{tag}: 存在 ≤1.0 的赔率")
    return warnings


def check_probabilities(df: pd.DataFrame, prefixes=("dc", "elo", "mkt"), tol: float = 1e-3) -> None:
    for p in prefixes:
        cols = [f"{p}_h", f"{p}_d", f"{p}_a"]
        if not set(cols) <= set(df.columns):
            continue
        x = df[cols].astype(float).dropna()
        if x.empty:
            continue
        if ((x < 0) | (x > 1)).any().any():
            raise QualityError(f"{p} 概率越界")
        if not np.allclose(x.sum(axis=1), 1.0, atol=tol):
            raise QualityError(f"{p} 概率之和不为 1")

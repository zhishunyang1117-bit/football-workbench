"""合并优先级、质量门、积分榜。"""

from datetime import date

import pandas as pd
import pytest

from fwb.analytics.league import standings, xpts
from fwb.pipeline import curate, quality
from fwb.sources.football_data_uk import COLUMNS
from fwb.storage import QualityError


def _fduk(rows):
    df = pd.DataFrame(rows)
    for c in COLUMNS.values():
        if c not in df:
            df[c] = pd.NA
    df["season"], df["status"] = "2627", "FINISHED"
    return df


def _of(rows):
    df = pd.DataFrame(rows)
    df["league"], df["season"] = "E0", "2627"
    df["unresolved"] = df.get("unresolved", False)
    for c in ("hthg", "htag", "kickoff"):
        df[c] = df.get(c, pd.NA)
    return df


def test_merge_priority_and_unresolved():
    fd = _fduk([{"league": "E0", "match_date": date(2026, 8, 21), "home": "A", "away": "B", "hg": 3, "ag": 0}])
    of = _of([
        {"match_date": date(2026, 8, 21), "home": "A", "away": "B", "hg": 2, "ag": 0, "status": "FINISHED"},
        {"match_date": date(2026, 9, 27), "home": "B", "away": "A", "hg": 1, "ag": 1, "status": "FINISHED"},
        {"match_date": date(2026, 10, 10), "home": "A", "away": "C", "hg": None, "ag": None, "status": "SCHEDULED"},
        {"match_date": date(2026, 10, 10), "home": "X FC", "away": "A", "hg": None, "ag": None,
         "status": "SCHEDULED", "unresolved": True},
    ])
    out, rep = curate.merge(fd, of)
    by = out.set_index(["home", "away"])
    assert by.loc[("A", "B"), "hg"] == 3  # football-data.co.uk 优先
    assert by.loc[("A", "B"), "result_source"] == "football-data.co.uk"
    assert by.loc[("B", "A"), "result_source"] == "openfootball"  # 补齐 co.uk 尚未收录的赛果
    assert ("X FC", "A") not in by.index  # 未解析队名不入规范表
    assert rep == {"unresolved_openfootball": 1, "unresolved_fdorg": 0, "score_conflicts": 1}


def test_quality_rejects_split_team_names():
    teams = [f"T{i}" for i in range(21)]
    df = pd.DataFrame({"league": "E0", "season": "2627", "home": teams, "away": teams[1:] + teams[:1],
                       "status": "SCHEDULED", "hg": pd.NA, "ag": pd.NA, "match_date": date(2026, 9, 1)})
    with pytest.raises(QualityError):
        quality.check_matches(df, "E0", "2627")


def test_quality_probabilities():
    ok = pd.DataFrame({"dc_h": [0.5], "dc_d": [0.3], "dc_a": [0.2]})
    quality.check_probabilities(ok)
    with pytest.raises(QualityError):
        quality.check_probabilities(pd.DataFrame({"dc_h": [0.5], "dc_d": [0.3], "dc_a": [0.3]}))


def test_standings_points_and_order():
    m = pd.DataFrame({
        "league": "E0", "season": "2627", "status": "FINISHED",
        "match_date": [date(2026, 8, d) for d in (1, 2, 3)],
        "home": ["A", "B", "C"], "away": ["B", "C", "A"],
        "hg": pd.array([2, 1, 0], dtype="Int64"), "ag": pd.array([0, 1, 0], dtype="Int64"),
        "hxg": [1.5, 1.0, 0.4], "axg": [0.5, 1.2, 0.9],
    })
    t = standings(m).set_index("球队")
    assert t.loc["A", "积分"] == 4 and t.loc["B", "积分"] == 1 and t.loc["C", "积分"] == 2
    assert list(t.index) == ["A", "C", "B"]
    assert t.loc["A", "近5场"] == "WD"


def test_luck_metrics_only_use_matches_with_xg():
    """最新赛果暂无 xG 时，积分−xPts 只比较有 xG 的场次（否则被系统性放大）。"""
    m = pd.DataFrame({
        "league": "E0", "season": "2627", "status": "FINISHED",
        "match_date": [date(2026, 8, 1), date(2026, 8, 8)], "home": ["A", "A"], "away": ["B", "C"],
        "hg": pd.array([1, 3], dtype="Int64"), "ag": pd.array([1, 0], dtype="Int64"),
        "hxg": [1.2, float("nan")], "axg": [1.2, float("nan")],
    })
    t = standings(m).set_index("球队")
    assert t.loc["A", "积分"] == 4 and t.loc["A", "xG场次"] == 1
    assert abs(t.loc["A", "积分-xPts"] - (1 - t.loc["A", "xPts"])) < 1e-9  # 只用第一场的 1 分


def test_xpts_bounds():
    h, a = xpts([1.5, 0.2], [0.5, 2.5])
    assert all(2 <= x + y <= 3 for x, y in zip(h, a))
    assert h[0] > a[0] and h[1] < a[1]

import numpy as np
import pandas as pd

from fwb.analytics import models

DC = {"xi": 0.0018, "train_days": 730, "min_train_matches": 60}
ELO = {"k": 20, "home_field_advantage": 60, "season_regression": 0.2, "promoted_default": 1420}


def test_implied_probs_remove_margin():
    p = models.implied_probs(pd.DataFrame({"h": [2.1, np.nan], "d": [3.4, 3.0], "a": [3.6, 2.0]}))
    assert np.isclose(p[0].sum(), 1.0) and np.isnan(p[1]).all()
    assert p[0, 0] > p[0, 2]


def test_window_start_groups_midweek_and_weekend():
    ws = lambda s: models.window_start(pd.Timestamp(s)).date().isoformat()  # noqa: E731
    assert ws("2026-09-22") == ws("2026-09-24") == "2026-09-22"  # 周二–周四
    assert ws("2026-09-25") == ws("2026-09-28") == "2026-09-25"  # 周五–周一


def test_backtest_is_out_of_sample_and_beats_uniform(synthetic_league):
    bt = models.backtest(synthetic_league, ["2526"], DC, ELO)
    assert len(bt) > 50
    # 每个预测只用窗口开始前的数据
    assert (pd.to_datetime(bt["window_start"]) <= pd.to_datetime(bt["match_date"])).all()
    probs = bt[["dc_h", "dc_d", "dc_a"]].dropna().to_numpy()
    assert np.allclose(probs.sum(axis=1), 1, atol=1e-6)
    ev = models.evaluate(bt).set_index("模型")
    uniform_rps = models.pb.metrics.rps_array(np.full((len(bt), 3), 1 / 3), bt["outcome"].to_numpy()).mean()
    assert ev.loc["Dixon-Coles", "RPS"] < uniform_rps


def test_dc_recovers_team_strength_order(synthetic_league):
    dc = models.DixonColes(0.0018).fit(synthetic_league, synthetic_league["match_date"].max())
    strong_home = dc.predict("T9", "T0")
    weak_home = dc.predict("T0", "T9")
    assert strong_home["dc_h"] > 0.6 > weak_home["dc_h"]
    assert dc.predict("T0", "Unknown") is None


def test_elo_promoted_team_inherits_relegated_mean():
    e = models.SeasonElo(**ELO)
    e.start_season({"A", "B", "C"})
    e.elo.ratings.update({"A": 1600.0, "B": 1500.0, "C": 1400.0})
    e.start_season({"A", "B", "D"})  # C 降级、D 升级
    assert np.isclose(e.elo.ratings["D"], 1500 + 0.8 * (1400 - 1500))
    assert np.isclose(e.elo.ratings["A"], 1580.0)


def test_elo_new_season_uses_full_fixture_list():
    """新赛季首轮只踢了一场时，保级队不能被当成"离开"，未出场的升班马也要继承降级队均分。"""
    s1 = pd.DataFrame({"league": "E0", "season": "2425", "match_date": pd.to_datetime(["2024-08-10"] * 3).date,
                       "home": ["A", "B", "C"], "away": ["B", "C", "A"],
                       "hg": pd.array([3, 3, 0], dtype="Int64"), "ag": pd.array([0, 0, 0], dtype="Int64")})
    s2 = pd.DataFrame({"league": "E0", "season": "2526", "match_date": pd.to_datetime(["2025-08-10"]).date,
                       "home": ["A"], "away": ["D"], "hg": pd.array([1], dtype="Int64"),
                       "ag": pd.array([1], dtype="Int64")})
    fin = pd.concat([s1, s2], ignore_index=True)
    teams = {"2425": {"A", "B", "C"}, "2526": {"A", "B", "D"}}  # C 降级，D 升级；B 本季还没出场
    e = models.SeasonElo(**ELO)
    pre, _ = e.run(fin, teams)
    pre_bad, _ = models.SeasonElo(**ELO).run(fin)  # 不传完整名单：B 被误判为离开
    d_pre = pre[pre["season"] == "2526"]["elo_away_pre"].iloc[0]
    # C 第二季未出场，其评分即赛季切换时回归后的值；D 应继承它
    assert np.isclose(d_pre, e.elo.ratings["C"])
    assert not np.isclose(pre_bad[pre_bad["season"] == "2526"]["elo_away_pre"].iloc[0], d_pre)


def test_predict_upcoming(synthetic_league):
    m = synthetic_league.copy()
    future = pd.DataFrame({"league": "E0", "season": "2526", "match_date": [pd.Timestamp("2026-07-10").date()],
                           "kickoff": "15:00", "home": "T9", "away": "T1", "status": "SCHEDULED",
                           "hg": pd.array([pd.NA], dtype="Int64"), "ag": pd.array([pd.NA], dtype="Int64")})
    m = pd.concat([m, future], ignore_index=True)
    fx = pd.DataFrame({"league": ["E0"], "home": ["T9"], "away": ["T1"], "odds_avg_h": [1.5],
                       "odds_avg_d": [4.2], "odds_avg_a": [6.5]})
    p = models.predict_upcoming(m, fx, DC, ELO, "2026-07-05", 14)
    assert len(p) == 1
    r = p.iloc[0]
    assert r["dc_h"] > r["dc_a"] and np.isclose(r[["mkt_h", "mkt_d", "mkt_a"]].sum(), 1)
    assert r["dc_top_score"]

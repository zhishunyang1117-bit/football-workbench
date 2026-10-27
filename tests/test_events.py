"""StatsBomb 事件指标：用最小化的合成事件流验证口径（出场时间、xA 归属、推进、PPDA）。"""

import numpy as np
import pandas as pd

from fwb.analytics import events as E
from fwb.analytics import players as P

_i = iter(range(1, 10_000))


def ev(t, team, player=None, period=1, minute=0, second=0, loc=None, **kw):
    e = {"id": f"e{next(_i)}", "index": next(_i), "period": period, "minute": minute, "second": second,
         "type": {"name": t}, "team": {"id": {"Home": 1, "Away": 2}.get(team), "name": team},
         "possession_team": {"name": team}}
    if player:
        e["player"] = {"id": player[0], "name": player[1]}
        e["position"] = {"name": player[2]}
    if loc:
        e["location"] = loc
    e.update(kw)
    return e


def xi(team, players):
    return ev("Starting XI", team, tactics={"lineup": [
        {"player": {"id": pid, "name": n}, "position": {"name": pos}} for pid, n, pos in players]})


H = [(1, "Keeper H", "Goalkeeper"), (2, "Mid H", "Center Midfield"), (3, "Fwd H", "Center Forward")]
A = [(11, "Keeper A", "Goalkeeper"), (12, "Mid A", "Center Midfield")]


def match():
    pass_id = "kp1"
    events = [
        xi("Home", H), xi("Away", A),
        ev("Pass", "Home", H[1], minute=10, loc=[50, 40], id=pass_id,
           **{"pass": {"end_location": [105, 40], "recipient": {"id": 3, "name": "Fwd H"}, "shot_assist": True}}),
        ev("Shot", "Home", H[2], minute=10, second=5, loc=[105, 40],
           shot={"statsbomb_xg": 0.3, "outcome": {"name": "Goal"}, "type": {"name": "Open Play"},
                 "key_pass_id": pass_id}),
        ev("Pass", "Away", A[1], minute=20, loc=[30, 40], **{"pass": {"end_location": [40, 40]}}),
        ev("Pass", "Away", A[1], minute=21, loc=[60, 40], **{"pass": {"end_location": [70, 40]}}),
        ev("Interception", "Home", H[1], minute=22, loc=[80, 40]),
        ev("Half End", "Home", period=1, minute=47),
        ev("Substitution", "Home", H[2], period=2, minute=60,
           substitution={"replacement": {"id": 4, "name": "Sub H"}}),
        ev("Foul Committed", "Away", A[1], period=2, minute=80, loc=[50, 40],
           foul_committed={"card": {"name": "Red Card"}}),
        ev("Half End", "Home", period=2, minute=95),
    ]
    for i, e in enumerate(events):
        e["index"] = i
    return E.flatten(events)


def test_minutes_from_events_handle_subs_and_red_cards():
    mp = E.minutes_played(match()).set_index("player_id")["minutes"]
    total = 47 + (95 - 45)  # 上半场含补时 + 下半场
    assert np.isclose(mp[1], total)
    assert np.isclose(mp[3], 47 + 15)  # 60′ 被换下
    assert np.isclose(mp[4], total - (47 + 15))  # 替补登场
    assert np.isclose(mp[12], 47 + 35)  # 80′ 红牌


def test_player_match_xa_and_progressive():
    pm = E.player_match(match(), []).set_index("player_id")
    assert pm.loc[2, "xa"] == 0.3 and pm.loc[2, "key_passes"] == 1
    assert pm.loc[2, "prog_passes"] == 1 and pm.loc[2, "passes_into_box"] == 1
    assert pm.loc[3, "goals"] == 1 and np.isclose(pm.loc[3, "npxg"], 0.3)


def test_team_match_ppda():
    tm = E.team_match(match(), "Home", "Away").set_index("team")
    # 对手在其本方 60% 区域（x<72）传球 2 次；主队前 60%（x>48）防守动作 1 次（拦截）
    assert tm.loc["Home", "ppda"] == 2.0
    assert tm.loc["Home", "goals"] == 1 and tm.loc["Away", "goals_against"] == 1


def test_team_match_joins_by_id_when_names_differ():
    # 实测 StatsBomb：事件里叫 'Caen'，比赛索引里叫 'Stade Malherbe Caen'
    events = [
        ev("Pass", "Caen", (1, "p1", "Center Midfield"), loc=[30, 40], **{"pass": {"end_location": [40, 40]}}),
        ev("Shot", "PSG", (2, "p2", "Center Forward"), loc=[110, 40],
           shot={"statsbomb_xg": 0.5, "outcome": {"name": "Goal"}, "type": {"name": "Open Play"}}),
    ]
    events[0]["team"] = {"id": 7, "name": "Caen"}
    events[1]["team"] = {"id": 9, "name": "PSG"}
    e = E.flatten(events)
    tm = E.team_match(e, "Paris Saint-Germain", "Stade Malherbe Caen", 9, 7).set_index("team")
    assert tm.loc["Paris Saint-Germain", "goals"] == 1 and tm.loc["Stade Malherbe Caen", "passes"] == 1
    assert np.isnan(tm.loc["Paris Saint-Germain", "ppda"])  # 没有高位防守动作 → 无定义，而非对手传球总数
    import pytest
    with pytest.raises(ValueError):
        E.team_match(e, "Paris Saint-Germain", "Stade Malherbe Caen", 9, 999)


def test_season_table_percentiles_and_similarity():
    pm = pd.DataFrame({
        "competition_id": 2, "season_id": 27, "match_id": [1, 1, 1, 2, 2, 2],
        "player_id": [1, 2, 3, 1, 2, 3], "team": "X", "player": ["a", "b", "c"] * 2,
        "position": "Center Forward", "position_group": "FW", "minutes": 90.0,
        **{m: [1.0, 2.0, 3.0, 1.0, 2.0, 3.0] for m in P.COUNT_METRICS},
        "passes_att": 10.0, "tackles_att": 2.0, "dribbles_att": 2.0, "aerials_lost": 0.0,
    })
    t = P.season_table(pm)
    assert (t["games"] == 2).all() and np.isclose(t.set_index("player_id").loc[3, "goals_p90"], 3.0)
    pct = P.percentiles(t, ["goals", "turnovers"], min_minutes=0).set_index("player_id")
    assert pct.loc[3, "goals_pct"] == 100 and pct.loc[3, "turnovers_pct"] < pct.loc[1, "turnovers_pct"]
    sim = P.similar_players(t, 3, "X", ["goals", "xg", "shots"], min_minutes=0)
    assert list(sim["player_id"])[0] == 2

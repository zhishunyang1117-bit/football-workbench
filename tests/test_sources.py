"""数据源解析测试（离线，使用与真实格式一致的样例载荷；格式于 2026-09-26 实测核对）。"""

from datetime import date

import pandas as pd

from fwb.pipeline import curate
from fwb.sources import football_data_org as fdorg
from fwb.sources import football_data_uk as fduk
from fwb.sources import openfootball

CSV = (
    "﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HxG,AxG,HS,AS,HST,AST,HF,AF,HC,AC,"
    "HY,AY,HR,AR,AvgH,AvgD,AvgA,AvgCH,AvgCD,AvgCA\n"
    "E0,21/08/2026,20:00,Arsenal,Coventry,3,0,H,2,0,H,T Bramall,1.88,0.2,20,4,6,1,10,13,8,2,1,1,0,0,"
    "1.19,6.77,14.19,1.2,6.72,14.0\n"
    ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,\n"
)


def test_fduk_normalize_handles_bom_dates_and_blank_rows():
    df = fduk.normalize(fduk._read_csv(CSV.encode("utf-8")), "2627")
    assert len(df) == 1
    r = df.iloc[0]
    assert r["league"] == "E0" and r["home"] == "Arsenal" and r["match_date"] == date(2026, 8, 21)
    assert r["hg"] == 3 and r["hxg"] == 1.88 and r["odds_close_avg_h"] == 1.2
    assert "odds_close_ps_h" in df.columns  # 缺失列补齐，列集合稳定


def test_openfootball_both_score_shapes_and_resolution():
    payload = {"matches": [
        {"round": "Matchday 1", "date": "2026-08-21", "time": "20:00", "team1": "Arsenal FC",
         "team2": "Coventry City FC", "score": {"ht": [2, 0], "ft": [3, 0]}},
        {"round": "Matchday 3", "date": "2026-09-05", "time": "15:00", "team1": "Nottingham Forest FC",
         "team2": "Tottenham Hotspur FC", "score": [0, 0]},
        {"round": "Matchday 7", "date": "2026-10-10", "time": "12:30", "team1": "Arsenal FC",
         "team2": "Mystery FC"},
    ]}
    raw = openfootball.parse(payload, "E0", "2627", "Europe/London")
    assert list(raw["status"]) == ["FINISHED", "FINISHED", "SCHEDULED"]
    assert raw.loc[1, "hg"] == 0 and raw.loc[0, "hthg"] == 2
    df, bad = curate.resolve_names(raw, {"Arsenal", "Coventry", "Nott'm Forest", "Tottenham"}, strict=True)
    assert df.loc[1, "home"] == "Nott'm Forest" and bad == ["Mystery FC"]
    assert list(df["unresolved"]) == [False, False, True]


def test_openfootball_local_kickoff_converted_to_london():
    # 实测：openfootball 西甲 19:30（马德里）= co.uk 18:30（伦敦）
    payload = {"matches": [
        {"round": "1", "date": "2026-08-15", "time": "19:30", "team1": "Deportivo Alavés", "team2": "Getafe CF"},
        {"round": "1", "date": "2026-08-16", "time": "00:30", "team1": "A", "team2": "B"},  # 跨午夜
        {"round": "1", "date": "2026-08-17", "time": None, "team1": "C", "team2": "D"},
    ]}
    df = openfootball.parse(payload, "SP1", "2627", "Europe/Madrid")
    assert df.loc[0, "kickoff"] == "18:30" and df.loc[0, "match_date"] == date(2026, 8, 15)
    assert df.loc[1, "kickoff"] == "23:30" and df.loc[1, "match_date"] == date(2026, 8, 15)
    assert pd.isna(df.loc[2, "kickoff"]) and df.loc[2, "match_date"] == date(2026, 8, 17)


def test_empty_payloads_return_typed_empty_frames():
    for df in (openfootball.parse({"matches": []}, "E0", "2627"),
               fdorg.parse_matches({"matches": []}, "E0", "2627"),
               fdorg.parse_scorers({"scorers": []}, "E0", "2627")):
        assert df.empty and {"league", "season"} <= set(df.columns)


def test_resolve_names_lenient_keeps_promoted_teams():
    raw = openfootball.parse({"matches": [{"round": "1", "date": "2026-08-21", "time": "20:00",
                                           "team1": "Arsenal FC", "team2": "Coventry City FC"}]}, "E0", "2627")
    df, bad = curate.resolve_names(raw, {"Arsenal", "Chelsea"}, strict=False)  # 上季队名兜底
    assert df.loc[0, "home"] == "Arsenal" and df.loc[0, "away"] == "Coventry City FC"
    assert not df["unresolved"].any() and bad == ["Coventry City FC"]


def test_fdorg_parse_matches_and_scorers():
    payload = {"matches": [{
        "id": 537000, "utcDate": "2026-10-10T11:30:00Z", "status": "TIMED", "matchday": 7,
        "homeTeam": {"id": 57, "name": "Arsenal FC"}, "awayTeam": {"id": 341, "name": "Leeds United FC"},
        "score": {"winner": None, "fullTime": {"home": None, "away": None}, "halfTime": {"home": None, "away": None}},
    }]}
    m, _ = curate.resolve_names(fdorg.parse_matches(payload, "E0", "2627"), {"Arsenal", "Leeds"}, strict=True)
    assert m.loc[0, "home"] == "Arsenal" and m.loc[0, "away"] == "Leeds"
    assert m.loc[0, "match_date"] == date(2026, 10, 10) and m["hg"].isna().all()
    sc = fdorg.parse_scorers({"scorers": [{"player": {"id": 1, "name": "E. Haaland", "section": "Offence"},
                                            "team": {"name": "Manchester City FC"}, "playedMatches": 5,
                                            "goals": 5, "assists": None, "penalties": 1}]}, "E0", "2627")
    assert sc.loc[0, "goals"] == 5 and sc["assists"].isna().all()

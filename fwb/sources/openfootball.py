"""openfootball/football.json（CC0-1.0）：整季赛程与比分，提供未来赛程。

文件：{base}/{2026-27}/{en.1}.json → {"name": ..., "matches": [{round, date, time, team1, team2, score?}]}
- 未开赛的比赛没有 score 字段；score 有两种写法：{"ft": [h, a], "ht": [h, a]} 或简写 [h, a]。
- date/time 是**联赛当地时间**（实测：西/德/意/法比 football-data.co.uk 的英国时间晚 1 小时），
  入库时统一换算为 Europe/London，与 co.uk / football-data.org 口径一致。
- 队名是全称（如 "Arsenal FC"），这里只保留原名（home_raw/away_raw）；
  映射到规范名在 pipeline.curate.resolve_names 中进行，每次运行都会用最新别名表重新解析。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pandas as pd

from fwb.http import FetchResult, client
from fwb.settings import Season, source_cfg

COLUMNS = ["league", "season", "round", "match_date", "kickoff", "home_raw", "away_raw", "hg", "ag", "hthg", "htag",
           "status", "home", "away", "unresolved"]


def _setup() -> str:
    cfg = source_cfg("openfootball")
    client().set_min_interval(urlsplit(cfg["base_url"]).netloc, cfg["min_interval_s"])
    return cfg["base_url"].rstrip("/")


def _to_london(dates: pd.Series, times: pd.Series, tz: str) -> tuple[pd.Series, pd.Series]:
    """联赛当地 (date, time) → 英国时间 (date, 'HH:MM')。缺少开球时间的只保留日期。"""
    has_time = times.notna() & (times.astype("string").str.len() > 0)
    local = pd.to_datetime(dates.astype(str) + " " + times.where(has_time, "12:00").astype(str), errors="coerce")
    london = local.dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("Europe/London")
    out_date = london.dt.date.where(has_time & london.notna(), pd.to_datetime(dates).dt.date)
    out_time = london.dt.strftime("%H:%M").where(has_time & london.notna(), pd.NA)
    return out_date, out_time


def parse(payload: dict, league: str, season: str, tz: str = "Europe/London") -> pd.DataFrame:
    rows = []
    for m in payload.get("matches", []):
        score = m.get("score")
        if isinstance(score, list):  # 简写形式 "score": [h, a]，仅全场比分
            ft, ht = (score if len(score) == 2 else None), None
        else:
            ft, ht = (score or {}).get("ft"), (score or {}).get("ht")
        rows.append(
            {
                "league": league,
                "season": season,
                "round": m.get("round"),
                "match_date": m["date"],
                "kickoff": m.get("time"),
                "home_raw": m["team1"],
                "away_raw": m["team2"],
                "hg": ft[0] if ft else None,
                "ag": ft[1] if ft else None,
                "hthg": ht[0] if ht else None,
                "htag": ht[1] if ht else None,
                "status": "FINISHED" if ft else "SCHEDULED",
            }
        )
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    df["match_date"], df["kickoff"] = _to_london(df["match_date"], df["kickoff"], tz)
    df["home"], df["away"], df["unresolved"] = df["home_raw"], df["away_raw"], False
    for c in ("hg", "ag", "hthg", "htag"):
        df[c] = pd.to_numeric(df[c]).astype("Int64")
    for c in ("round", "kickoff", "home_raw", "away_raw", "home", "away", "status"):
        df[c] = df[c].astype("string")
    return df[COLUMNS]


def fetch_season(league: str, code: str, season: str, tz: str,
                 etag: str | None = None) -> tuple[pd.DataFrame | None, FetchResult]:
    base = _setup()
    res = client().get(f"{base}/{Season(season).openfootball}/{code}.json", etag=etag)
    if res.not_modified:
        return None, res
    return parse(res.json(), league, season, tz), res

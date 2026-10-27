"""StatsBomb Open Data（事件级历史数据）。

条款（open-data README "Terms & Conditions"）：发布、分享或分发基于该数据的研究/分析/洞察时，
须注明数据来源为 StatsBomb 并使用其 logo（Media Pack）。应用页脚已统一加署名。

说明：不依赖 statsbombpy —— 其仓库（现 hudl/statsbombpy）没有 LICENSE 文件，许可不明；
这里直接读取官方仓库的原始 JSON，功能等价且无许可风险。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pandas as pd

from fwb.http import client
from fwb.settings import source_cfg


def _base() -> str:
    cfg = source_cfg("statsbomb_open_data")
    client().set_min_interval(urlsplit(cfg["base_url"]).netloc, cfg["min_interval_s"])
    return cfg["base_url"].rstrip("/")


def competitions() -> pd.DataFrame:
    return pd.DataFrame(client().get(f"{_base()}/competitions.json").json())


def matches(competition_id: int, season_id: int) -> pd.DataFrame:
    data = client().get(f"{_base()}/matches/{competition_id}/{season_id}.json").json()
    rows = [
        {
            "match_id": int(m["match_id"]),
            "competition_id": int(competition_id),
            "season_id": int(season_id),
            "competition": m["competition"]["competition_name"],
            "season_name": m["season"]["season_name"],
            "match_date": pd.to_datetime(m["match_date"]).date(),
            "match_week": m.get("match_week"),
            "home": m["home_team"]["home_team_name"],
            "away": m["away_team"]["away_team_name"],
            "home_team_id": int(m["home_team"]["home_team_id"]),
            "away_team_id": int(m["away_team"]["away_team_id"]),
            "home_score": m.get("home_score"),
            "away_score": m.get("away_score"),
            "last_updated": m.get("last_updated"),
        }
        for m in data
        if m.get("match_status", "available") == "available"
    ]
    df = pd.DataFrame(rows)
    for c in ("match_week", "home_score", "away_score"):
        df[c] = pd.to_numeric(df[c]).astype("Int64")
    return df


def events(match_id: int) -> list[dict]:
    return client().get(f"{_base()}/events/{int(match_id)}.json").json()


def lineups(match_id: int) -> list[dict]:
    return client().get(f"{_base()}/lineups/{int(match_id)}.json").json()

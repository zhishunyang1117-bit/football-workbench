"""football-data.org v4（可选的实时数据源）：比赛状态/比分、射手榜。

- 免费注册 token：https://www.football-data.org/client/register ，放入 .streamlit/secrets.toml
  （本地一键启动脚本首次运行会提示粘贴）或环境变量 FOOTBALL_DATA_ORG_TOKEN（GitHub Actions 用 repository secret）。
- 请求头 X-Auth-Token；免费层包含五大联赛（PL/PD/BL1/SA/FL1），按分钟限流，
  响应头 X-Requests-Available-Minute 显示剩余额度。免费层比分可能有延迟，不适合"秒级直播"。
- 每次运行每个联赛只发 2 个请求（整季比赛 + 射手榜），5 个联赛共 10 个请求。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pandas as pd

from fwb.http import client
from fwb.settings import Season, fdorg_token, source_cfg

def enabled() -> bool:
    flag = source_cfg("football_data_org").get("enabled", "auto")
    return (flag == "auto" or bool(flag)) and bool(fdorg_token())


def _get(path: str, params: dict) -> dict:
    cfg = source_cfg("football_data_org")
    token = fdorg_token()
    if not token:
        raise RuntimeError("未配置 football-data.org token")
    client().set_min_interval(urlsplit(cfg["base_url"]).netloc, cfg["min_interval_s"])
    res = client().get(f"{cfg['base_url'].rstrip('/')}{path}", params=params, headers={"X-Auth-Token": token})
    return res.json()


# 实时比分：一次请求取五大联赛的一个日期窗口（v4 /matches，competitions 过滤）
LIVE_COLUMNS = ["competition", "fdorg_id", "utc_kickoff", "status", "minute", "home", "away", "hg", "ag",
                "hthg", "htag", "last_updated"]


def parse_live(payload: dict) -> pd.DataFrame:
    rows = []
    for m in payload.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        ht = (m.get("score") or {}).get("halfTime") or {}
        rows.append({
            "competition": (m.get("competition") or {}).get("code"),
            "fdorg_id": int(m["id"]),
            "utc_kickoff": m["utcDate"],
            "status": m.get("status"),
            "minute": m.get("minute"),  # 部分套餐才提供，免费层可能为空
            "home": (m.get("homeTeam") or {}).get("shortName") or (m.get("homeTeam") or {}).get("name"),
            "away": (m.get("awayTeam") or {}).get("shortName") or (m.get("awayTeam") or {}).get("name"),
            "hg": ft.get("home"), "ag": ft.get("away"), "hthg": ht.get("home"), "htag": ht.get("away"),
            "last_updated": m.get("lastUpdated"),
        })
    if not rows:
        return pd.DataFrame(columns=LIVE_COLUMNS)
    df = pd.DataFrame(rows)[LIVE_COLUMNS]
    df["utc_kickoff"] = pd.to_datetime(df["utc_kickoff"], utc=True)
    for c in ("hg", "ag", "hthg", "htag"):
        df[c] = pd.to_numeric(df[c]).astype("Int64")
    return df.sort_values(["utc_kickoff", "competition", "home"]).reset_index(drop=True)


def fetch_live(date_from: str, date_to: str) -> pd.DataFrame:
    comps = ",".join(source_cfg("football_data_org").get("competitions", ["PL", "PD", "BL1", "SA", "FL1"]))
    return parse_live(_get("/matches", {"competitions": comps, "dateFrom": date_from, "dateTo": date_to}))


MATCH_COLUMNS = ["league", "season", "fdorg_id", "utc_kickoff", "match_date", "matchday", "status", "home_raw",
                 "away_raw", "hg", "ag", "hthg", "htag", "home", "away", "unresolved"]
SCORER_COLUMNS = ["league", "season", "player_id", "player", "nationality", "position", "team_raw", "played",
                  "goals", "assists", "penalties"]


def parse_matches(payload: dict, league: str, season: str) -> pd.DataFrame:
    """队名只保留原名；映射到规范名在 pipeline.curate.resolve_names 中进行。"""
    rows = []
    for m in payload.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        ht = (m.get("score") or {}).get("halfTime") or {}
        utc = pd.to_datetime(m["utcDate"], utc=True)
        rows.append(
            {
                "league": league,
                "season": season,
                "fdorg_id": int(m["id"]),
                "utc_kickoff": utc.isoformat(),
                "match_date": utc.tz_convert("Europe/London").date(),
                "matchday": m.get("matchday"),
                "status": m.get("status"),  # SCHEDULED/TIMED/IN_PLAY/PAUSED/FINISHED/POSTPONED/...
                "home_raw": m["homeTeam"]["name"],
                "away_raw": m["awayTeam"]["name"],
                "hg": ft.get("home"),
                "ag": ft.get("away"),
                "hthg": ht.get("home"),
                "htag": ht.get("away"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=MATCH_COLUMNS)
    df = pd.DataFrame(rows)
    df["home"], df["away"], df["unresolved"] = df["home_raw"], df["away_raw"], False
    for c in ("hg", "ag", "hthg", "htag", "matchday"):
        df[c] = pd.to_numeric(df[c]).astype("Int64")
    for c in ("home_raw", "away_raw", "home", "away", "status"):
        df[c] = df[c].astype("string")
    return df[MATCH_COLUMNS]


def parse_scorers(payload: dict, league: str, season: str) -> pd.DataFrame:
    rows = []
    for s in payload.get("scorers", []):
        p, t = s.get("player") or {}, s.get("team") or {}
        rows.append(
            {
                "league": league,
                "season": season,
                "player_id": int(p["id"]),
                "player": p.get("name"),
                "nationality": p.get("nationality"),
                "position": p.get("section") or p.get("position"),
                "team_raw": t.get("name"),
                "played": s.get("playedMatches"),
                "goals": s.get("goals"),
                "assists": s.get("assists"),
                "penalties": s.get("penalties"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=SCORER_COLUMNS)
    df = pd.DataFrame(rows)
    for c in ("played", "goals", "assists", "penalties"):
        df[c] = pd.to_numeric(df[c]).astype("Int64")
    return df[SCORER_COLUMNS]


def fetch_matches(league: str, code: str, season: str) -> pd.DataFrame:
    payload = _get(f"/competitions/{code}/matches", {"season": Season(season).fdorg})
    return parse_matches(payload, league, season)


def fetch_scorers(league: str, code: str, season: str, limit: int = 50) -> pd.DataFrame:
    payload = _get(f"/competitions/{code}/scorers", {"season": Season(season).fdorg, "limit": limit})
    return parse_scorers(payload, league, season)

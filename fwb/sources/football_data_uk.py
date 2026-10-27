"""football-data.co.uk：五大联赛赛果、技术统计、赛前与收盘赔率（2026/27 起含 HxG/AxG）。

- 赛季文件：{base}/mmz4281/{season}/{league}.csv，每个联赛每季一个文件（约 200KB），
  网站约每周更新两次。文件即"整季快照"，所以写入采用 replace_partitions，自动吸收事后修正。
- 近期赛程：{base}/fixtures.csv，含未来比赛的赛前赔率（所有联赛混合，需过滤）。
- 列含义见 https://www.football-data.co.uk/notes.txt ；带 "C" 的是收盘赔率（如 AvgCH）。
"""

from __future__ import annotations

import io
from urllib.parse import urlsplit

import pandas as pd

from fwb.http import FetchResult, client
from fwb.settings import source_cfg

# 原始列 → 规范列（只保留分析需要的列；各博彩公司单独赔率不入库，保留平均/最高/收盘）
COLUMNS = {
    "Div": "league",
    "Date": "match_date",
    "Time": "kickoff",
    "HomeTeam": "home",
    "AwayTeam": "away",
    "FTHG": "hg",
    "FTAG": "ag",
    "HTHG": "hthg",
    "HTAG": "htag",
    "Referee": "referee",
    "HxG": "hxg",
    "AxG": "axg",
    "HS": "h_shots",
    "AS": "a_shots",
    "HST": "h_sot",
    "AST": "a_sot",
    "HC": "h_corners",
    "AC": "a_corners",
    "HF": "h_fouls",
    "AF": "a_fouls",
    "HY": "h_yellow",
    "AY": "a_yellow",
    "HR": "h_red",
    "AR": "a_red",
    # 赛前市场平均 / 最高赔率
    "AvgH": "odds_avg_h",
    "AvgD": "odds_avg_d",
    "AvgA": "odds_avg_a",
    "MaxH": "odds_max_h",
    "MaxD": "odds_max_d",
    "MaxA": "odds_max_a",
    "Avg>2.5": "odds_avg_o25",
    "Avg<2.5": "odds_avg_u25",
    # 收盘赔率（模型评估的"市场基准"）
    "AvgCH": "odds_close_avg_h",
    "AvgCD": "odds_close_avg_d",
    "AvgCA": "odds_close_avg_a",
    "PSCH": "odds_close_ps_h",
    "PSCD": "odds_close_ps_d",
    "PSCA": "odds_close_ps_a",
    "AvgC>2.5": "odds_close_avg_o25",
    "AvgC<2.5": "odds_close_avg_u25",
}
INT_COLS = ["hg", "ag", "hthg", "htag", "h_shots", "a_shots", "h_sot", "a_sot", "h_corners", "a_corners",
            "h_fouls", "a_fouls", "h_yellow", "a_yellow", "h_red", "a_red"]


def _setup() -> str:
    cfg = source_cfg("football_data_uk")
    client().set_min_interval(urlsplit(cfg["base_url"]).netloc, cfg["min_interval_s"])
    return cfg["base_url"].rstrip("/")


def _read_csv(content: bytes) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip")
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    return df.dropna(how="all")


def normalize(raw: pd.DataFrame, season: str | None) -> pd.DataFrame:
    df = raw[[c for c in COLUMNS if c in raw.columns]].rename(columns=COLUMNS)
    df = df.dropna(subset=["home", "away"])
    for col in COLUMNS.values():  # 保证列集合稳定（旧赛季缺 xG / Pinnacle 列）
        if col not in df.columns:
            df[col] = pd.NA
    df = df[list(dict.fromkeys(COLUMNS.values()))]
    df["match_date"] = pd.to_datetime(df["match_date"], dayfirst=True, format="mixed").dt.date
    df["kickoff"] = df["kickoff"].astype("string")
    df["referee"] = df["referee"].astype("string")
    for c in ("home", "away", "league"):
        df[c] = df[c].astype("string").str.strip()
    for c in INT_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    num = [c for c in df.columns if c.startswith("odds_")] + ["hxg", "axg"]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Float64")
    if season is not None:
        df["season"] = season
        df["status"] = "FINISHED"
    return df.reset_index(drop=True)


def fetch_season(league: str, season: str, etag: str | None = None, last_modified: str | None = None
                 ) -> tuple[pd.DataFrame | None, FetchResult]:
    """返回 (DataFrame 或 None[未变化], 请求结果)。"""
    base = _setup()
    res = client().get(f"{base}/mmz4281/{season}/{league}.csv", etag=etag, last_modified=last_modified)
    if res.not_modified:
        return None, res
    df = normalize(_read_csv(res.content), season)
    df = df[df["hg"].notna() & df["ag"].notna()]  # 只保留已完赛
    return df.reset_index(drop=True), res


def fetch_fixtures(leagues: list[str]) -> pd.DataFrame:
    """近期赛程 + 赛前赔率（实时快照）。"""
    base = _setup()
    res = client().get(f"{base}/fixtures.csv")
    raw = _read_csv(res.content)
    df = normalize(raw, season=None)
    df = df[df["league"].isin(leagues)]
    keep = ["league", "match_date", "kickoff", "home", "away", "referee",
            "odds_avg_h", "odds_avg_d", "odds_avg_a", "odds_max_h", "odds_max_d", "odds_max_a",
            "odds_avg_o25", "odds_avg_u25"]
    return df[keep].reset_index(drop=True)

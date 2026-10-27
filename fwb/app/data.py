"""应用端数据访问。缓存键包含 manifest 的 data_version：Actions 推送新数据后缓存自动失效。"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from fwb.settings import DATA_DIR
from fwb.storage import Store


def data_version() -> str:
    p = DATA_DIR / "manifest.json"
    if not p.exists():
        return "empty"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("data_version", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


@st.cache_data(show_spinner=False)
def _read(name: str, version: str, **filters) -> pd.DataFrame:  # noqa: ARG001  version 仅用作缓存键
    return Store().read(name, **filters)


def table(name: str, **filters) -> pd.DataFrame:
    return _read(name, data_version(), **filters)


def manifest_key() -> str:
    """manifest 的缓存键用文件修改时间 + 大小：数据无变化的运行也会更新运行日志，需要反映到新鲜度指示上。"""
    p = DATA_DIR / "manifest.json"
    try:
        s = p.stat()
        return f"{s.st_mtime_ns}-{s.st_size}"
    except OSError:
        return "missing"


@st.cache_data(show_spinner=False)
def manifest(key: str) -> dict:  # noqa: ARG001  key 仅用作缓存键
    return Store().manifest


@st.cache_data(ttl=60, show_spinner=False, max_entries=4)
def live_matches(date_from: str, date_to: str) -> tuple[pd.DataFrame, str]:
    """实时比分（football-data.org）。cache_data 在所有访客之间共享：60 秒内最多请求一次 API。"""
    from datetime import datetime, timezone

    from fwb.sources.football_data_org import fetch_live

    return fetch_live(date_from, date_to), datetime.now(timezone.utc).isoformat()


@st.cache_data(show_spinner="正在获取 StatsBomb 比赛事件…", ttl=7 * 24 * 3600, max_entries=64)
def sb_events(match_id: int) -> pd.DataFrame:
    from fwb.analytics.events import flatten
    from fwb.sources import statsbomb_open

    return flatten(statsbomb_open.events(match_id))


@st.cache_data(show_spinner="正在获取 StatsBomb 比赛列表…", ttl=7 * 24 * 3600)
def sb_match_index(competition_id: int, season_id: int) -> pd.DataFrame:
    idx = table("sb_matches", competition_id=competition_id, season_id=season_id)
    if not idx.empty:
        return idx
    from fwb.sources import statsbomb_open

    return statsbomb_open.matches(competition_id, season_id)

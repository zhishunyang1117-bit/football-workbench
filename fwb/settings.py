"""配置加载与赛季代码工具函数。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# 用 abspath 而非 resolve()：某些 Windows 目录（如应用虚拟化目录）resolve 后是 \\?\ 前缀路径，子进程无法作为工作目录
ROOT = Path(os.path.abspath(__file__)).parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("FWB_DATA_DIR", ROOT / "data"))


@lru_cache(maxsize=1)
def load_settings() -> dict[str, Any]:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def leagues() -> dict[str, dict[str, Any]]:
    return load_settings()["leagues"]


def current_season(today: date | None = None) -> str:
    """配置为 auto 时按日期推断：7 月 1 日起视为新赛季。"""
    v = str(load_settings()["seasons"]["current"])
    if v == "auto":
        d = today or date.today()
        return Season.from_start_year(d.year if d.month >= 7 else d.year - 1).code
    return v


def all_seasons(today: date | None = None) -> list[str]:
    """当季 + 历史赛季。history 为 auto 时取当季之前的 3 个赛季。"""
    s = load_settings()["seasons"]
    cur = current_season(today)
    if str(s["history"]) == "auto":
        start = Season(cur).start_year
        history = [Season.from_start_year(y).code for y in range(start - 3, start)]
    else:
        history = [str(x) for x in s["history"]]
    return sorted({*history, cur})


def source_cfg(name: str) -> dict[str, Any]:
    return load_settings()["sources"][name]


def model_cfg(name: str) -> dict[str, Any]:
    return load_settings()["models"][name]


def display_timezone() -> str:
    return load_settings().get("app", {}).get("display_timezone", "Asia/Shanghai")


SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"
TOKEN_KEY = "FOOTBALL_DATA_ORG_TOKEN"


def fdorg_token() -> str | None:
    """football-data.org token：优先环境变量，其次 .streamlit/secrets.toml（与 st.secrets 同一文件）。

    token 只在请求头中使用，永不写入日志、数据文件或页面。
    """
    tok = os.environ.get(TOKEN_KEY, "").strip()
    if tok:
        return tok
    try:
        import tomllib

        with open(SECRETS_FILE, "rb") as f:
            tok = str(tomllib.load(f).get(TOKEN_KEY, "")).strip()
    except (OSError, ValueError):
        return None
    return tok if tok and "在这里粘贴" not in tok else None


@dataclass(frozen=True)
class Season:
    """赛季代码转换。code 为 football-data.co.uk 格式，如 "2627"。"""

    code: str

    @property
    def start_year(self) -> int:
        yy = int(self.code[:2])
        return 2000 + yy if yy < 90 else 1900 + yy

    @property
    def label(self) -> str:  # 2026/27
        return f"{self.start_year}/{str(self.start_year + 1)[-2:]}"

    @property
    def openfootball(self) -> str:  # 2026-27
        return f"{self.start_year}-{str(self.start_year + 1)[-2:]}"

    @property
    def fdorg(self) -> int:  # football-data.org 的 season 参数 = 起始年份
        return self.start_year

    @classmethod
    def from_start_year(cls, year: int) -> "Season":
        return cls(f"{year % 100:02d}{(year + 1) % 100:02d}")

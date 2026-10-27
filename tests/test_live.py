"""实时比分解析、token 读取、分享链接识别（离线）。"""

import pandas as pd

from fwb import settings
from fwb.serve import URL_RE
from fwb.sources import football_data_org as fdorg

PAYLOAD = {"matches": [
    {"id": 2, "utcDate": "2026-09-27T15:30:00Z", "status": "IN_PLAY", "minute": 67,
     "competition": {"code": "BL1"}, "homeTeam": {"name": "FC Bayern München", "shortName": "Bayern"},
     "awayTeam": {"name": "Borussia Dortmund", "shortName": "Dortmund"},
     "score": {"fullTime": {"home": 2, "away": 1}, "halfTime": {"home": 1, "away": 1}}},
    {"id": 1, "utcDate": "2026-09-27T13:00:00Z", "status": "FINISHED", "competition": {"code": "PL"},
     "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Leeds United FC", "shortName": "Leeds"},
     "score": {"fullTime": {"home": 3, "away": 0}, "halfTime": {"home": 1, "away": 0}}},
    {"id": 3, "utcDate": "2026-09-28T19:00:00Z", "status": "TIMED", "competition": {"code": "SA"},
     "homeTeam": {"shortName": "Inter"}, "awayTeam": {"shortName": "Milan"},
     "score": {"fullTime": {"home": None, "away": None}, "halfTime": {"home": None, "away": None}}},
]}


def test_parse_live_sorted_and_typed():
    df = fdorg.parse_live(PAYLOAD)
    assert list(df["fdorg_id"]) == [1, 2, 3]  # 按开球时间排序
    assert df.loc[0, "home"] == "Arsenal FC" and df.loc[1, "home"] == "Bayern"  # 优先简称
    assert df.loc[1, "minute"] == 67 and df.loc[1, "hg"] == 2
    assert pd.isna(df.loc[2, "hg"]) and str(df["utc_kickoff"].dt.tz) == "UTC"
    assert fdorg.parse_live({"matches": []}).empty


def test_token_from_env_or_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv(settings.TOKEN_KEY, raising=False)
    f = tmp_path / "secrets.toml"
    monkeypatch.setattr(settings, "SECRETS_FILE", f)
    assert settings.fdorg_token() is None
    f.write_text('FOOTBALL_DATA_ORG_TOKEN = "在这里粘贴你的token"\n', encoding="utf-8")
    assert settings.fdorg_token() is None  # 模板占位符不算
    f.write_text('FOOTBALL_DATA_ORG_TOKEN = "abc123"\n', encoding="utf-8")
    assert settings.fdorg_token() == "abc123"
    monkeypatch.setenv(settings.TOKEN_KEY, "fromenv")
    assert settings.fdorg_token() == "fromenv"


def test_share_url_regex():
    line = "2026-09-26T23:00:00Z INF |  https://happy-fox-alpha-beta.trycloudflare.com                  |"
    assert URL_RE.search(line).group(0) == "https://happy-fox-alpha-beta.trycloudflare.com"
    assert URL_RE.search("POST https://api.trycloudflare.com/tunnel") is None  # 接口地址不能当成分享链接

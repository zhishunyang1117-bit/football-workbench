"""多源合并 → 规范 matches 表。

优先级（同一 league/season/home/away 视为同一场比赛，联赛内每对主客组合每季唯一）：
- 已完赛比分与技术统计/赔率/xG：football-data.co.uk（最全）
- football-data.co.uk 尚未收录的比赛：football-data.org（实时，若启用）> openfootball
- 未开赛比赛的日期/开球时间：football-data.org（若启用，改期信息最新）> openfootball
队名无法解析的行不进入规范表（避免同一球队出现两个名字导致积分榜错误），在运行摘要中报警。
"""

from __future__ import annotations

import pandas as pd

from fwb.sources.football_data_uk import COLUMNS
from fwb.teams import resolve

KEY = ["league", "season", "home", "away"]


def resolve_names(df: pd.DataFrame | None, canonical: set[str] | None, strict: bool
                  ) -> tuple[pd.DataFrame | None, list[str]]:
    """把 home_raw/away_raw 映射为规范名。每次运行都重新解析，别名表的修改立即生效（即使远端 304）。

    strict=True：canonical 来自当季 co.uk，无法解析的行标记 unresolved（合并时丢弃并告警）。
    strict=False：当季 co.uk 尚无数据（新赛季初），canonical 用上季队名兜底；
                  无法解析的（多为升班马）保留原名、不丢弃，co.uk 收录后会自动改用规范名。
    """
    if df is None or df.empty:
        return df, []
    names = sorted(set(df["home_raw"].dropna()) | set(df["away_raw"].dropna()))
    mapping = {n: (resolve(n, canonical) if canonical else None) for n in names}
    bad = [n for n, v in mapping.items() if v is None]
    out = df.copy()
    out["home"] = out["home_raw"].map(lambda n: mapping.get(n) or n).astype("string")
    out["away"] = out["away_raw"].map(lambda n: mapping.get(n) or n).astype("string")
    if strict:
        out["unresolved"] = out["home_raw"].isin(bad) | out["away_raw"].isin(bad)
    else:
        out["unresolved"] = False
    return out, bad
FDORG_STATUS = {"FINISHED": "FINISHED", "AWARDED": "FINISHED", "IN_PLAY": "IN_PLAY", "PAUSED": "IN_PLAY",
                "SCHEDULED": "SCHEDULED", "TIMED": "SCHEDULED", "POSTPONED": "POSTPONED",
                "SUSPENDED": "SUSPENDED", "CANCELLED": "CANCELLED"}


def merge(fduk: pd.DataFrame, of: pd.DataFrame, fdorg: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    report = {"unresolved_openfootball": 0, "unresolved_fdorg": 0, "score_conflicts": 0}
    cols = list(dict.fromkeys([*COLUMNS.values(), "season", "status"]))
    base = fduk[cols].copy() if not fduk.empty else pd.DataFrame(columns=cols)
    base["result_source"] = "football-data.co.uk"

    def unresolved(df: pd.DataFrame) -> pd.Series:
        return df["unresolved"].fillna(False).astype(bool) if "unresolved" in df else pd.Series(False, index=df.index)

    extra_sources = []
    if of is not None and not of.empty:
        bad = unresolved(of)
        report["unresolved_openfootball"] = int(bad.sum())
        extra_sources.append(("openfootball", of[~bad]))
    if fdorg is not None and not fdorg.empty:
        bad = unresolved(fdorg)
        report["unresolved_fdorg"] = int(bad.sum())
        f = fdorg[~bad].copy()
        f["status"] = f["status"].map(FDORG_STATUS).fillna("SCHEDULED")
        f["kickoff"] = pd.to_datetime(f["utc_kickoff"], utc=True).dt.tz_convert("Europe/London").dt.strftime("%H:%M")
        extra_sources.append(("football-data.org", f))

    merged = base.set_index(KEY)
    rank = {"football-data.co.uk": 0, "football-data.org": 1, "openfootball": 2}
    # 按优先级从高到低处理；低优先级来源不会覆盖高优先级来源写入的行
    for name, src in sorted(extra_sources, key=lambda x: rank[x[0]]):
        s = src.set_index(KEY)
        both = s.index.intersection(merged.index)
        fin_both = [k for k in both if merged.at[k, "status"] == "FINISHED" and s.at[k, "status"] == "FINISHED"]
        report["score_conflicts"] += sum(
            int(merged.at[k, "hg"] != s.at[k, "hg"] or merged.at[k, "ag"] != s.at[k, "ag"]) for k in fin_both
        )
        # 已存在、未完赛、且由更低优先级来源写入的行：用当前来源更新状态/比分/日期
        upd = [k for k in both if merged.at[k, "status"] != "FINISHED"
               and rank[merged.at[k, "result_source"]] > rank[name]]
        for k in upd:
            for c in ("status", "hg", "ag", "hthg", "htag", "match_date", "kickoff"):
                if c in s.columns and pd.notna(s.at[k, c]):
                    merged.at[k, c] = s.at[k, c]
            merged.at[k, "result_source"] = name
        new = s.loc[s.index.difference(merged.index)].copy()
        if not new.empty:
            new = new[[c for c in ("match_date", "kickoff", "status", "hg", "ag", "hthg", "htag") if c in new]]
            new["result_source"] = name
            merged = pd.concat([merged, new])
    out = merged.reset_index()
    for c in ("hg", "ag", "hthg", "htag"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    for c in ("status", "result_source", "kickoff", "home", "away", "league", "season"):
        out[c] = out[c].astype("string")
    out["match_date"] = pd.to_datetime(out["match_date"]).dt.date
    return out.sort_values(["match_date", "home"]).reset_index(drop=True), report

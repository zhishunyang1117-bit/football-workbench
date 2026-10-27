"""管道入口。

  python -m fwb.pipeline --mode incremental   # 日常：只处理当季；条件请求（304 即跳过）；重算衍生表
  python -m fwb.pipeline --mode full          # 兜底：重新下载全部配置赛季、重建规范表与全部回测
  python -m fwb.pipeline --mode statsbomb     # 一次性/偶发：StatsBomb 开放数据事件聚合（只处理新增或更新的比赛）

三类数据的处理策略：
- 实时（赛程、比赛状态、赛前赔率）：每次运行整体覆盖为最新快照（replace）。
- 历史（已完赛赛果、技术统计、收盘赔率、事件数据）：按赛季分区；往季冻结，只在 full 模式重拉；
  当季以"整季文件"替换分区以吸收事后修正，并受行数骤降保护。
- 衍生（Elo、Dixon-Coles 预测、回测、球员/球队聚合）：每次由原始数据重算，带 model_version，永不回写原始层。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timezone

import pandas as pd

from fwb.analytics import events as ev_mod
from fwb.analytics import models, players
from fwb.analytics.league import finished
from fwb.http import FETCH_ERRORS
from fwb.pipeline import curate, quality
from fwb.settings import Season, all_seasons, current_season, leagues, load_settings, model_cfg, source_cfg
from fwb.sources import football_data_org as fdorg
from fwb.sources import football_data_uk as fduk
from fwb.sources import openfootball, statsbomb_open
from fwb.storage import QualityError, Store

log = logging.getLogger("fwb.pipeline")


class Run:
    def __init__(self, mode: str):
        self.mode = mode
        self.started = datetime.now(timezone.utc).replace(microsecond=0)
        self.changes: dict[str, int] = {}
        self.warnings: list[str] = []
        self.requests_skipped = 0
        self.fetch_errors = 0

    def record(self, changes: dict[str, int]) -> None:
        for k, v in changes.items():
            self.changes[k] = self.changes.get(k, 0) + v

    def warn(self, msg: str) -> None:
        log.warning(msg)
        self.warnings.append(msg)

    def fetch_failed(self, what: str, err: Exception) -> None:
        """单个数据源的抓取失败：记警告、计数，继续处理其他数据源（运行状态记为 partial）。"""
        self.fetch_errors += 1
        self.warn(f"{what} 获取失败：{type(err).__name__}: {err}")

    def summary(self, status: str, error: str | None = None) -> dict:
        if status == "success" and self.fetch_errors:
            status = "partial"
        return {
            "mode": self.mode, "status": status, "error": error,
            "started_at": self.started.isoformat(),
            "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "partitions_changed": len(self.changes), "row_delta": sum(self.changes.values()),
            "not_modified_skips": self.requests_skipped, "fetch_errors": self.fetch_errors,
            "warnings": self.warnings[:50],
        }


def _conditional(store: Store, table: str, source: str, key: str, incremental: bool, **part) -> dict:
    """条件请求用的水位线；本地分区文件不存在时忽略水位线，强制完整拉取（避免 304 掩盖本地数据缺失）。"""
    if not incremental or not store.has_partition(table, **part):
        return {}
    return store.watermark(source, key)


# ------------------------------------------------------------------ 1. 采集
def ingest(store: Store, run: Run, league_ids: list[str], seasons: list[str], incremental: bool, cur: str) -> None:
    drop = load_settings()["update"]["max_row_drop_ratio"]
    lcfg = leagues()

    if source_cfg("football_data_uk")["enabled"]:
        for lg in league_ids:
            for s in seasons:
                key = f"{lg}/{s}"
                wm = _conditional(store, "fduk_matches", "football_data_uk", key, incremental, league=lg, season=s)
                try:
                    df, res = fduk.fetch_season(lg, s, etag=wm.get("etag"), last_modified=wm.get("last_modified"))
                except FETCH_ERRORS as e:
                    run.fetch_failed(f"football-data.co.uk {key}", e)
                    continue
                if df is None:
                    run.requests_skipped += 1
                    store.set_watermark("football_data_uk", key)
                    continue
                if df.empty:
                    continue
                run.record(store.write("fduk_matches", df, mode="replace_partitions", guard_drop=drop))
                store.set_watermark("football_data_uk", key, etag=res.etag, last_modified=res.last_modified,
                                   max_date=str(df["match_date"].max()), rows=len(df))
        try:
            fx = fduk.fetch_fixtures(list(lcfg))
            run.record(store.write("fduk_fixtures", fx, mode="replace_table"))
        except FETCH_ERRORS as e:
            run.fetch_failed("football-data.co.uk fixtures.csv", e)

    if source_cfg("openfootball")["enabled"]:
        for lg in league_ids:
            key = f"{lg}/{cur}"
            wm = _conditional(store, "openfootball_matches", "openfootball", key, incremental, league=lg, season=cur)
            try:
                df, res = openfootball.fetch_season(lg, lcfg[lg]["openfootball"], cur, lcfg[lg]["timezone"],
                                                    etag=wm.get("etag"))
            except FETCH_ERRORS as e:
                run.fetch_failed(f"openfootball {key}", e)
                continue
            if df is None:
                run.requests_skipped += 1
                store.set_watermark("openfootball", key)
                continue
            if df.empty:
                continue
            run.record(store.write("openfootball_matches", df, mode="replace_partitions", guard_drop=drop))
            store.set_watermark("openfootball", key, etag=res.etag, rows=len(df))

    if fdorg.enabled():
        for lg in league_ids:
            code = lcfg[lg]["fdorg"]
            try:
                m = fdorg.fetch_matches(lg, code, cur)
                if not m.empty:
                    run.record(store.write("fdorg_matches", m, mode="replace_partitions", guard_drop=drop))
                sc = fdorg.fetch_scorers(lg, code, cur)
                if not sc.empty:
                    run.record(store.write("fdorg_scorers", sc, mode="replace_partitions"))
                store.set_watermark("football_data_org", f"{lg}/{cur}", rows=len(m))
            except FETCH_ERRORS as e:
                run.fetch_failed(f"football-data.org {lg}", e)


# ------------------------------------------------------------------ 2. 规范化
def _teams(df: pd.DataFrame) -> set[str]:
    return (set(df["home"]) | set(df["away"])) if not df.empty else set()


def build_curated(store: Store, run: Run, league_ids: list[str], seasons: list[str]) -> None:
    drop = load_settings()["update"]["max_row_drop_ratio"]
    for lg in league_ids:
        for s in seasons:
            a = store.read("fduk_matches", league=lg, season=s)
            b = store.read("openfootball_matches", league=lg, season=s)
            c = store.read("fdorg_matches", league=lg, season=s)
            if a.empty and b.empty and c.empty:
                continue
            canon, strict = _teams(a), True
            if not canon:  # 新赛季初：co.uk 尚无当季数据，用上季队名兜底解析，未匹配的保留原名
                prev = Season.from_start_year(Season(s).start_year - 1).code
                canon, strict = _teams(store.read("fduk_matches", league=lg, season=prev)), False
                log.info("%s/%s：co.uk 尚无当季数据，暂用上季队名解析（升班马保留原名）", lg, s)
            b, bad_b = curate.resolve_names(b, canon, strict)
            c, bad_c = curate.resolve_names(c, canon, strict)
            if strict and (bad_b or bad_c):
                run.warn(f"{lg}/{s}: 队名未能映射、相关比赛未进入规范表（请在 config/team_aliases.csv 补充）："
                         f"{sorted(set(bad_b) | set(bad_c))[:10]}")
            merged, rep = curate.merge(a, b, c)
            if rep["score_conflicts"]:
                run.warn(f"{lg}/{s}: {rep['score_conflicts']} 场比赛各来源比分不一致（以 football-data.co.uk 为准）")
            for w in quality.check_matches(merged, lg, s):
                run.warn(w)
            run.record(store.write("matches", merged, mode="replace_partitions", guard_drop=drop))


# ------------------------------------------------------------------ 3. 衍生
def derive(store: Store, run: Run, league_ids: list[str], full: bool, today: date) -> None:
    dc_cfg, elo_cfg = model_cfg("dixon_coles"), model_cfg("elo")
    horizon = load_settings()["update"]["horizon_days"]
    seasons = all_seasons(today)
    for lg in league_ids:
        m = store.read("matches", league=lg)
        if m.empty:
            continue
        _, hist = models.SeasonElo(**elo_cfg).run(finished(m), models.season_teams(m))
        run.record(store.write("elo_history", hist, mode="replace_partitions"))

        # 预测按联赛分区写入：只动本次处理的联赛（--leagues 时不影响其他联赛）
        fx = store.read("fduk_fixtures", league=lg)
        p = models.predict_upcoming(m, fx, dc_cfg, elo_cfg, today, horizon)
        if p.empty:
            run.record(store.delete_partition("predictions", league=lg))
        else:
            quality.check_probabilities(p)
            run.record(store.write("predictions", p, mode="replace_partitions"))

        test_seasons = seasons[1:] if full else [current_season(today)]
        bt = models.backtest(m, test_seasons, dc_cfg, elo_cfg)
        if bt.attrs.get("dc_failures"):
            run.warn(f"{lg}: 回测中 {bt.attrs['dc_failures']} 场 Dixon-Coles 预测因数值问题跳过（不影响其余样本）")
        if not bt.empty:
            quality.check_probabilities(bt)
            run.record(store.write("backtest", bt, mode="replace_partitions" if full else "upsert"))


# ------------------------------------------------------------------ StatsBomb 回填
def statsbomb_backfill(store: Store, run: Run, max_matches: int | None, rebuild: bool = False) -> None:
    cfg = source_cfg("statsbomb_open_data")
    if not cfg["enabled"]:
        return
    budget = max_matches if max_matches is not None else 10**9
    for cid, sid in cfg["competitions"]:
        try:
            idx = statsbomb_open.matches(cid, sid)
        except FETCH_ERRORS as e:
            run.fetch_failed(f"StatsBomb 比赛列表 {cid}/{sid}", e)
            continue
        run.record(store.write("sb_matches", idx, mode="replace_partitions"))
        done = store.read("sb_team_match", competition_id=cid, season_id=sid)
        seen = dict(zip(done["match_id"], done["last_updated"])) if not (done.empty or rebuild) else {}
        todo = idx[[seen.get(r.match_id) != r.last_updated for r in idx.itertuples()]]
        log.info("StatsBomb %s/%s：共 %d 场，待处理 %d 场", cid, sid, len(idx), len(todo))
        tm_buf, pm_buf = [], []
        for r in todo.itertuples(index=False):
            if budget <= 0:
                break
            budget -= 1
            try:
                e = ev_mod.flatten(statsbomb_open.events(r.match_id))
                lu = statsbomb_open.lineups(r.match_id)
            except FETCH_ERRORS as err:
                run.fetch_failed(f"StatsBomb 比赛 {r.match_id}", err)
                continue
            # 事件文件里的队名可能与比赛索引不同（如 'Caen' vs 'Stade Malherbe Caen'），一律按 team_id 关联
            names = {r.home_team_id: r.home, r.away_team_id: r.away}
            try:
                tm = ev_mod.team_match(e, r.home, r.away, r.home_team_id, r.away_team_id)
            except ValueError as err:
                run.warn(f"StatsBomb 比赛 {r.match_id} 跳过：{err}")
                continue
            meta = {"match_id": r.match_id, "competition_id": cid, "season_id": sid}
            tm_buf.append(tm.assign(**meta, match_date=r.match_date, last_updated=r.last_updated))
            pm = ev_mod.player_match(e, lu)
            pm["team"] = pm["team_id"].map(names).fillna(pm["team"])
            pm_buf.append(pm.assign(**meta))
            if len(tm_buf) >= 50:
                _flush_sb(store, run, tm_buf, pm_buf)
        _flush_sb(store, run, tm_buf, pm_buf)
    pm = store.read("sb_player_match")
    if not pm.empty:
        run.record(store.write("sb_player_season", players.season_table(pm), mode="replace_table"))


def _flush_sb(store: Store, run: Run, tm_buf: list, pm_buf: list) -> None:
    if tm_buf:
        run.record(store.write("sb_team_match", pd.concat(tm_buf, ignore_index=True)))
        run.record(store.write("sb_player_match", pd.concat(pm_buf, ignore_index=True)))
        store.save_manifest()  # 检查点：长任务中断时已处理的比赛不丢
        tm_buf.clear()
        pm_buf.clear()

# ------------------------------------------------------------------ main
def _step_summary(summary: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [f"## 数据管道：{summary['mode']} — {summary['status']}", "",
             f"- 变更分区：{summary['partitions_changed']}，行数净变化：{summary['row_delta']:+d}",
             f"- 304 未变化跳过：{summary['not_modified_skips']}，抓取失败：{summary.get('fetch_errors', 0)}"]
    if summary.get("error"):
        lines.append(f"- 错误：`{summary['error']}`")
    if summary["warnings"]:
        lines += ["", "### 警告"] + [f"- {w}" for w in summary["warnings"]]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fwb.pipeline", description="足球分析工作台数据管道")
    ap.add_argument("--mode", choices=["incremental", "full", "statsbomb"], default="incremental")
    ap.add_argument("--leagues", default="", help="逗号分隔，如 E0,SP1；默认全部")
    ap.add_argument("--max-matches", type=int, default=None, help="statsbomb 模式本次最多处理的比赛数")
    ap.add_argument("--rebuild", action="store_true", help="statsbomb 模式：忽略已处理记录，全部重算（指标口径变更后使用）")
    ap.add_argument("--today", default=None, help="覆盖'今天'（YYYY-MM-DD），用于复现/测试")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    league_ids = [x for x in args.leagues.split(",") if x] or list(leagues())
    unknown = set(league_ids) - set(leagues())
    if unknown:
        ap.error(f"未知联赛：{unknown}")
    today = date.fromisoformat(args.today) if args.today else date.today()
    store = Store()
    run = Run(args.mode)
    t0 = time.monotonic()
    try:
        if args.mode == "statsbomb":
            statsbomb_backfill(store, run, args.max_matches, rebuild=args.rebuild)
        else:
            full = args.mode == "full"
            cur = current_season(today)
            seasons = all_seasons(today) if full else [cur]
            ingest(store, run, league_ids, seasons, incremental=not full, cur=cur)
            build_curated(store, run, league_ids, seasons)
            derive(store, run, league_ids, full, today)
        summary = run.summary("success")
        code = 0
    except QualityError as e:
        log.error("质量门失败：%s", e)
        summary, code = run.summary("quality_failed", str(e)), 2
    except Exception as e:  # noqa: BLE001
        log.exception("管道异常")
        summary, code = run.summary("error", repr(e)), 1
    summary["duration_s"] = round(time.monotonic() - t0, 1)
    store.log_run(summary)
    store.save_manifest()
    _step_summary(summary)
    log.info("完成：%s，变更分区 %d，耗时 %.1fs", summary["status"], summary["partitions_changed"], summary["duration_s"])
    return code


if __name__ == "__main__":
    sys.exit(main())

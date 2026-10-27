"""存储层：Hive 风格分区的 Parquet + manifest.json。

设计要点
- Parquet 是唯一的规范存储（不用 .duckdb/.sqlite 单文件：每次更新都会整体重写、在 git 中无限膨胀）。
- 写入确定性：固定列序、按主键排序、zstd 压缩；内容哈希不变则不重写文件，git 不产生无意义 diff。
- manifest.json 记录每个分区的行数/哈希、各数据源水位线（ETag/Last-Modified/最大日期）、运行日志，
  既是数据版本号，也是质量门比较"上一版本"的依据。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fwb.settings import DATA_DIR

SCHEMA_VERSION = 1
DataClass = Literal["realtime", "historical", "derived"]


@dataclass(frozen=True)
class TableSpec:
    layer: Literal["raw", "curated", "derived"]
    key: tuple[str, ...]
    partitions: tuple[str, ...]
    data_class: DataClass
    description: str


# 表注册表：页面"数据状态"和质量门都从这里读元信息
TABLES: dict[str, TableSpec] = {
    "fduk_matches": TableSpec("raw", ("league", "season", "home", "away"), ("league", "season"), "historical",
                              "football-data.co.uk 赛果 + 技术统计 + 赛前/收盘赔率（2026/27 起含 xG）"),
    "fduk_fixtures": TableSpec("raw", ("league", "home", "away", "match_date"), ("league",), "realtime",
                               "football-data.co.uk 近期赛程 + 赛前赔率（快照，每次覆盖）"),
    "openfootball_matches": TableSpec("raw", ("league", "season", "home", "away"), ("league", "season"), "realtime",
                                      "openfootball 完整赛程与比分（CC0），用于未来赛程"),
    "fdorg_matches": TableSpec("raw", ("league", "season", "fdorg_id"), ("league", "season"), "realtime",
                               "football-data.org 比赛状态/比分（可选，需免费 token）"),
    "fdorg_scorers": TableSpec("raw", ("league", "season", "player_id"), ("league", "season"), "realtime",
                               "football-data.org 射手榜快照（可选）"),
    "matches": TableSpec("curated", ("league", "season", "home", "away"), ("league", "season"), "historical",
                         "规范化比赛表：多源合并、统一队名、统一状态"),
    "elo_history": TableSpec("derived", ("league", "match_date", "team"), ("league",), "derived",
                             "赛后 Elo 评分轨迹"),
    "predictions": TableSpec("derived", ("league", "home", "away", "match_date"), ("league",), "derived",
                             "未来赛程的 Dixon-Coles / Elo 概率与市场隐含概率"),
    "backtest": TableSpec("derived", ("league", "season", "home", "away"), ("league",), "derived",
                          "滚动回测：逐周重训后的样本外预测与真实结果"),
    "sb_matches": TableSpec("raw", ("match_id",), ("competition_id", "season_id"), "historical",
                            "StatsBomb Open Data 比赛索引"),
    "sb_player_season": TableSpec("derived", ("competition_id", "season_id", "player_id", "team"),
                                  ("competition_id", "season_id"), "derived",
                                  "由 StatsBomb 事件聚合的球员赛季指标（每 90 分钟）"),
    "sb_team_match": TableSpec("derived", ("match_id", "team"), ("competition_id", "season_id"), "derived",
                               "由 StatsBomb 事件聚合的球队单场战术指标（PPDA、xT、xG 等）"),
    "sb_player_match": TableSpec("derived", ("match_id", "player_id"), ("competition_id", "season_id"), "derived",
                                 "由 StatsBomb 事件聚合的球员单场指标（赛季表的来源）"),
}


class QualityError(RuntimeError):
    """质量门失败：拒绝写入，保持上一版本数据不变。"""


def _atomic_replace(src: Path, dst: Path, attempts: int = 10) -> None:
    """os.replace 是原子的；Windows 上若目标正被读者打开会报 PermissionError，短暂重试即可。"""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))
        except OSError as e:
            # 跨磁盘（Windows WinError 17 / POSIX EXDEV，如被虚拟化的目录）：退化为复制后删除
            if getattr(e, "winerror", None) != 17 and e.errno != 18:
                raise
            shutil.copyfile(src, dst)
            os.remove(src)
            return


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def content_hash(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update("|".join(f"{c}:{df[c].dtype}" for c in df.columns).encode())
    if len(df):
        h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()[:16]


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or DATA_DIR)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = self._load_manifest()

    # ---------------- manifest ----------------
    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {"schema_version": SCHEMA_VERSION, "partitions": {}, "watermarks": {}, "runs": []}

    def save_manifest(self) -> None:
        parts = self.manifest["partitions"]
        digest = hashlib.sha256("".join(f"{k}{v['hash']}" for k, v in sorted(parts.items())).encode())
        self.manifest["data_version"] = digest.hexdigest()[:12]
        self.manifest["schema_version"] = SCHEMA_VERSION
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        _atomic_replace(tmp, self.manifest_path)

    def watermark(self, source: str, key: str) -> dict[str, Any]:
        return self.manifest["watermarks"].get(source, {}).get(key, {})

    def set_watermark(self, source: str, key: str, **values: Any) -> None:
        wm = self.manifest["watermarks"].setdefault(source, {}).setdefault(key, {})
        wm.update({k: v for k, v in values.items() if v is not None})
        wm["checked_at"] = _now()

    def log_run(self, run: dict[str, Any], keep: int = 30) -> None:
        self.manifest["runs"] = ([run] + self.manifest.get("runs", []))[:keep]
        self.manifest["last_run"] = run

    # ---------------- paths ----------------
    def table_dir(self, name: str) -> Path:
        spec = TABLES[name]
        return self.root / spec.layer / name

    def partition_path(self, name: str, values: dict[str, Any]) -> Path:
        spec = TABLES[name]
        p = self.table_dir(name)
        for col in spec.partitions:
            p = p / f"{col}={values[col]}"
        return p / "data.parquet"

    def _rel(self, path: Path) -> str:
        return path.parent.relative_to(self.root).as_posix()

    # ---------------- write ----------------
    def write(
        self,
        name: str,
        df: pd.DataFrame,
        mode: Literal["upsert", "replace_partitions", "replace_table"] = "upsert",
        guard_drop: float | None = None,
    ) -> dict[str, int]:
        """写表。返回 {partition: 行数变化}，未变化的分区不出现在结果中。

        guard_drop：若某分区新行数比 manifest 中上一版本少了超过该比例，抛出 QualityError 且不写该分区
        （防止数据源临时返回残缺文件时覆盖掉好数据）。
        """
        spec = TABLES[name]
        missing = [c for c in (*spec.key, *spec.partitions) if c not in df.columns]
        if missing:
            raise ValueError(f"{name} 缺少主键/分区列：{missing}")
        changes: dict[str, int] = {}
        groups = [] if df.empty else list(df.groupby(list(spec.partitions), sort=True))
        targets = [
            (self.partition_path(name, dict(zip(spec.partitions, vals if isinstance(vals, tuple) else (vals,)))), part)
            for vals, part in groups
        ]
        if mode == "replace_table":  # 快照表：删除本次没有出现的旧分区
            keep = {self._rel(path) for path, _ in targets}
            prefix = f"{spec.layer}/{name}/"
            for rel in [k for k in self.manifest["partitions"] if k.startswith(prefix) and k not in keep]:
                shutil.rmtree(self.root / rel, ignore_errors=True)
                changes[rel] = -self.manifest["partitions"].pop(rel)["rows"]
        for path, part in targets:
            old_rows = self.manifest["partitions"].get(self._rel(path), {}).get("rows", 0)
            if mode == "upsert" and path.exists():
                existing = pd.read_parquet(path)
                part = pd.concat([existing, part], ignore_index=True)
            part = (
                part.drop_duplicates(subset=list(spec.key), keep="last")
                .sort_values(list(spec.key), kind="mergesort")
                .reset_index(drop=True)
            )
            if guard_drop is not None and old_rows and len(part) < old_rows * (1 - guard_drop):
                raise QualityError(f"{self._rel(path)} 行数从 {old_rows} 降到 {len(part)}，超过阈值 {guard_drop:.0%}")
            if self._write_partition(path, part):
                changes[self._rel(path)] = len(part) - old_rows
        return changes

    def delete_partition(self, name: str, **values: Any) -> dict[str, int]:
        """删除单个分区（例如某联赛本次没有任何未来赛程预测）。"""
        path = self.partition_path(name, values)
        rel = self._rel(path)
        meta = self.manifest["partitions"].pop(rel, None)
        if path.parent.exists():
            shutil.rmtree(path.parent, ignore_errors=True)
        return {rel: -meta["rows"]} if meta else {}

    def has_partition(self, name: str, **values: Any) -> bool:
        return self.partition_path(name, values).exists()

    def _write_partition(self, path: Path, df: pd.DataFrame) -> bool:
        rel = self._rel(path)
        h = content_hash(df)
        meta = self.manifest["partitions"].get(rel)
        if meta and meta["hash"] == h and path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, preserve_index=False)
        tmp = path.with_name(path.name + ".tmp")
        pq.write_table(table, tmp, compression="zstd", row_group_size=100_000)
        _atomic_replace(tmp, path)  # 应用同时在读：写临时文件再原子替换，读者永远看不到半个文件
        self.manifest["partitions"][rel] = {"rows": len(df), "hash": h, "updated_at": _now()}
        return True

    # ---------------- read ----------------
    def read(self, name: str, **filters: Any) -> pd.DataFrame:
        d = self.table_dir(name)
        if not d.exists():
            return pd.DataFrame()
        frames = []
        for f in sorted(d.rglob("data.parquet")):
            rel = f.parent.relative_to(d).as_posix()
            pvals = dict(seg.split("=", 1) for seg in rel.split("/") if "=" in seg)
            if any(str(pvals.get(k, "")) != str(v) for k, v in filters.items() if k in TABLES[name].partitions):
                continue
            frames.append(pd.read_parquet(f))
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        for k, v in filters.items():
            if k in df.columns and k not in TABLES[name].partitions:
                df = df[df[k] == v]
        return df.reset_index(drop=True)

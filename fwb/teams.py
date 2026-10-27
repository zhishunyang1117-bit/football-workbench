"""跨数据源队名归一化。

规范名 = football-data.co.uk 的队名（核心赛果/赔率/xG 均来自它）。
解析顺序（在"当赛季该联赛的规范队名集合"内查找，避免跨联赛误配）：
  1. 归一化后完全相等
  2. 别名表 config/team_aliases.csv（可手工维护）
  3. 词集包含：规范名的词集 ⊆ 外部名的词集，取词数最多者（如 "Newcastle United FC" → "Newcastle"）
  4. difflib 相似度 ≥ 0.85
解析失败返回 None，由质量门报告，绝不静默猜测。
"""

from __future__ import annotations

import csv
import difflib
import re
import unicodedata
from functools import lru_cache

from fwb.settings import CONFIG_DIR

# 去除的俱乐部法人/组织前后缀（纯噪声词）
_STOP = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "cfc", "rc", "rcd", "ogc", "sv", "tsg", "vfb", "vfl",
    "fsv", "bc", "sd", "ud", "cd", "ca", "club", "calcio", "de", "del", "la", "le", "acf", "osc", "sco", "aj",
    "and", "the", "football", "futbol", "balompie", "alsace", "hove", "albion",
}


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t not in _STOP and not t.isdigit()]
    return " ".join(tokens)


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    path = CONFIG_DIR / "team_aliases.csv"
    out: dict[str, str] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out[normalize(row["alias"])] = row["canonical"].strip()
    return out


def resolve(name: str, candidates: set[str] | list[str]) -> str | None:
    """把外部数据源的队名映射到 candidates（规范名集合）中的一个；失败返回 None。"""
    if not candidates:
        return None
    cands = {normalize(c): c for c in candidates}
    n = normalize(name)
    if n in cands:
        return cands[n]
    alias = _aliases().get(n)
    if alias and alias in set(candidates):
        return alias
    toks = set(n.split())
    subset = [(len(k.split()), c) for k, c in cands.items() if k and set(k.split()) <= toks]
    if subset:
        subset.sort(reverse=True)
        if len(subset) == 1 or subset[0][0] > subset[1][0]:
            return subset[0][1]
    best = difflib.get_close_matches(n, list(cands), n=1, cutoff=0.85)
    return cands[best[0]] if best else None


def resolve_many(names, candidates) -> dict[str, str | None]:
    cset = set(candidates)
    return {n: resolve(n, cset) for n in sorted(set(names))}

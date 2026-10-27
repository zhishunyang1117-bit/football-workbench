import pandas as pd
import pytest

from fwb.storage import QualityError, Store


def _df(n, league="E0", season="2627", offset=0):
    return pd.DataFrame({
        "league": league, "season": season,
        "home": [f"H{i + offset}" for i in range(n)], "away": [f"A{i + offset}" for i in range(n)],
        "hg": range(n),
    })


def test_upsert_is_idempotent(tmp_path):
    s = Store(tmp_path)
    assert s.write("matches", _df(3))
    assert s.write("matches", _df(3)) == {}  # 内容哈希不变 → 不重写
    assert len(s.read("matches")) == 3


def test_upsert_merges_by_key(tmp_path):
    s = Store(tmp_path)
    s.write("matches", _df(3))
    upd = _df(1).assign(hg=99)
    s.write("matches", pd.concat([upd, _df(1, offset=10)]))
    out = s.read("matches").set_index("home")
    assert len(out) == 4 and out.loc["H0", "hg"] == 99


def test_replace_partitions_and_filters(tmp_path):
    s = Store(tmp_path)
    s.write("matches", pd.concat([_df(3), _df(2, league="SP1")]))
    s.write("matches", _df(1), mode="replace_partitions")
    assert len(s.read("matches", league="E0")) == 1
    assert len(s.read("matches", league="SP1")) == 2


def test_replace_table_drops_missing_partitions(tmp_path):
    s = Store(tmp_path)
    df = pd.DataFrame({"league": ["E0", "SP1"], "home": ["a", "b"], "away": ["c", "d"],
                       "match_date": ["2026-10-01", "2026-10-02"]})
    s.write("predictions", df, mode="replace_table")
    s.write("predictions", df.iloc[:1], mode="replace_table")
    assert set(s.read("predictions")["league"]) == {"E0"}
    assert not (tmp_path / "derived/predictions/league=SP1").exists()


def test_guard_drop_blocks_truncated_source(tmp_path):
    s = Store(tmp_path)
    s.write("matches", _df(100))
    with pytest.raises(QualityError):
        s.write("matches", _df(50), mode="replace_partitions", guard_drop=0.05)
    assert len(s.read("matches")) == 100  # 旧数据保持不变


def test_scoped_partition_delete_and_conditional_watermark(tmp_path):
    from fwb.pipeline.run import _conditional

    s = Store(tmp_path)
    df = pd.DataFrame({"league": ["E0", "SP1"], "home": ["a", "b"], "away": ["c", "d"],
                       "match_date": ["2026-10-01", "2026-10-02"]})
    s.write("predictions", df, mode="replace_partitions")
    s.write("predictions", df.iloc[:1].assign(home="z"), mode="replace_partitions")  # 只处理 E0
    assert set(s.read("predictions")["league"]) == {"E0", "SP1"}  # 其他联赛不受影响
    assert s.delete_partition("predictions", league="SP1") == {"derived/predictions/league=SP1": -1}
    assert not s.has_partition("predictions", league="SP1")
    # 本地分区缺失时，条件请求不带 ETag（强制完整拉取）
    s.set_watermark("football_data_uk", "E0/2627", etag='"abc"')
    assert _conditional(s, "fduk_matches", "football_data_uk", "E0/2627", True, league="E0", season="2627") == {}
    s.write("fduk_matches", _df(1))
    assert _conditional(s, "fduk_matches", "football_data_uk", "E0/2627", True,
                        league="E0", season="2627")["etag"] == '"abc"'


def test_manifest_version_tracks_content(tmp_path):
    s = Store(tmp_path)
    s.write("matches", _df(2))
    s.save_manifest()
    v1 = s.manifest["data_version"]
    s.write("matches", _df(3), mode="replace_partitions")
    s.save_manifest()
    assert s.manifest["data_version"] != v1
    assert Store(tmp_path).manifest["data_version"] == s.manifest["data_version"]

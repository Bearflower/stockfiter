"""完成态容器日志归档的保留边界。"""

from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path


def make_archive(root: Path, stamp: datetime, suffix: str, extra: bool = False,
                 single: bool = False) -> Path:
    directory = root / f"{stamp:%Y%m%dT%H%M%SZ}-run-{suffix}-12345678"
    directory.mkdir()
    containers = ("stockfilter-kline",) if single else (
        "stockfilter-kline", "stockfilter-obpc", "stockfilter-eadvisor")
    for container in containers:
        with gzip.open(directory / f"{container}.log.gz", "wt") as output:
            output.write("old log\n")
    (directory / "manifest.json").write_text(json.dumps({
        "version": 1, "containers": [
            {"container": container, "file": f"{container}.log.gz"} for container in containers
        ],
    }))
    if extra:
        (directory / "unexpected.txt").write_text("保留")
    return directory


def test_prune_keeps_recent_two_and_unknown_files(tmp_path):
    from scripts.prune_container_archives import prune

    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    oldest = make_archive(tmp_path, now - timedelta(days=120), "aaa")
    unknown = make_archive(tmp_path, now - timedelta(days=110), "bbb", extra=True)
    protected = make_archive(tmp_path, now - timedelta(days=100), "ccc")
    newest = make_archive(tmp_path, now - timedelta(days=2), "ddd")
    removed = prune(tmp_path, now=now, retention_days=90, min_releases=2)
    assert removed == [oldest]
    assert not oldest.exists()
    assert unknown.exists() and (unknown / "unexpected.txt").exists()
    assert protected.exists() and newest.exists()


def test_prune_never_counts_partial_directory_as_release(tmp_path):
    from scripts.prune_container_archives import prune

    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    older = make_archive(tmp_path, now - timedelta(days=100), "aaa")
    newer = make_archive(tmp_path, now - timedelta(days=99), "bbb")
    partial = tmp_path / f"{now:%Y%m%dT%H%M%SZ}-run-ccc-12345678"
    partial.mkdir()
    (partial / "stockfilter-kline.log.gz").write_bytes(b"incomplete")
    assert prune(tmp_path, now=now, retention_days=90, min_releases=2) == []
    assert older.exists() and newer.exists() and partial.exists()


def test_single_container_manifest_is_not_a_complete_release(tmp_path):
    from scripts.prune_container_archives import _complete_archive

    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    single = make_archive(tmp_path, now, "single", single=True)
    assert not _complete_archive(single)

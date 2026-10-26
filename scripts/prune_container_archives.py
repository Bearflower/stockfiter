"""保守清理过期的完成态容器日志归档。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import shutil
import sys


NAME_RE = re.compile(r"(\d{8}T\d{6}Z)-[A-Za-z0-9_-]+-[0-9a-f]{8}")
CONTAINERS = {"stockfilter-kline", "stockfilter-obpc", "stockfilter-eadvisor"}


def _complete_archive(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir() or not NAME_RE.fullmatch(path.name):
        return False
    manifest_path = path / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != 1 or not isinstance(manifest.get("containers"), list):
            return False
        if len(manifest["containers"]) != len(CONTAINERS):
            return False
        expected = {"manifest.json"}
        seen = set()
        for entry in manifest["containers"]:
            container = entry["container"]
            filename = entry["file"]
            if container not in CONTAINERS or container in seen:
                return False
            if filename != f"{container}.log.gz":
                return False
            seen.add(container)
            expected.add(filename)
        if seen != CONTAINERS:
            return False
        if {entry.name for entry in path.iterdir()} != expected:
            return False
        for filename in expected - {"manifest.json"}:
            archive_file = path / filename
            if archive_file.is_symlink() or not archive_file.is_file():
                return False
            with gzip.open(archive_file, "rb") as content:
                while content.read(65536):
                    pass
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return False
    return True


def prune(
    root: Path,
    *,
    now: datetime | None = None,
    retention_days: int = 90,
    min_releases: int = 2,
) -> list[Path]:
    """只清除过期且不属于最近完整发布的受管目录。"""
    if retention_days < 1 or min_releases < 2:
        raise ValueError("保留天数至少为 1、保留轮次至少为 2")
    if not root.is_dir() or root.is_symlink():
        raise ValueError("归档根目录不存在或为符号链接")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    complete: list[tuple[datetime, Path]] = []
    for path in root.iterdir():
        if not _complete_archive(path):
            print(f"跳过非完成态或未知归档：{path}", file=sys.stderr)
            continue
        match = NAME_RE.fullmatch(path.name)
        assert match is not None
        stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        complete.append((stamp, path))
    complete.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    cutoff = now - timedelta(days=retention_days)
    removed = []
    for stamp, path in complete[min_releases:]:
        if stamp < cutoff:
            shutil.rmtree(path)
            removed.append(path)
    return removed


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3, 4):
        raise SystemExit("用法：prune_container_archives.py ROOT [DAYS] [MIN_RELEASES]")
    try:
        for path in prune(
            Path(sys.argv[1]),
            retention_days=int(sys.argv[2]) if len(sys.argv) >= 3 else 90,
            min_releases=int(sys.argv[3]) if len(sys.argv) >= 4 else 2,
        ):
            print(f"已清理过期归档：{path}")
    except (OSError, ValueError) as exc:
        print(f"日志归档清理失败：{exc}", file=sys.stderr)
        raise SystemExit(1)

"""原子归档重建前尚由 Docker 保留的三个容器日志。"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4


CONTAINERS = ("stockfilter-kline", "stockfilter-obpc", "stockfilter-eadvisor")
REVISION_RE = re.compile(r"[0-9a-f]{40}")


def docker_output(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True, timeout=30,
    )
    return result.stdout.strip()


def archive(root: Path, new_revision: str = "unknown", run_id: str = "manual") -> Path:
    """只有所有现有容器日志通过校验，才公布一个完成态归档目录。"""
    if new_revision != "unknown" and not REVISION_RE.fullmatch(new_revision):
        raise ValueError("新提交 SHA 非法")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("run_id 非法")
    root.mkdir(parents=True, exist_ok=True)
    existing = set(docker_output("ps", "-a", "--format", "{{.Names}}").splitlines())
    missing = set(CONTAINERS) - existing
    if missing:
        raise RuntimeError(f"旧容器缺失，拒绝不完整归档：{', '.join(sorted(missing))}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = root / f"{stamp}-{run_id}-{new_revision[:12]}-{uuid4().hex[:8]}"
    pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=root))
    entries = []
    try:
        for container in CONTAINERS:
            raw = docker_output(
                "inspect", "-f",
                '{{.Id}}|{{.Image}}|{{index .Config.Labels "org.opencontainers.image.revision"}}',
                container,
            )
            parts = raw.split("|")
            if len(parts) != 3 or not parts[0] or not parts[1]:
                raise ValueError(f"{container} 镜像元数据不完整")
            container_id, image_id, revision = parts
            if not REVISION_RE.fullmatch(revision):
                revision = "unknown"
            filename = f"{container}.log.gz"
            file_path = pending / filename
            timeout = int(os.environ.get("CONTAINER_ARCHIVE_DOCKER_TIMEOUT_SECONDS", "120"))
            if timeout <= 0:
                raise ValueError("Docker 日志归档超时必须大于 0")
            result = subprocess.run(
                ["docker", "logs", "--timestamps", container],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout, check=True,
            )
            with gzip.open(file_path, "wt", encoding="utf-8") as output:
                for raw_line in io.BytesIO(result.stdout):
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    output.write(
                        f"container={container} revision={revision} image={image_id} {line}\n"
                    )
            with gzip.open(file_path, "rb") as check:
                while check.read(65536):
                    pass
            entries.append({
                "container": container, "container_id": container_id,
                "image_id": image_id, "revision": revision, "file": filename,
            })
        manifest = {
            "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "new_revision": new_revision, "run_id": run_id, "containers": entries,
        }
        (pending / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        pending.rename(final)
    except BaseException:
        shutil.rmtree(pending)
        raise
    return final


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：archive_container_logs.py ARCHIVE_ROOT")
    try:
        result = archive(
            Path(sys.argv[1]), os.environ.get("DEPLOY_REVISION", "unknown"),
            os.environ.get("DEPLOY_RUN_ID", "manual"),
        )
        print(f"容器日志归档完成：{result}")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as exc:
        print(f"容器日志归档失败：{exc}", file=sys.stderr)
        raise SystemExit(1)

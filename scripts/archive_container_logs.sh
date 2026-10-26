#!/usr/bin/env bash
# 兼容旧入口；实际归档逻辑使用 Python 保证整次发布原子完成。
set -euo pipefail
exec python3 "$(dirname "$0")/archive_container_logs.py" "$@"

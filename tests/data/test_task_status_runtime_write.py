"""task_status 轻量写入的真实 PostgreSQL 集成测试。

只允许连接名称含 test/pytest 的显式 TEST_DB_NAME；未配置时跳过，严禁误写生产库。
每个用例使用独立临时 schema，并在 finally 中级联删除。
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import psycopg2
import pytest


def _is_explicit_test_database_name(database: str) -> bool:
    """仅接受 test/pytest 作为独立命名片段，避免 latest 等误命中。"""
    return bool(
        re.search(
            r"(?:^|[^a-z0-9])(?:test|pytest)(?:$|[^a-z0-9])",
            database.lower(),
        )
    )


def _test_db_params() -> dict:
    password = os.getenv("TEST_DB_PASSWORD")
    database = os.getenv("TEST_DB_NAME")
    if not password or not database:
        pytest.skip("未配置 TEST_DB_PASSWORD/TEST_DB_NAME，跳过 task_status 集成测试")
    if not _is_explicit_test_database_name(database):
        pytest.fail("TEST_DB_NAME 必须以独立片段 test/pytest 标识隔离数据库")
    return {
        "host": os.getenv("TEST_DB_HOST", "localhost"),
        "port": os.getenv("TEST_DB_PORT", "5432"),
        "database": database,
        "user": os.getenv("TEST_DB_USER", "postgres"),
        "password": password,
        "connect_timeout": 5,
    }


@pytest.mark.parametrize(
    "database",
    ["stockfilter_pytest", "pytest_stockfilter", "stockfilter-test"],
)
def test_explicit_test_database_name_accepts_safe_names(database):
    assert _is_explicit_test_database_name(database) is True


@pytest.mark.parametrize(
    "database",
    ["stockfilter", "latest_stockfilter", "contest", "production"],
)
def test_explicit_test_database_name_rejects_substring_collisions(database):
    assert _is_explicit_test_database_name(database) is False


@contextmanager
def _isolated_schema():
    params = _test_db_params()
    schema = f"pytest_task_status_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(**params)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
    env = {
        "DB_HOST": str(params["host"]),
        "DB_PORT": str(params["port"]),
        "DB_NAME": str(params["database"]),
        "DB_USER": str(params["user"]),
        "DB_PASSWORD": str(params["password"]),
        "DB_SCHEMA": schema,
    }
    try:
        with patch.dict(os.environ, env, clear=False):
            yield params, schema
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()


def _schema_connection(params: dict, schema: str):
    return psycopg2.connect(
        **params,
        options=f"-c search_path={schema} -c statement_timeout=5000",
    )


def test_old_three_column_table_migrates_and_jsonb_roundtrips():
    """旧三列表应幂等补列，运行期 UPSERT 必须真实写入 JSONB。"""
    from scripts.scheduler_kline import _ensure_task_status_table, _write_task_status

    with _isolated_schema() as (params, schema):
        conn = _schema_connection(params, schema)
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE task_status ("
                "task_name VARCHAR(50) PRIMARY KEY, "
                "last_completed_at TIMESTAMP, status VARCHAR(20))"
            )
        conn.commit()
        conn.close()

        _ensure_task_status_table(
            db_connect_timeout_seconds=2,
            db_query_timeout_seconds=5,
        )
        assert _write_task_status(
            "pytest_kline_update",
            "failed",
            "2026-10-22",
            {"completion_mode": "failed", "covered": 1682},
            db_connect_timeout_seconds=2,
            db_query_timeout_seconds=5,
            task_status_write_retries=2,
            task_status_write_retry_delay_seconds=0,
            task_status_process_terminate_grace_seconds=1,
        ) is True

        conn = _schema_connection(params, schema)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, target_date::text, details, pg_typeof(details)::text "
                "FROM task_status WHERE task_name=%s",
                ("pytest_kline_update",),
            )
            row = cur.fetchone()
        conn.close()
        assert row == (
            "failed",
            "2026-10-22",
            {"completion_mode": "failed", "covered": 1682},
            "jsonb",
        )


def test_row_lock_timeout_reconnects_and_succeeds_after_release():
    """首轮行锁超时后必须换新连接，锁释放后第二轮原子写入成功。"""
    from scripts.scheduler_kline import _ensure_task_status_table, _write_task_status

    with _isolated_schema() as (params, schema):
        _ensure_task_status_table(
            db_connect_timeout_seconds=2,
            db_query_timeout_seconds=5,
        )
        locker = _schema_connection(params, schema)
        with locker.cursor() as cur:
            cur.execute(
                "INSERT INTO task_status "
                "(task_name, last_completed_at, status, target_date, details) "
                "VALUES ('pytest_locked', NOW(), 'completed', '2026-10-21', '{}')"
            )
        locker.commit()
        with locker.cursor() as cur:
            cur.execute(
                "SELECT task_name FROM task_status "
                "WHERE task_name='pytest_locked' FOR UPDATE"
            )

        def _release_lock():
            time.sleep(1.2)
            locker.commit()
            locker.close()

        releaser = threading.Thread(target=_release_lock, daemon=True)
        releaser.start()
        try:
            result = _write_task_status(
                "pytest_locked",
                "failed",
                "2026-10-22",
                {"completion_mode": "failed"},
                db_connect_timeout_seconds=2,
                db_query_timeout_seconds=1,
                task_status_write_retries=2,
                task_status_write_retry_delay_seconds=1,
                task_status_process_terminate_grace_seconds=1,
            )
        finally:
            releaser.join(timeout=5)
            if not locker.closed:
                locker.rollback()
                locker.close()

        assert result is True
        conn = _schema_connection(params, schema)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, target_date::text FROM task_status "
                "WHERE task_name='pytest_locked'"
            )
            assert cur.fetchone() == ("failed", "2026-10-22")
        conn.close()


def test_monitor_coverage_sql_uses_stock_pool_intersection():
    """用真实 PostgreSQL 最小数据集证明池外指数和重复 K 线不进入覆盖分子。"""
    monitor_text = (
        Path(__file__).resolve().parents[2] / "scripts" / "monitor.sh"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"# 目标日 K 线覆盖：.*?cur\.execute\(\\\"\\\"\\\""
        r"(?P<sql>.*?)\\\"\\\"\\\", \(target,\)\)",
        monitor_text,
        flags=re.DOTALL,
    )
    assert match, "无法从 monitor.sh 提取覆盖率 SQL"

    with _isolated_schema() as (params, schema):
        conn = _schema_connection(params, schema)
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE stocks (code TEXT)")
            cur.execute(
                "CREATE TABLE klines (code TEXT, date DATE, frequency TEXT)"
            )
            cur.execute("INSERT INTO stocks(code) VALUES ('A'), ('B'), ('A')")
            cur.execute(
                "INSERT INTO klines(code, date, frequency) VALUES "
                "('A', '2026-10-22', 'd'), "
                "('A', '2026-10-22', 'd'), "
                "('B', '2026-10-21', 'd'), "
                "('B', '2026-10-22', '5'), "
                "('000300', '2026-10-22', 'd')"
            )
            sql = match.group("sql").replace(
                "schema_stockfilter.", f'"{schema}".'
            )
            cur.execute(sql, ("2026-10-22",))
            covered = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT code) FROM stocks")
            pool_count = cur.fetchone()[0]
        conn.close()

        assert covered == 1
        assert pool_count == 2

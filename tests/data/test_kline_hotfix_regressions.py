"""2026-10-23 生产采集阻塞的最小回归。"""

from unittest.mock import MagicMock, patch

from scripts.data.update_kline_daily import ChunkResult


def test_chunk_result_exposes_processed_count_for_progress_writer():
    result = ChunkResult(target_date="2026-10-23", chunk_index=0, chunk_size=3, total=3)
    result.success = 1
    result.failed = 1
    result.skipped = 1

    assert result.processed_count == 3


def test_runtime_database_connection_does_not_run_schema_ddl(monkeypatch):
    from data.database import DatabaseManager

    monkeypatch.setenv("DB_PASSWORD", "isolated-test-only")
    fake_connection = MagicMock()
    with patch.object(DatabaseManager, "_create_tables") as create_tables, patch.object(
        DatabaseManager, "_migrate_task_status_columns"
    ) as migrate, patch.object(
        DatabaseManager, "_connect", lambda self: setattr(self, "conn", fake_connection)
    ):
        db = DatabaseManager(initialize_schema=False, autocommit=True)

    create_tables.assert_not_called()
    migrate.assert_not_called()
    assert db.autocommit is True
    assert fake_connection.autocommit is True


def test_default_database_connection_does_not_run_schema_ddl(monkeypatch):
    from data.database import DatabaseManager

    monkeypatch.setenv("DB_PASSWORD", "isolated-test-only")
    fake_connection = MagicMock()
    with patch.object(DatabaseManager, "_create_tables") as create_tables, patch.object(
        DatabaseManager, "_migrate_task_status_columns"
    ) as migrate, patch.object(
        DatabaseManager, "_connect", lambda self: setattr(self, "conn", fake_connection)
    ):
        DatabaseManager()

    create_tables.assert_not_called()
    migrate.assert_not_called()


def test_obpc_wait_uses_autocommit_connection_without_schema_ddl(monkeypatch):
    from scripts import scheduler_obpc
    import data.database

    observed = {}

    class FakeDB:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(data.database, "DatabaseManager", FakeDB)
    monkeypatch.setattr(scheduler_obpc, "_query_task_status", lambda db, task: {
        "status": "completed",
        "target_date": "2026-10-23",
        "last_completed_at": scheduler_obpc.datetime.now(scheduler_obpc.TZ),
    })

    assert scheduler_obpc.wait_for_kline_update("2026-10-23") is True
    assert observed == {"initialize_schema": False, "autocommit": True}

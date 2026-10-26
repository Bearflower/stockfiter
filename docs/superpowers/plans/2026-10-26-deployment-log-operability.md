# Deployment Log Operability Implementation Plan

> **For agentic workers:** Follow the repository development workflow and test-driven development; do not commit or deploy without separate user authorization.

**Goal:** Close the remaining deployment log and host-monitor operational gaps.

**Architecture:** Keep the existing three-container Compose deployment. Add revision-aware log formatting and bounded archive retention, synchronize the host monitor via an identity-checked deploy helper, and correct GitHub Actions path/build filters.

**Tech Stack:** GitHub Actions YAML, Bash, Python 3 standard library, Docker Compose, pytest.

---

### Task 1: Trigger and build selection

**Files:** `.github/workflows/deploy.yml`; test `tests/operations/test_deploy_workflow.py`.

- [x] Add a failing test for nested Markdown filtering, full push comparison and health-gated success.
- [x] Confirm the prior workflow fails these checks.
- [x] Add `**/*.md` and compare complete push range rather than only `HEAD~1`; preserve dispatch behavior.
- [x] Re-run focused tests and YAML parsing. Actual GitHub trigger remains a production acceptance item.

### Task 2: Revision-aware persistent and archived logs

**Files:** `scripts/persistent_log_runner.py`, `scripts/archive_container_logs.sh`, `Dockerfile.kline`, `Dockerfile.obpc`, `Dockerfile.eadvisor`, `docker-compose.yml`, `.github/workflows/deploy.yml`; test `tests/operations/test_persistent_logs.py`.

- [x] Add failing tests for file-log metadata/stdout parity, archive metadata/layout, two consecutive archives, unknown legacy revision and partial failure.
- [x] Run focused tests and confirm new cases fail for the expected reason.
- [x] Add image revision ARG/ENV/label, set requested log paths, prefix each file-log line, and write revision-aware atomic archives.
- [x] Re-run focused tests, YAML parsing and shell syntax checks.

### Task 3: Safe archive retention

**Files:** create `scripts/prune_container_archives.py`; test `tests/operations/test_archive_retention.py`; modify `.github/workflows/deploy.yml`.

- [x] Add failing tests for age boundary, last-two protection, unknown files and partial directories.
- [x] Implement 90-day configurable retention and two-release floor, only within the managed archive root.
- [x] Call pruning after successful deployment; re-run tests and compilation.

### Task 4: Host monitor synchronization

**Files:** create `scripts/monitor_deploy.py` and `scripts/deploy_release.sh`; modify `.github/workflows/deploy.yml`; test `tests/operations/test_monitor_deploy.py`.

- [x] Add failing tests for PID/cwd identity, missing or duplicate monitor instances, and activation/restore script rollback.
- [x] Implement exact-SHA download, pre-stop pause, identity-checked atomic replacement after container health is confirmed, and partial-failure recording.
- [x] Run tests and `bash -n` for monitor and release scripts. Remote execution still pending release authorization.

### Task 5: Documentation and final verification

**Files:** `docs/designs/部署方案.md`, `docs/reports/2026-10-26-kline-lock-and-log-persistence.md`, `docs/README.md`.

- [x] Align paths, retention, monitor restart and verification instructions with real code.
- [x] Run final focused/full tests (454 passed, 13 skipped), compilation with writable cache, YAML checks, shell syntax and `git diff --check`.
- [x] Review the complete diff against AC1–AC7. Pending production-only checks: Compose semantic validation on server, two consecutive releases, real file-log append/rotation, and a real docs-only push. The local Docker CLI lacks the Compose plugin; no remote changes were made.

No Git write or production change is part of this local implementation plan unless the user separately authorizes it.

"""
Task 5 验收器 fail-closed 契约测试（修复 issue 8）

覆盖 verify_obpc_pipeline.verify_data_metrics_for_graded：
  - data_metrics 缺失/为空 → 致命错误（fail-closed，不得静默放过）
  - policy_mode 缺失/非法 → 致命错误
  - graded 模式顶层 market_regime/position_multiplier 缺失 → 致命错误
  - shadow 模式必须读取嵌套 shadow_graded_decision（而非误判顶层缺失）
  - shadow 极弱 allow_entry=True → 分级裁决穿透（致命）
  - hard_filter 模式缺失字段 → 正常放行
"""

import importlib.util
import os

import pytest

# verify_obpc_pipeline.py 位于 scripts/ 下，直接按路径加载模块
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SPEC = importlib.util.spec_from_file_location(
    "verify_obpc_pipeline", os.path.join(_ROOT, "scripts", "verify_obpc_pipeline.py")
)
verify_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify_mod)

verify_data_metrics_for_graded = verify_mod.verify_data_metrics_for_graded


class TestVerifyDataMetricsFailClosed:
    def test_missing_data_metrics_is_fatal(self):
        """data_metrics=None → 致命错误（修复前只 warning 且 passed=True）"""
        r = verify_data_metrics_for_graded(None, selected_count=0, raw_candidate_count=3)
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_empty_data_metrics_is_fatal(self):
        """data_metrics={} → 致命错误"""
        r = verify_data_metrics_for_graded({}, selected_count=0, raw_candidate_count=3)
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_missing_policy_mode_is_fatal(self):
        """data_metrics 有内容但缺 policy_mode → 致命错误"""
        r = verify_data_metrics_for_graded(
            {"raw_candidate_count": 3}, selected_count=0, raw_candidate_count=3
        )
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_invalid_policy_mode_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {"policy_mode": "bogus"}, selected_count=0, raw_candidate_count=0
        )
        assert r["passed"] is False

    def test_hard_filter_mode_passes(self):
        """hard_filter 模式只带 policy_mode → 正常放行"""
        r = verify_data_metrics_for_graded(
            {"policy_mode": "hard_filter"}, selected_count=0, raw_candidate_count=0
        )
        assert r["passed"] is True
        assert r["fatal_errors"] == []


class TestVerifyDataMetricsGraded:
    def _graded_dm(self, **overrides):
        dm = {
            "policy_mode": "graded",
            "market_regime": "weak",
            "position_multiplier": 0.5,
            "raw_candidate_count": 10,
        }
        dm.update(overrides)
        return dm

    def test_graded_missing_regime_is_fatal(self):
        dm = self._graded_dm()
        dm.pop("market_regime")
        r = verify_data_metrics_for_graded(dm, selected_count=0, raw_candidate_count=10)
        assert r["passed"] is False

    def test_graded_missing_position_multiplier_is_fatal(self):
        dm = self._graded_dm()
        dm.pop("position_multiplier")
        r = verify_data_metrics_for_graded(dm, selected_count=0, raw_candidate_count=10)
        assert r["passed"] is False

    def test_graded_extreme_weak_but_selected_is_fatal(self):
        """极弱市场却放行 → 风控穿透"""
        dm = self._graded_dm(market_regime="extreme_weak", position_multiplier=0.0)
        r = verify_data_metrics_for_graded(dm, selected_count=2, raw_candidate_count=10)
        assert r["passed"] is False

    def test_graded_valid_passes(self):
        r = verify_data_metrics_for_graded(
            self._graded_dm(), selected_count=1, raw_candidate_count=10
        )
        assert r["passed"] is True
        assert r["regime"] == "weak"


class TestVerifyDataMetricsShadow:
    def _shadow_dm(self, decision=None):
        return {
            "policy_mode": "shadow",
            "raw_candidate_count": 10,
            "shadow_graded_decision": decision
            if decision is not None
            else {
                "regime": "weak",
                "allow_entry": True,
                "position_multiplier": 0.5,
                "score_threshold": 75,
                "daily_signal_limit": 2,
                "require_relative_strength": True,
                "reason_code": "weak_market",
                "reason": "大盘偏弱",
            },
        }

    def test_shadow_reads_nested_decision(self):
        """shadow 必须从嵌套 shadow_graded_decision 读取，而非误判顶层缺失。"""
        r = verify_data_metrics_for_graded(
            self._shadow_dm(), selected_count=0, raw_candidate_count=10
        )
        assert r["passed"] is True
        assert r["is_graded"] is True
        assert r["regime"] == "weak"

    def test_shadow_missing_graded_decision_is_fatal(self):
        dm = self._shadow_dm()
        dm.pop("shadow_graded_decision")
        r = verify_data_metrics_for_graded(dm, selected_count=0, raw_candidate_count=10)
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_shadow_extreme_weak_allow_entry_is_fatal(self):
        """shadow 裁决本身在极弱市场不得 allow_entry=True（穿透）。"""
        dm = self._shadow_dm(decision={
            "regime": "extreme_weak",
            "allow_entry": True,
            "position_multiplier": 0.0,
        })
        r = verify_data_metrics_for_graded(dm, selected_count=0, raw_candidate_count=10)
        assert r["passed"] is False


class TestVerifyModeDrift:
    """P1-2：配置期望模式与落库 policy_mode 交叉校验（fail-closed）。

    修复前 policy_mode=="hard_filter" 直接 passed=True，
    「配置已切 graded/shadow 但当日仍按 hard_filter 落库」的模式漂移被静默放过。
    """

    def test_config_graded_but_persisted_hard_filter_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {"policy_mode": "hard_filter"},
            selected_count=0,
            raw_candidate_count=5,
            expected_mode="graded",
        )
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_config_shadow_but_persisted_hard_filter_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {"policy_mode": "hard_filter"},
            selected_count=0,
            raw_candidate_count=5,
            expected_mode="shadow",
        )
        assert r["passed"] is False

    def test_config_matches_persisted_passes(self):
        r = verify_data_metrics_for_graded(
            {"policy_mode": "hard_filter"},
            selected_count=0,
            raw_candidate_count=0,
            expected_mode="hard_filter",
        )
        assert r["passed"] is True

    def test_expected_mode_none_skips_cross_check(self):
        """expected_mode=None → 不做漂移校验（向后兼容）"""
        r = verify_data_metrics_for_graded(
            {"policy_mode": "hard_filter"}, selected_count=0, raw_candidate_count=0
        )
        assert r["passed"] is True
        assert r["expected_mode"] is None


class TestVerifyUnparseableMultiplierFatal:
    """P2-2：position_multiplier 不可解析 → fail-closed 致命（修复前仅 warning）。"""

    def test_graded_unparseable_multiplier_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "graded",
                "market_regime": "weak",
                "position_multiplier": "abc",
            },
            selected_count=0,
            raw_candidate_count=5,
        )
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_shadow_unparseable_multiplier_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "shadow",
                "raw_candidate_count": 5,
                "shadow_graded_decision": {
                    "regime": "weak",
                    "allow_entry": True,
                    "position_multiplier": "abc",
                },
            },
            selected_count=0,
            raw_candidate_count=5,
        )
        assert r["passed"] is False
        assert r["fatal_errors"]


class _DbAccessReached(Exception):
    """哨兵异常：main() 执行到数据库连接步骤即抛出，用于验证 fail-closed 顺序。"""


class TestVerifyCandidateCountClosure:
    """P2-1（2026-10-20）：候选计数闭环必须 fail-closed（graded / shadow 一致）。

    修复前三种矛盾都能 passed=True：data_metrics.raw_candidate_count 缺失只跳过检查、
    两处 raw_candidate_count 不一致仅 warning、shadow 在计数校验前提前 return。
    """

    def test_graded_missing_data_metrics_raw_count_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "graded",
                "market_regime": "weak",
                "position_multiplier": 0.5,
            },
            selected_count=3,
            raw_candidate_count=10,
        )
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_graded_mismatched_raw_counts_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "graded",
                "market_regime": "weak",
                "position_multiplier": 0.5,
                "raw_candidate_count": 7,
            },
            selected_count=1,
            raw_candidate_count=9,
        )
        assert r["passed"] is False
        assert any("不一致" in e for e in r["fatal_errors"])

    def test_graded_selected_exceeds_scan_raw_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "graded",
                "market_regime": "weak",
                "position_multiplier": 0.5,
                "raw_candidate_count": 2,
            },
            selected_count=5,
            raw_candidate_count=2,
        )
        assert r["passed"] is False
        assert any("候选数逻辑矛盾" in e for e in r["fatal_errors"])

    def test_shadow_missing_data_metrics_raw_count_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "shadow",
                "shadow_graded_decision": {
                    "regime": "weak",
                    "allow_entry": True,
                    "position_multiplier": 0.5,
                },
            },
            selected_count=0,
            raw_candidate_count=5,
        )
        assert r["passed"] is False
        assert r["fatal_errors"]

    def test_shadow_selected_exceeds_raw_is_fatal(self):
        """shadow 必须在返回前完成计数校验（修复前提前 return 直接放过）。"""
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "shadow",
                "raw_candidate_count": 2,
                "shadow_graded_decision": {
                    "regime": "weak",
                    "allow_entry": True,
                    "position_multiplier": 0.5,
                },
            },
            selected_count=3,
            raw_candidate_count=2,
        )
        assert r["passed"] is False
        assert any("候选数逻辑矛盾" in e for e in r["fatal_errors"])

    def test_consistent_counts_pass(self):
        """正向对照：两处计数一致且 selected 不超上限 → 通过。"""
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "graded",
                "market_regime": "weak",
                "position_multiplier": 0.5,
                "raw_candidate_count": 7,
            },
            selected_count=2,
            raw_candidate_count=7,
        )
        assert r["passed"] is True
        assert r["fatal_errors"] == []


class TestVerifyCandidateCountStrictType:
    """P2-1 加固（2026-10-20）：候选计数必须严格为「非布尔非负整数」。

    修复前 _safe_int 直接 int(value)：True→1、3.9→3；scan_runs.raw_candidate_count
    为 None/非法值时也不产生 fatal → 候选计数闭环仍可能 fail-open。
    """

    def test_safe_int_rejects_bool(self):
        # Python 中 isinstance(True, int) 为 True，必须先排除 bool
        assert verify_mod._safe_int(True) is None
        assert verify_mod._safe_int(False) is None

    def test_safe_int_rejects_float(self):
        assert verify_mod._safe_int(3.9) is None
        assert verify_mod._safe_int(3.0) is None

    def test_safe_int_rejects_str(self):
        assert verify_mod._safe_int("3") is None

    def test_safe_int_rejects_negative(self):
        assert verify_mod._safe_int(-1) is None

    def test_safe_int_accepts_nonneg_int(self):
        assert verify_mod._safe_int(0) == 0
        assert verify_mod._safe_int(7) == 7

    def test_graded_scan_runs_raw_count_none_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "graded",
                "market_regime": "weak",
                "position_multiplier": 0.5,
                "raw_candidate_count": 5,
            },
            selected_count=1,
            raw_candidate_count=None,
        )
        assert r["passed"] is False
        assert any("scan_runs.raw_candidate_count" in e for e in r["fatal_errors"])

    def test_graded_bool_raw_count_is_fatal(self):
        """data_metrics(raw)=True 会被旧 _safe_int 当成 1，必须判 fatal。"""
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "graded",
                "market_regime": "weak",
                "position_multiplier": 0.5,
                "raw_candidate_count": True,
            },
            selected_count=0,
            raw_candidate_count=1,
        )
        assert r["passed"] is False
        assert any("data_metrics.raw_candidate_count" in e for e in r["fatal_errors"])

    def test_shadow_scan_runs_raw_count_none_is_fatal(self):
        r = verify_data_metrics_for_graded(
            {
                "policy_mode": "shadow",
                "raw_candidate_count": 5,
                "shadow_graded_decision": {
                    "regime": "weak",
                    "allow_entry": True,
                    "position_multiplier": 0.5,
                },
            },
            selected_count=0,
            raw_candidate_count=None,
        )
        assert r["passed"] is False
        assert any("scan_runs.raw_candidate_count" in e for e in r["fatal_errors"])


class TestVerifyShadowExtremeWeakAllowEntryStrict:
    """P2-2 加固（2026-10-20）：shadow 极弱必须 allow_entry 明确为布尔 False。

    修复前只拦截 allow_entry is True；字段缺失 / None / 非布尔值都静默通过，
    无法证明极弱硬阻断生效。
    """

    def _dm(self, allow, present: bool = True):
        dec = {"regime": "extreme_weak", "position_multiplier": 0.0}
        if present:
            dec["allow_entry"] = allow
        return {
            "policy_mode": "shadow",
            "raw_candidate_count": 5,
            "shadow_graded_decision": dec,
        }

    def test_missing_allow_entry_is_fatal(self):
        r = verify_data_metrics_for_graded(
            self._dm(None, present=False), selected_count=0, raw_candidate_count=5
        )
        assert r["passed"] is False
        assert any("allow_entry" in e for e in r["fatal_errors"])

    @pytest.mark.parametrize("allow", [None, "False", 0, 1, True])
    def test_non_false_allow_entry_is_fatal(self, allow):
        r = verify_data_metrics_for_graded(
            self._dm(allow), selected_count=0, raw_candidate_count=5
        )
        assert r["passed"] is False
        assert any("allow_entry" in e for e in r["fatal_errors"])

    def test_false_allow_entry_passes(self):
        """正向对照：极弱 + allow_entry 严格 False → 通过。"""
        r = verify_data_metrics_for_graded(
            self._dm(False), selected_count=0, raw_candidate_count=5
        )
        assert r["passed"] is True, r["fatal_errors"]


class TestVerifyMainConfigLoadFailClosed:
    """P1-6 回归（2026-10-20）：配置读取失败不得默认成 hard_filter 继续验收。

    修复前 load_config 出错返回空 dict，main 随后把 expected_mode 默认成
    hard_filter——若数据库恰好也记录 hard_filter，验收就可能通过（fail-open）。
    """

    def test_load_config_missing_file_returns_empty(self, tmp_path):
        """load_config 读不到文件时返回空 dict（供 fail-closed 判定使用）。"""
        missing = tmp_path / "no_such_config.yaml"
        assert verify_mod.load_config(str(missing)) == {}

    def test_main_records_precondition_failure_before_db_access(
        self, monkeypatch, tmp_path, capsys
    ):
        """配置加载失败必须在校验任何数据库证据之前就判定为前置条件失败。"""
        import sys

        missing = tmp_path / "no_such_config.yaml"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "verify_obpc_pipeline.py",
                "--signal-date",
                "2026-10-16",
                "--config",
                str(missing),
            ],
        )
        # 拦截数据库连接：只要执行到这里就抛哨兵异常，证明此前无任何 DB 证据
        monkeypatch.setattr(
            verify_mod,
            "_connect_readonly",
            lambda: (_ for _ in ()).throw(_DbAccessReached()),
        )

        with pytest.raises(_DbAccessReached):
            verify_mod.main()

        err = capsys.readouterr().err
        # 该消息与追加进 precondition_failures 的 reason 完全一致
        assert "加载失败或为空" in err
        assert "不得默认按 hard_filter 验收" in err

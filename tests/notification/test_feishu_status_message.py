"""
Task 4 Step 1 / 9.3.2：build_scan_summary_message 六态覆盖测试

六态必须：永不抛异常；completed_with_signals → None（走卡片）；
其他状态返回文本。
"""

import pytest

from scripts.feishu_push import build_scan_summary_message


# 基础三例（计划 Task 4 Step 1）


def test_missing_run_is_not_reported_as_zero_signal():
    """run=None 不能说"无符合形态"，必须说扫描结果缺失"""
    message = build_scan_summary_message("2026-10-16", None)
    assert message is not None
    assert "扫描结果缺失" in message
    assert "无符合形态" not in message


def test_market_block_reports_raw_candidates():
    """blocked_market 必须展示原始候选数和过滤原因"""
    run = {
        "status": "blocked_market",
        "raw_candidate_count": 10,
        "selected_count": 0,
        "filter_reason": "沪深300 DIF <= 0",
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "10 个原始形态候选" in message
    assert "市场风控拦截" in message
    assert "沪深300 DIF <= 0" in message


def test_completed_zero_is_the_only_true_zero_message():
    """completed_zero 是唯一可以说"完整扫描后未发现形态"的状态"""
    run = {
        "status": "completed_zero",
        "raw_candidate_count": 0,
        "selected_count": 0,
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "完整扫描后未发现形态" in message


# 六态全覆盖


def test_completed_with_signals_returns_none():
    """completed_with_signals → None（主流程走卡片）"""
    run = {
        "status": "completed_with_signals",
        "raw_candidate_count": 5,
        "selected_count": 3,
    }
    assert build_scan_summary_message("2026-10-16", run) is None


def test_running_status_does_not_raise():
    """running → 返回异常语义文本，不抛异常（第 9.3.2 节）"""
    run = {"status": "running"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "尚未结束" in message or "未收敛" in message or "调度" in message


def test_blocked_data_status():
    """blocked_data → 数据不完整"""
    run = {"status": "blocked_data"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "数据不完整" in message


def test_failed_status_includes_reason_if_present():
    """failed → 扫描任务失败，有 filter_reason 时附带"""
    run = {"status": "failed", "filter_reason": "scan_runs_persist_failed: xxx"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "扫描任务失败" in message
    assert "scan_runs_persist_failed" in message


def test_failed_status_no_reason():
    """failed 无 filter_reason 也不崩"""
    run = {"status": "failed"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "扫描任务失败" in message


def test_unknown_status_does_not_raise():
    """未知 status → 返回状态异常文本，不抛异常"""
    run = {"status": "some_weird_value_not_in_enum"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "状态异常" in message or "some_weird_value_not_in_enum" in message


def test_run_dict_malformed_does_not_raise():
    """run 缺字段/非标准结构也不抛异常"""
    # 直接测试 status 缺键
    message = build_scan_summary_message("2026-10-16", {})
    assert message is not None


# 通知语义修复（P1）：blocked_market 按 filter_reason 区分市场层面 / 个股层面


def test_blocked_market_individual_level_not_market_interception():
    """个股层面筛掉全部候选（RISK_CONTROLS_FILTERED_ALL）不能说"市场风控拦截"，
    且不能把内部常量字符串原样展示给用户"""
    from scripts.daily_scan import RISK_CONTROLS_FILTERED_ALL

    run = {
        "status": "blocked_market",
        "raw_candidate_count": 4,
        "selected_count": 0,
        "filter_reason": RISK_CONTROLS_FILTERED_ALL,
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "4 个原始形态候选" in message
    assert "市场风控拦截" not in message
    assert RISK_CONTROLS_FILTERED_ALL not in message
    assert "个股级筛选" in message


def test_blocked_market_market_level_keeps_market_wording():
    """市场层面拦截（filter_reason 非个股全筛标识）必须出现"市场风控拦截" """
    run = {
        "status": "blocked_market",
        "raw_candidate_count": 7,
        "selected_count": 0,
        "filter_reason": "MACD DIF < 0",
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "7 个原始形态候选" in message
    assert "市场风控拦截" in message
    assert "MACD DIF < 0" in message


def test_blocked_market_lists_raw_candidate_codes_and_names():
    """blocked_market 有 data_metrics.raw_candidates 时，文案必须列出股票代码和名称"""
    run = {
        "status": "blocked_market",
        "raw_candidate_count": 2,
        "selected_count": 0,
        "filter_reason": (
            "大盘环境不佳（20日均线过滤：沪深300 4036.30 < MA20 4313.60）"
        ),
        "data_metrics": {
            "raw_candidate_count": 2,
            "raw_candidates": [
                {"code": "000001", "name": "平安银行"},
                {"code": "600000", "name": "浦发银行"},
            ],
        },
    }
    message = build_scan_summary_message("2026-10-22", run)
    assert message is not None
    assert "2 个原始形态候选（000001 平安银行，600000 浦发银行）" in message
    assert "市场风控拦截" in message


def test_blocked_market_without_candidate_details_falls_back():
    """旧记录缺 raw_candidates（或结构异常）时回退为仅展示数量，不抛异常"""
    for data_metrics in (None, {}, {"raw_candidates": "bad_type"}, {"raw_candidates": [1, {}]}):
        run = {
            "status": "blocked_market",
            "raw_candidate_count": 6,
            "selected_count": 0,
            "filter_reason": "沪深300 DIF <= 0",
            "data_metrics": data_metrics,
        }
        message = build_scan_summary_message("2026-10-21", run)
        assert message is not None
        assert "6 个原始形态候选，但被市场风控拦截" in message


# ===========================================================================
# Task 5 / V3 分级市场风控：send_card_message 分级展示测试
# ===========================================================================


def _make_base_signal(**overrides) -> dict:
    """构造一个最小可用的信号 dict（所有必要字段）"""
    base = {
        "code": "000001",
        "name": "平安银行",
        "support_level": 10.0,
        "stop_loss_price": 9.7,
        "retrace_date": "2026-10-10",
        "surge_date": "2026-10-08",
        "signal_date": "2026-10-16",
        "score": 85.5,
        "surge_pct": 0.12,
        "surge_volume_ratio": 3.5,
        "drop_rate": -0.08,
        "retrace_low": 9.8,
        "retrace_close": 10.2,
        "shrink_ratio": 0.85,
        "drop_start_date": "2026-10-08",
        "drop_end_date": "2026-10-10",
        "trailing_stop_ratio": 0.08,
        "hard_stop_loss": 0.10,
        "stop_loss_ratio": 0.97,
    }
    base.update(overrides)
    return base


def _patch_send_via_webhook(monkeypatch):
    """monkeypatch FeishuPusher._send_via_webhook 捕获 card 并返回"""
    captured = {}

    def fake_send(self, card):
        captured["card"] = card
        return True

    from scripts.feishu_push import FeishuPusher

    monkeypatch.setattr(FeishuPusher, "_send_via_webhook", fake_send)
    return captured


class TestGradedMarketNotification:
    """分级市场风控通知语义测试。

    目标：确保 feishu_push 按 market_regime 正确分流展示，
    极弱市场绝不出现"建议买入"措辞。
    """

    def test_strong_market_signal_shows_green_header(self, monkeypatch):
        """强市信号：header 绿色，标题包含"强市正常放行"，正文有分级标签。"""
        captured = _patch_send_via_webhook(monkeypatch)
        signals = [
            _make_base_signal(
                market_regime="strong",
                market_reason_code="STRONG_MA20_UP",
                position_multiplier=1.0,
                base_position_pct=0.10,
                recommended_position_pct=0.10,
                relative_strength=0.15,
                relative_strength_lookback=20,
            )
        ]

        from scripts.feishu_push import FeishuPusher

        pusher = FeishuPusher(webhook_url="https://example.com/fake")
        pusher.send_card_message(signals)

        card = captured["card"]
        # header 颜色绿色
        assert card["card"]["header"]["template"] == "green"
        # 标题含强市标签
        assert "强市正常放行" in card["card"]["header"]["title"]["content"]
        # 第一只股票元素要有强市标签
        first_element = card["card"]["elements"][1]  # summary 是 elements[0]
        assert "强市正常放行" in first_element["text"]["content"]
        # 强市不展示建议仓位强市标签（只放分级 tag）
        assert "观察名单" not in card["card"]["header"]["title"]["content"]

    def test_weak_market_signal_shows_reduced_position(self, monkeypatch):
        """弱市信号：header 黄色，标题含"弱市分级放行"，正文展示建议仓位和相对强度。"""
        captured = _patch_send_via_webhook(monkeypatch)
        signals = [
            _make_base_signal(
                market_regime="weak",
                market_reason_code="WEAK_BELOW_MA20",
                position_multiplier=0.5,
                base_position_pct=0.10,
                recommended_position_pct=0.05,
                relative_strength=0.08,
                relative_strength_lookback=20,
            )
        ]

        from scripts.feishu_push import FeishuPusher

        pusher = FeishuPusher(webhook_url="https://example.com/fake")
        pusher.send_card_message(signals)

        card = captured["card"]
        # header 颜色黄色
        assert card["card"]["header"]["template"] == "yellow"
        # 标题含弱市标签
        assert "弱市分级放行" in card["card"]["header"]["title"]["content"]

        # summary 段要有弱市提示
        summary = card["card"]["elements"][0]["text"]["content"]
        assert "弱市分级放行" in summary
        assert "观察名单" not in card["card"]["header"]["title"]["content"]

    def test_extreme_weak_shows_observation_list_not_buy(self, monkeypatch):
        """极弱市场：标题是"观察名单"（橙色），绝对不能出现"建议买入"措辞。"""
        captured = _patch_send_via_webhook(monkeypatch)
        signals = [
            _make_base_signal(
                market_regime="extreme_weak",
                market_reason_code="EXTREME_WEAK_BELOW_MA60",
                position_multiplier=0.0,
                base_position_pct=0.10,
                recommended_position_pct=0.0,
                relative_strength=None,
                relative_strength_lookback=20,
            )
        ]

        from scripts.feishu_push import FeishuPusher

        pusher = FeishuPusher(webhook_url="https://example.com/fake")
        pusher.send_card_message(signals)

        card = captured["card"]
        # header 必须是观察名单 + 橙色
        assert card["card"]["header"]["template"] == "orange"
        assert "观察名单" in card["card"]["header"]["title"]["content"]
        assert "买入信号" not in card["card"]["header"]["title"]["content"]

        # summary 段要有禁止开仓的措辞
        summary = card["card"]["elements"][0]["text"]["content"]
        assert "禁止开仓" in summary
        assert "仅供观察" in summary or "观察参考" in summary
        # 绝对不能出现"建议买入"或"择机买入"
        assert "建议买入" not in summary
        assert "择机买入" not in summary

        # 个股卡片里不能有交易建议段
        first_element = card["card"]["elements"][1]
        element_text = first_element["text"]["content"]
        assert "极弱市场" in element_text
        assert "仅供观察" in element_text or "仅供观察" in element_text
        # 极弱时不展示交易建议
        assert "💡 交易建议" not in element_text
        assert "建议买入价" not in element_text

    def test_relative_strength_displayed_in_weak_market(self, monkeypatch):
        """弱市信号必须展示相对强度百分比（正相对强度有 + 号，负相对强度有 - 号）。"""
        captured = _patch_send_via_webhook(monkeypatch)
        signals = [
            _make_base_signal(
                market_regime="weak",
                relative_strength=0.08,
                relative_strength_lookback=20,
                recommended_position_pct=0.05,
                position_multiplier=0.5,
            ),
            _make_base_signal(
                market_regime="weak",
                code="000002",
                name="万科A",
                relative_strength=-0.03,
                relative_strength_lookback=20,
                recommended_position_pct=0.05,
                position_multiplier=0.5,
            ),
        ]

        from scripts.feishu_push import FeishuPusher

        pusher = FeishuPusher(webhook_url="https://example.com/fake")
        pusher.send_card_message(signals)

        card = captured["card"]
        # 个股卡片元素在 elements[1] 和 elements[2]
        first_element = card["card"]["elements"][1]["text"]["content"]
        second_element = card["card"]["elements"][2]["text"]["content"]

        # 正相对强度带 + 号
        assert "+8.00%" in first_element or "+8.0%" in first_element
        # 负相对强度带 - 号
        assert "-3.00%" in second_element or "-3.0%" in second_element
        # 两个都要有相对强度字段
        assert "相对强度" in first_element
        assert "相对强度" in second_element
        # 两个都要有建议仓位
        assert "建议仓位" in first_element
        assert "建议仓位" in second_element

    def test_hard_filter_mode_no_graded_labels(self, monkeypatch):
        """hard_filter 模式下不展示任何分级标签（用户体验不被干扰）。"""
        captured = _patch_send_via_webhook(monkeypatch)
        signals = [
            _make_base_signal(
                market_regime="hard_filter",
                market_reason_code=None,
                position_multiplier=1.0,
                base_position_pct=0.10,
                recommended_position_pct=0.10,
                relative_strength=None,
                relative_strength_lookback=20,
            )
        ]

        from scripts.feishu_push import FeishuPusher

        pusher = FeishuPusher(webhook_url="https://example.com/fake")
        pusher.send_card_message(signals)

        card = captured["card"]
        # header 传统蓝色 + 传统标题
        assert card["card"]["header"]["template"] == "blue"
        assert card["card"]["header"]["title"]["content"] == "📈 新筛选股票提醒"

        # summary 传统文案（建议买入）
        summary = card["card"]["elements"][0]["text"]["content"]
        assert "择机买入" in summary
        assert "弱市" not in summary
        assert "观察名单" not in summary

        # 个股卡片无分级字段
        first_element = card["card"]["elements"][1]["text"]["content"]
        assert "强市正常放行" not in first_element
        assert "弱市分级放行" not in first_element
        assert "极弱市场" not in first_element
        assert "建议仓位" not in first_element
        assert "相对强度" not in first_element

    def test_strong_market_signal_has_trading_advice(self, monkeypatch):
        """强市信号必须有完整交易建议段（买入价/移动止盈/硬止损）。"""
        captured = _patch_send_via_webhook(monkeypatch)
        signals = [
            _make_base_signal(
                market_regime="strong",
                position_multiplier=1.0,
                base_position_pct=0.10,
                recommended_position_pct=0.10,
                relative_strength=0.15,
                relative_strength_lookback=20,
            )
        ]

        from scripts.feishu_push import FeishuPusher

        pusher = FeishuPusher(webhook_url="https://example.com/fake")
        pusher.send_card_message(signals)

        card = captured["card"]
        first_element = card["card"]["elements"][1]["text"]["content"]
        assert "💡 交易建议" in first_element
        assert "建议买入价" in first_element
        assert "移动止盈" in first_element
        assert "硬止损" in first_element

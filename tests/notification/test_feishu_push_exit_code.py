"""
通知语义修复（P1）：scripts/feishu_push.main() 退出码测试

覆盖：
  - 发送返回 False → main() 抛 SystemExit 且 code == 1（调度器 check=True 才能判定失败）
  - 发送成功 → 不抛 SystemExit（或 code 为 0）
  - 扫描状态声称"有信号"（completed_with_signals）但信号文件为空 → SystemExit code == 1

本测试全程 monkeypatch：替换 FEISHU_WEBHOOK 环境变量、DatabaseManager 与
FeishuPusher，禁止真实数据库连接与真实 HTTP 发送。
"""

import sys

import pytest

from scripts.feishu_push import main


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class _FakeStdin:
    """isatty() 返回 False，使 main() 不进入交互式确认（auto_send 保持 True）"""

    def isatty(self):
        return False


def _make_fake_db_manager(run):
    """返回可注入 data.database.DatabaseManager 的 Fake 类"""

    class FakeDBManager:
        def __init__(self, *args, **kwargs):
            pass

        def get_scan_run(self, signal_date, run_kind=None):
            return run

        def close(self):
            pass

    return FakeDBManager


def _make_fake_pusher(card_result, text_result):
    """返回可注入 scripts.feishu_push.FeishuPusher 的 Fake 类（不发 HTTP）"""

    class FakePusher:
        def __init__(self, webhook_url=None, *args, **kwargs):
            self.webhook_url = webhook_url

        def send_card_message(self, signals):
            return card_result

        def send_text_message(self, content):
            return text_result

    return FakePusher


def _setup_main(monkeypatch, run, card_result=False, text_result=False, signals=None):
    """统一安装 main() 所需的环境与替身（不触碰真实 DB / HTTP）"""
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://example.com/fake-webhook")
    # argparse 读 sys.argv，必须屏蔽 pytest 自身的参数
    monkeypatch.setattr(sys, "argv", ["feishu_push.py", "--signal-date", "2026-10-16"])
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    monkeypatch.setattr("data.database.DatabaseManager", _make_fake_db_manager(run))
    monkeypatch.setattr(
        "scripts.feishu_push.FeishuPusher",
        _make_fake_pusher(card_result, text_result),
    )
    if signals is not None:
        monkeypatch.setattr(
            "scripts.feishu_push.load_signals",
            lambda signal_date=None: signals,
        )


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_send_failure_exits_nonzero(monkeypatch):
    """文本发送返回 False → main() 必须 SystemExit code == 1"""
    run = {
        "status": "blocked_market",
        "raw_candidate_count": 3,
        "selected_count": 0,
        "filter_reason": "沪深300 DIF <= 0",
    }
    _setup_main(monkeypatch, run, text_result=False)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1, "发送失败必须以退出码 1 结束"


def test_send_success_does_not_exit_nonzero(monkeypatch):
    """文本发送返回 True → 正常 return（不抛 SystemExit，或 code 为 0）"""
    run = {
        "status": "blocked_market",
        "raw_candidate_count": 3,
        "selected_count": 0,
        "filter_reason": "沪深300 DIF <= 0",
    }
    _setup_main(monkeypatch, run, text_result=True)

    try:
        main()
    except SystemExit as exc:
        assert exc.code in (0, None), f"成功路径不应以 {exc.code} 退出"


def test_signals_missing_but_status_says_signals_exits_nonzero(monkeypatch):
    """completed_with_signals 但信号文件为空 → SystemExit code == 1（不当作推送成功）"""
    run = {
        "status": "completed_with_signals",
        "raw_candidate_count": 5,
        "selected_count": 3,
    }
    # summary_text is None → 走卡片分支；信号文件为空列表，属异常
    _setup_main(monkeypatch, run, card_result=True, signals=[])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1, "信号缺失/为空时必须以退出码 1 结束"

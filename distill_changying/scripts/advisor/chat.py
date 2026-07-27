"""
E大投资决策助手交互式问答模块

提供基于 E大知识底座的交互式问答功能。
维护三轮上下文：system(知识底座) + market(当前市场数据，可刷新) + history(最近 N 轮对话)
"""

from __future__ import annotations
import logging
from typing import Any

from scripts.shared.llm_utils import build_llm_client
from scripts.advisor.knowledge_base import load_knowledge_base

logger = logging.getLogger(__name__)

DISCLAIMER = "\n\n> ⚠️ 以上分析仅供参考，不构成投资建议。投资有风险，决策须谨慎。"

# 刷新市场数据命令集合
_REFRESH_COMMANDS = {"刷新", "refresh", "update", "更新数据"}


def build_market_context(
    market_data: dict,
    temperature: dict,
    position: dict,
    etf_recommendations: dict | None = None,
) -> str:
    """构建当前市场上下文文本（静态注入到每条用户消息后面）。

    类似 analyze 模式的数据部分，但更简洁，用于对话中提供当前市场快照。
    包含 ETF 操作建议摘要。

    Args:
        market_data: fetch_all_valuations 的返回值
        temperature: calculate_market_temperature 的返回值
        position: get_position_advice 的返回值
        etf_recommendations: generate_recommendations 的返回值（可选）

    Returns:
        str: 格式化的市场上下文文本
    """
    indices = market_data.get("indices", [])
    details = temperature.get("details", [])

    # 构建详情查找表
    lookup: dict[str, dict] = {}
    for d in details:
        code = d.get("code", "")
        if code:
            lookup[code] = d

    lines: list[str] = []
    lines.append("【当前市场数据】")

    # 指数估值表格（仅有效数据）
    lines.append("| 指数 | PE | PE历史分位 | 温度 |")
    lines.append("|------|-----|-----------|------|")
    for idx in indices:
        if not idx.get("valid"):
            continue
        name = idx.get("name", "")
        code = idx.get("code", "")
        pe = _fmt_val(idx.get("pe"), decimals=2)
        detail = lookup.get(code, {})
        pct = _fmt_val(detail.get("percentile"), suffix="%", decimals=1)
        label = detail.get("label", "--")
        lines.append(f"| {name} | {pe} | {pct} | {label} |")

    lines.append("")

    # 全市场温度
    lines.append(
        f"全市场温度：{temperature['label']}"
        f"（PE 分位均值 {temperature['avg_percentile']:.1f}%）"
    )

    # 仓位建议
    lines.append(
        f"建议仓位：A股 {position['stock']}% / 债券 {position['bond']}% / 现金 {position['cash']}%"
    )
    lines.append(
        f"150 份框架：总份数 {position.get('total_shares', 150)} 份，"
        f"每份 {position.get('nav_per_share', 10000)} 元"
    )

    # ETF 操作建议摘要
    if etf_recommendations and etf_recommendations.get("recommendations"):
        recs = etf_recommendations["recommendations"]
        buy_recs = [r for r in recs if r["action"] == "buy"]
        sell_recs = [r for r in recs if r["action"] == "sell"]

        if buy_recs or sell_recs:
            lines.append("")
            lines.append("【ETF 操作建议摘要】")
            if buy_recs:
                for r in buy_recs:
                    pe_str = f"{r['pe_percentile']:.1f}%" if r["pe_percentile"] is not None else "--"
                    lines.append(
                        f"  买入 {r['shares']} 份 {r['etf_name']}({r['etf_code']}) "
                        f"— PE 分位 {pe_str}"
                    )
            if sell_recs:
                for r in sell_recs:
                    pe_str = f"{r['pe_percentile']:.1f}%" if r["pe_percentile"] is not None else "--"
                    lines.append(
                        f"  卖出 {r['shares']} 份 {r['etf_name']}({r['etf_code']}) "
                        f"— PE 分位 {pe_str}"
                    )

    return "\n".join(lines)


def _fmt_val(value: float | None, suffix: str = "", decimals: int = 1) -> str:
    """格式化数值为表格字符串，None 时返回占位符 "--"。

    Args:
        value: 待格式化的数值
        suffix: 后缀（如 "%"）
        decimals: 小数位数

    Returns:
        str: 格式化后的字符串
    """
    if value is None:
        return "--"
    return f"{value:.{decimals}f}{suffix}"


class ChatSession:
    """交互式问答会话，维护上下文状态。

    三轮上下文：
    - system_context: 知识底座（静态，会话期间不变）
    - market_context: 当前市场数据 + ETF 推荐（半静态，可刷新）
    - history: 对话历史（最近 N 轮消息）

    Attributes:
        config: 完整配置字典
        client: OpenAI 客户端实例
        system_context: 知识底座系统 Prompt
        market_context: 当前市场数据文本，每次提问时注入
        etf_recommendations: 当前 ETF 推荐结果
        history: 对话历史消息列表，每项 {"role": str, "content": str}
        max_history_rounds: 保留的最大历史轮数
        summary_trigger_rounds: 触发历史压缩的轮数阈值
    """

    def __init__(
        self,
        config: dict,
        market_data: dict,
        temperature: dict,
        position: dict,
        etf_recommendations: dict | None = None,
    ):
        """初始化交互式问答会话。

        Args:
            config: 完整配置字典
            market_data: fetch_all_valuations 的返回值
            temperature: calculate_market_temperature 的返回值
            position: get_position_advice 的返回值
            etf_recommendations: generate_recommendations 的返回值（可选）
        """
        self.config = config
        self.market_data = market_data
        self.temperature = temperature
        self.position = position
        self.etf_recommendations = etf_recommendations

        self.client = build_llm_client(config)
        knowledge_base = load_knowledge_base(config)
        self.system_context = knowledge_base["system_prompt"]
        self.market_context = build_market_context(
            market_data, temperature, position, etf_recommendations,
        )
        self.history: list[dict[str, str]] = []

        self.max_history_rounds = config["chat"].get("max_history_rounds", 10)
        self.summary_trigger = config["chat"].get("summary_trigger_rounds", 8)

    def refresh_market_data(
        self,
        market_data: dict,
        temperature: dict,
        position: dict,
        etf_recommendations: dict | None = None,
    ) -> None:
        """刷新市场数据上下文。

        在用户执行刷新命令时调用，更新市场快照和 ETF 推荐。

        Args:
            market_data: 新的市场估值数据
            temperature: 新的温度评估结果
            position: 新的仓位建议
            etf_recommendations: 新的 ETF 推荐结果（可选）
        """
        self.market_data = market_data
        self.temperature = temperature
        self.position = position
        self.etf_recommendations = etf_recommendations
        self.market_context = build_market_context(
            market_data, temperature, position, etf_recommendations,
        )
        logger.info("市场数据已刷新（含ETF推荐）")

    def ask(self, question: str) -> str:
        """向 LLM 提问。将问题与当前上下文组合后发送。

        流程：
        1. 构建消息列表：system + 压缩后的 history + 当前问题（含 market_context）
        2. 调用 LLM（含重试）
        3. 更新对话历史
        4. 检查是否需要压缩历史

        Args:
            question: 用户提问文本（不含市场上下文）

        Returns:
            str: LLM 回答文本（含免责声明）

        Raises:
            Exception: LLM 调用全部重试失败时抛出
        """
        # 构建消息列表
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_context},
        ]

        # 加入历史消息
        messages.extend(self.history)

        # 当前问题：用户问题 + 市场上下文
        full_question = f"{question}\n\n{self.market_context}"
        messages.append({"role": "user", "content": full_question})

        # 调用 LLM
        answer = self._call_llm(messages)

        # 更新历史（只记录原始问题，不带市场上下文）
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})

        # 检查是否需要压缩历史
        if len(self.history) > self.summary_trigger * 2:
            self._compress_history()

        return answer + DISCLAIMER

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """调用 LLM，含指数退避重试。

        重试次数和退避延迟从 config 读取。

        Args:
            messages: 完整的消息列表

        Returns:
            str: LLM 返回的文本

        Raises:
            Exception: 所有重试耗尽后抛出
        """
        import time

        max_retries = self.config["api"].get("max_retries", 3)
        base_delay = self.config["api"].get("retry_base_delay", 5)
        model = self.config["api"].get("model", "deepseek-chat")
        api_temperature = self.config["api"].get("temperature", 0.7)
        max_tokens = self.config["api"].get("max_tokens", 2000)

        last_exception: Exception | None = None

        for attempt in range(max_retries):
            try:
                logger.info(
                    "LLM 问答调用中 (尝试 %d/%d)...", attempt + 1, max_retries
                )
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=api_temperature,
                    max_tokens=max_tokens,
                )
                content = response.choices[0].message.content
                logger.info("LLM 问答成功，返回 %d 字符", len(content) if content else 0)
                return content or ""

            except Exception as e:
                last_exception = e
                logger.warning(
                    "LLM 问答失败 (尝试 %d/%d): %s", attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.info("等待 %d 秒后重试...", delay)
                    time.sleep(delay)

        raise last_exception  # type: ignore[misc]

    def _compress_history(self) -> None:
        """当历史轮数超过 summary_trigger 阈值时，压缩历史。

        策略：
        1. 保留最近 4 轮对话（8 条消息）
        2. 保留最旧 2 条消息（上下文锚点）
        3. 丢弃中间的旧消息
        4. 确保总消息数不超过 max_history_rounds * 2
        """
        if len(self.history) <= self.summary_trigger * 2:
            return

        max_messages = self.max_history_rounds * 2
        # 保留最近 8 条（4 轮）
        keep_recent = min(8, max_messages // 2)
        recent = self.history[-keep_recent:]

        # 保留最旧 2 条作为锚点
        anchor = self.history[:2]

        self.history = anchor + recent
        logger.info(
            "对话历史已压缩，当前保留 %d 条消息（锚点 %d + 最近 %d）",
            len(self.history),
            len(anchor),
            len(recent),
        )


def run_chat_session(
    config: dict,
    market_data: dict,
    temperature: dict,
    position: dict,
    etf_recommendations: dict | None = None,
) -> None:
    """运行交互式问答主循环。

    流程：
    1. 展示市场快报（含 ETF 操作建议摘要）
    2. 进入对话循环
    3. 识别特殊命令（退出/刷新/帮助）
    4. 每轮回答末尾附带免责声明

    Args:
        config: 完整配置字典
        market_data: fetch_all_valuations 的返回值
        temperature: calculate_market_temperature 的返回值
        position: get_position_advice 的返回值
        etf_recommendations: generate_recommendations 的返回值（可选）
    """
    session = ChatSession(config, market_data, temperature, position, etf_recommendations)

    # ── 展示市场快报 ──
    print("\n═══ E大投资决策助手 · 交互模式 ═══")
    print(
        f"当前温度：{temperature['label']}"
        f"（分位均值 {temperature['avg_percentile']}%）"
    )
    print(
        f"建议仓位：A股 {position['stock']}% / "
        f"债券 {position['bond']}% / "
        f"现金 {position['cash']}%"
    )
    print(f"150 份框架：总份数 {position.get('total_shares', 150)} 份")

    # ETF 操作摘要
    if etf_recommendations and etf_recommendations.get("recommendations"):
        summary = etf_recommendations["summary"]
        recs = etf_recommendations["recommendations"]
        buy_recs = [r for r in recs if r["action"] == "buy"]
        sell_recs = [r for r in recs if r["action"] == "sell"]
        if buy_recs or sell_recs:
            print("ETF 操作建议：")
            for r in buy_recs:
                pe_str = f"{r['pe_percentile']:.1f}%" if r["pe_percentile"] is not None else "--"
                print(f"  买入 {r['shares']} 份 {r['etf_name']}({r['etf_code']}) — PE 分位 {pe_str}")
            for r in sell_recs:
                pe_str = f"{r['pe_percentile']:.1f}%" if r["pe_percentile"] is not None else "--"
                print(f"  卖出 {r['shares']} 份 {r['etf_name']}({r['etf_code']}) — PE 分位 {pe_str}")

    print()
    print("可以问我任何关于当前市场估值的问题（支持 ETF 品种追问）")
    print("输入 'exit' 或 '退出' 退出，输入 'refresh' 刷新数据，输入 'help' 查看帮助")
    print()

    # ── 对话循环 ──
    while True:
        try:
            user_input = input("💬 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        if _is_exit_command(user_input):
            print("再见！")
            break

        if user_input in _REFRESH_COMMANDS:
            # 刷新市场数据
            from scripts.advisor.market_data import fetch_all_valuations
            from scripts.advisor.temperature import calculate_market_temperature
            from scripts.advisor.position import get_position_advice
            from scripts.advisor.etf_recommend import generate_recommendations

            print("🔄 正在刷新市场数据...")
            try:
                new_data = fetch_all_valuations(config, force_refresh=True)
                if new_data["valid_count"] > 0:
                    new_temp = calculate_market_temperature(
                        new_data["indices"], config
                    )
                    new_pos = get_position_advice(new_temp["label"], config)
                    new_etf_recs = generate_recommendations(new_data, config)
                    session.refresh_market_data(new_data, new_temp, new_pos, new_etf_recs)
                    print(
                        f"✅ 已刷新：温度 {new_temp['label']}，"
                        f"分位均值 {new_temp['avg_percentile']}%"
                    )
                    # 显示 ETF 操作摘要
                    summary = new_etf_recs["summary"]
                    if summary["buy_count"] > 0 or summary["sell_count"] > 0:
                        print(
                            f"   ETF 建议：买入 {summary['buy_count']} 个({summary['total_buy_shares']} 份)"
                            f" / 卖出 {summary['sell_count']} 个({summary['total_sell_shares']} 份)"
                        )
                else:
                    print("⚠️ 无法获取有效数据，当前市场数据保持不变")
            except Exception as e:
                logger.error("刷新市场数据失败: %s", e)
                print(f"❌ 刷新失败: {e}")
            continue

        if user_input in {"help", "帮助", "?"}:
            print("命令说明：")
            print("  exit / 退出    -- 结束对话")
            print("  refresh / 刷新 -- 刷新市场数据和ETF推荐")
            print("  help / 帮助 / ? -- 显示此帮助信息")
            print()
            print("你可以问：")
            print("  - 现在市场怎么样？")
            print("  - 沪深300现在能买吗？")
            print("  - 我该买什么？")
            print("  - 纳指ETF怎么看？")
            print()
            continue

        try:
            answer = session.ask(user_input)
            print(f"\n📊 E大助手: {answer}")
            print()  # 空行分隔
        except Exception as e:
            logger.error("问答失败: %s", e, exc_info=True)
            print(f"❌ 抱歉，回答生成失败: {e}")


def _is_exit_command(text: str) -> bool:
    """判断是否为退出命令。

    Args:
        text: 用户输入文本

    Returns:
        bool: 是否为退出命令
    """
    return text.strip().lower() in {"exit", "quit", "q", "退出", "quit()"}
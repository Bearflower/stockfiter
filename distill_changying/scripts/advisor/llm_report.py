"""
E大投资决策助手 AI 分析引擎

将计算好的市场数据 + E大知识底座发送给 DeepSeek LLM，
生成结构化的 E大口吻投资分析报告。
LLM 做"解说员"不做"决策者"。
"""

from __future__ import annotations
import logging
import time

from scripts.shared.llm_utils import build_llm_client
from scripts.advisor.knowledge_base import load_knowledge_base

logger = logging.getLogger(__name__)

# 免责声明模板
DISCLAIMER = "\n\n---\n以上分析仅供参考，不构成投资建议。投资有风险，决策须谨慎。"


def build_analysis_prompt(
    market_data: dict,
    temperature: dict,
    position: dict,
    knowledge_base: dict,
    etf_recommendations: dict | None = None,
) -> tuple[str, str]:
    """构建分析报告的用户 Prompt 和系统 Prompt。

    系统 Prompt 从知识底座（knowledge_base["system_prompt"]）获取，
    用户 Prompt 包含当前市场数据、ETF 操作建议和报告格式要求。

    Args:
        market_data: fetch_all_valuations 的返回值
        temperature: calculate_market_temperature 的返回值
        position: get_position_advice 的返回值
        knowledge_base: load_knowledge_base 的返回值
        etf_recommendations: generate_recommendations 的返回值（可选）

    Returns:
        tuple[str, str]: (system_prompt, user_prompt)
    """
    system_prompt = knowledge_base.get("system_prompt", "")

    # ── 构建各指数估值表格 ──
    indices = market_data.get("indices", [])
    table_lines: list[str] = []
    table_lines.append("| 指数 | PE | PE历史分位 | PB | PB历史分位 | 数据状态 |")
    table_lines.append("|------|-----|-----------|----|-----------|---------|")

    for idx in indices:
        name = idx.get("name", "")
        code = idx.get("code", "")
        display_name = f"{name}({code})"
        pe = _fmt_val(idx.get("pe"), decimals=2)
        pe_pct = _fmt_val(idx.get("pe_percentile"), suffix="%", decimals=1)
        pb = _fmt_val(idx.get("pb"), decimals=2)
        pb_pct = _fmt_val(idx.get("pb_percentile"), suffix="%", decimals=1)
        status = "有效" if idx.get("valid") else idx.get("valid_msg", "无效")
        table_lines.append(
            f"| {display_name} | {pe} | {pe_pct} | {pb} | {pb_pct} | {status} |"
        )

    table_str = "\n".join(table_lines)

    # ── ETF 操作建议段落 ──
    etf_section = ""
    if etf_recommendations and etf_recommendations.get("recommendations"):
        from scripts.advisor.etf_recommend import format_recommendations_text
        etf_text = format_recommendations_text(etf_recommendations)
        etf_section = f"\n【ETF 操作建议（算法生成）】\n{etf_text}\n"

    # ── 用户 Prompt（V2：新增 ETF 操作建议段） ──
    user_prompt = f"""请基于以下 A 股市场估值数据，以 E大投资顾问的视角，
输出一份含 ETF 操作建议的日度投资分析报告。

【当前市场数据】
{table_str}

【市场温度评估】
全市场温度：{temperature['label']}（PE 分位均值 {temperature['avg_percentile']}%）
置信度：{temperature['confidence']}

【仓位建议】
A股：{position['stock']}% / 债券：{position['bond']}% / 现金：{position['cash']}%
150 份框架：总份数 {position.get('total_shares', 150)} 份，每份 {position.get('nav_per_share', 10000)} 元
{etf_section}
请按以下结构输出报告：
1. **今日市场概况**（1-2 句）
2. **估值温度判断**（当前处于什么阶段）
3. **仓位建议及理由**（基于 150 份框架）
4. **ETF 操作建议**（基于上述算法推荐，给出你的分析和操作解读，可以推荐具体品种和份数）
5. **风险提示**
6. **E大风格的金句总结**（一句即可）
"""

    return system_prompt, user_prompt


def generate_report(
    config: dict,
    market_data: dict,
    temperature: dict,
    position: dict,
    etf_recommendations: dict | None = None,
) -> str:
    """调用 LLM 生成分析报告（含 ETF 操作建议）。

    流程：
    1. 加载知识底座
    2. 构建 Prompt（含 ETF 操作建议数据）
    3. 调用 DeepSeek API（含重试）
    4. API 失败时降级为纯数据报告
    5. 追加免责声明

    Args:
        config: 完整配置字典（由 get_config 返回）
        market_data: fetch_all_valuations 的返回值
        temperature: calculate_market_temperature 的返回值
        position: get_position_advice 的返回值
        etf_recommendations: generate_recommendations 的返回值（可选）

    Returns:
        str: Markdown 格式的完整分析报告
    """
    logger.info("正在加载知识底座...")
    kb = load_knowledge_base(config)
    if kb.get("warning"):
        logger.warning("知识底座加载警告: %s", kb["warning"])

    system_prompt, user_prompt = build_analysis_prompt(
        market_data, temperature, position, kb, etf_recommendations,
    )

    try:
        client = build_llm_client(config)
        llm_report = _call_llm_with_retry(
            client, system_prompt, user_prompt, config,
        )
        logger.info("LLM 分析报告生成成功")
        return llm_report + DISCLAIMER

    except Exception as e:
        logger.warning("LLM 调用失败，降级为纯数据报告: %s", e)
        return _build_fallback_report(market_data, temperature, position, config, etf_recommendations)


def _build_fallback_report(
    market_data: dict,
    temperature: dict,
    position: dict,
    config: dict,
    etf_recommendations: dict | None = None,
) -> str:
    """构建降级报告（LLM 不可用时使用）。

    包含：市场数据 + 温度 + 仓位 + ETF 操作建议 + 免责声明。

    Args:
        market_data: fetch_all_valuations 的返回值
        temperature: calculate_market_temperature 的返回值
        position: get_position_advice 的返回值
        config: 完整配置字典
        etf_recommendations: generate_recommendations 的返回值（可选）

    Returns:
        str: Markdown 格式的降级分析报告
    """
    indices = market_data.get("indices", [])
    timestamp = market_data.get("timestamp", "未知")

    lines: list[str] = []
    lines.append("# E大投资决策助手 -- 市场分析报告（降级版）")
    lines.append("")
    lines.append(f"> 数据时间：{timestamp}")
    lines.append("> **注意：AI 分析引擎当前不可用，以下为纯数据摘要。**")
    lines.append("")

    # ── 1. 市场估值一览 ──
    lines.append("## 一、市场估值一览")
    lines.append("")
    lines.append("| 指数 | PE | PE历史分位 | 数据状态 |")
    lines.append("|------|-----|-----------|---------|")
    for idx in indices:
        name = idx.get("name", "")
        code = idx.get("code", "")
        display_name = f"{name}({code})"
        pe = _fmt_val(idx.get("pe"), decimals=2)
        pe_pct = _fmt_val(idx.get("pe_percentile"), suffix="%", decimals=1)
        status = "有效" if idx.get("valid") else "无效"
        lines.append(f"| {display_name} | {pe} | {pe_pct} | {status} |")
    lines.append("")

    # ── 2. 市场温度 ──
    lines.append("## 二、全市场温度")
    lines.append("")
    lines.append(f"- **温度标签**：{temperature['label']}")
    lines.append(f"- **PE 分位均值**：{temperature['avg_percentile']}%")
    lines.append(f"- **置信度**：{temperature['confidence']}")
    lines.append("")

    # ── 3. 仓位建议 ──
    lines.append("## 三、仓位建议")
    lines.append("")
    lines.append(f"- A股：{position['stock']}%")
    lines.append(f"- 债券：{position['bond']}%")
    lines.append(f"- 现金：{position['cash']}%")
    lines.append(f"- 150 份框架：总份数 {position.get('total_shares', 150)} 份")
    lines.append(f"- **说明**：{position['description']}")
    lines.append("")

    # ── 4. ETF 操作建议 ──
    if etf_recommendations and etf_recommendations.get("recommendations"):
        lines.append("## 四、ETF 操作建议")
        lines.append("")
        from scripts.advisor.etf_recommend import format_recommendations_text
        lines.append(format_recommendations_text(etf_recommendations))
        lines.append("")
        next_section = "五"
    else:
        next_section = "四"

    # ── 5/4. 各指数温度详情 ──
    details = temperature.get("details", [])
    if details:
        lines.append(f"## {next_section}、各指数温度详情")
        lines.append("")
        lines.append("| 指数 | PE | PE历史分位 | 温度标签 |")
        lines.append("|------|-----|-----------|---------|")
        for d in details:
            name = d.get("name", "")
            pe = _fmt_val(d.get("pe"), decimals=2)
            pct = _fmt_val(d.get("percentile"), suffix="%", decimals=1)
            label = d.get("label", "--")
            lines.append(f"| {name} | {pe} | {pct} | {label} |")
        lines.append("")

    # ── 免责声明 ──
    lines.append("---")
    disclaimer = config.get("disclaimer", "以上分析仅供参考，不构成投资建议。")
    lines.append(disclaimer)

    return "\n".join(lines)


def _call_llm_with_retry(
    client,
    system_prompt: str,
    user_prompt: str,
    config: dict,
) -> str:
    """调用 LLM，含指数退避重试。

    重试次数：config["api"]["max_retries"]
    退避延迟：config["api"]["retry_base_delay"] * 2^attempt

    Args:
        client: OpenAI 客户端实例（由 build_llm_client 创建）
        system_prompt: 系统 Prompt
        user_prompt: 用户 Prompt
        config: 完整配置字典

    Returns:
        str: LLM 返回的分析报告文本

    Raises:
        Exception: 所有重试耗尽后抛出最后一次的异常
    """
    max_retries = config["api"].get("max_retries", 3)
    base_delay = config["api"].get("retry_base_delay", 5)
    model = config["api"].get("model", "deepseek-chat")
    temperature = config["api"].get("temperature", 0.7)
    max_tokens = config["api"].get("max_tokens", 2000)

    last_exception: Exception | None = None

    for attempt in range(max_retries):
        try:
            logger.info("LLM 调用中 (尝试 %d/%d)...", attempt + 1, max_retries)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            logger.info("LLM 调用成功，返回 %d 字符", len(content) if content else 0)
            return content or ""

        except Exception as e:
            last_exception = e
            logger.warning(
                "LLM 调用失败 (尝试 %d/%d): %s", attempt + 1, max_retries, e
            )
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.info("等待 %d 秒后重试...", delay)
                time.sleep(delay)

    # 所有重试耗尽
    raise last_exception  # type: ignore[misc]


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
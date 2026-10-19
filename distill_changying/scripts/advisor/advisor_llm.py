"""
E大投资决策LLM分析模块

通过 DeepSeek（deepseek-chat）统一进行仓位建议、ETF 推荐和市场解读推理。
单次 LLM 调用同时产出三个输出，失败时由调用方（scheduler.py）降级到规则引擎。

数据流：
  市场估值数据 + ETF 品种池 + E大观点原则 + 150 份框架
    → DeepSeek（deepseek-chat）
    → { position_advice, etf_recommendations, market_commentary }
"""

from __future__ import annotations

import json
import logging
from typing import Any

from scripts.shared.llm_utils import build_llm_client, call_deepseek_json

logger = logging.getLogger(__name__)

# ===================== System Prompt =====================

SYSTEM_PROMPT = """你是一位深度理解"ETF拯救世界"（E大）投资思想的投资顾问。
你的任务是结合当前市场数据和 E大 的投资原则框架，进行一次完整的投资决策分析。

E大 的核心投资原则：
1. 150 份资产配置框架：将总资金分为 150 份，每份金额 = 总资产 / 150。用"份"为单位买卖，避免情绪化操作。
2. 估值温度计：根据 PE 历史分位判断市场温度 — 钻石坑(低) → 低估 → 正常 → 高估 → 泡沫(高)。
3. 不空仓不满仓：无论市场如何，永远持有一定仓位（至少 5%），也永远保留一定现金（至少 10%）。
4. 越跌越买、越涨越卖：估值越低越加仓，估值越高越减仓，逆向操作。
5. 资产配置多元化：A股宽基 + 策略型 + 海外 + 商品 + 债券，多资产分散风险。
6. 用"温度"而非"点位"做决策：不看指数绝对值，看历史分位相对位置。
7. 每次只操作 1 份：E大 从不一次性大笔买卖，每次买入或卖出只操作 1 份，通过多次操作逐步加减仓。这样才不会一次买在最高点或卖在最低点。

【重要约束】
- A股仓位范围：5%-80%，债券仓位范围：10%-50%，现金仓位范围：10%-60%
- 总资产 = 150 份，每份金额由下文给出
- 每个 ETF 品种每次买入或卖出的份数只能是 1 份（不能因为市场极度泡沫就建议一次性卖出多份）
- 每个品种的累计持仓不可超过其 max_shares 上限
- 永远不要建议满仓或空仓"""


def _build_etf_table(etf_pool: list[dict]) -> str:
    """将 ETF 品种池格式化为表格文本。"""
    lines = []
    lines.append(f"{'代码':<8} {'名称':<14} {'类别':<12} {'买入区≤':<8} {'卖出区≥':<8} {'每份':<6} {'上限':<6}")
    lines.append("-" * 70)
    for etf in etf_pool:
        code = etf.get("code", "")
        name = etf.get("name", "")
        cat = etf.get("category", "")
        buy_max = etf.get("buy_zone", {}).get("pe_percentile_max", "")
        sell_min = etf.get("sell_zone", {}).get("pe_percentile_min", "")
        per_trade = etf.get("shares_per_trade", 1)
        max_shares = etf.get("max_shares", 10)
        lines.append(f"{code:<8} {name:<14} {cat:<12} ≤{str(buy_max):<6} ≥{str(sell_min):<6} {per_trade:<6} {max_shares:<6}")
    return "\n".join(lines)


def _build_index_details(temperature: dict) -> str:
    """将各指数详情格式化为表格。"""
    details = temperature.get("details", [])
    if not details:
        return "（无有效指数数据）"
    lines = []
    lines.append(f"{'指数名称':<12} {'代码':<8} {'PE分位%':<10} {'温度':<6}")
    lines.append("-" * 40)
    for d in details:
        name = d.get("name", "")
        code = d.get("code", "")
        pct = d.get("percentile")
        label = d.get("label", "")
        pct_str = f"{pct:.1f}" if pct is not None else "--"
        lines.append(f"{name:<12} {code:<8} {pct_str:<10} {label:<6}")
    return "\n".join(lines)


def _build_principles_section(config: dict) -> str:
    """从观点库加载 E大的通用原则，构建原则上下文部分。

    优先从 观点库.jsonl 加载 principle 类型记录，
    不存在时使用配置中的备用原则。
    """
    import os

    opinions_path = config.get("opinion_matching", {}).get("opinions_path", "")
    if not opinions_path:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        opinions_path = os.path.join(project_root, "docs", "distilled", "观点库.jsonl")

    principles: list[str] = []
    fallback = config.get("opinion_matching", {}).get("fallback_opinions", [])

    if os.path.isfile(opinions_path):
        try:
            from scripts.advisor.opinion_matcher import load_jsonl
            records = load_jsonl(opinions_path)
            principles = [
                r.get("opinion", "") for r in records
                if r.get("source_type") in ("principle", None, "")
                and r.get("opinion")
            ]
        except Exception as e:
            logger.warning("加载观点库失败: %s", e)

    # 如果观点库无数据，用备用观点
    if not principles:
        principles = [fb.get("opinion", "") for fb in fallback if fb.get("opinion")]

    # 截断过长，避免 token 超限
    max_chars = 4000
    result = []
    chars = 0
    for p in principles:
        if chars + len(p) > max_chars:
            result.append(f"（原则库过长，已截断，剩余 {len(principles) - len(result)} 条）")
            break
        result.append(f"- {p}")
        chars += len(p)

    return "\n".join(result) if result else "（暂无 E大原则数据）"


# ===================== User Prompt =====================

ANALYSIS_PROMPT_TEMPLATE = """【E大投资原则参考】
{principles}

【当前市场温度】
- 全市场温度：{temperature_label}
- 平均 PE 分位：{avg_percentile}%
- 置信度：{confidence}

【各指数详情】
{index_details}

【ETF 品种池】
{etf_table}

【150 份资产配置框架】
总份数：{total_shares} 份
每份净值：{nav_per_share} 元

【免责声明】
{disclaimer}

========================
请基于以上数据，以 E大 的投资框架进行分析，输出以下 JSON 格式的结果：

{{
  "position_advice": {{
    "stock_pct": 40,
    "bond_pct": 40,
    "cash_pct": 20,
    "reasoning": "简要说明仓位分配理由（50字以内）",
    "temperature_label": "正常"
  }},
  "etf_recommendations": [
    {{
      "etf_code": "510300",
      "etf_name": "沪深300ETF",
      "action": "buy/sell/hold",
      "shares": 1,
      "reasoning": "操作理由（30字以内）"
    }}
  ],
  "market_commentary": "200-300字的自然语言市场解读，体现 E大 的逆向投资风格和仓位管理思维。分析当前估值状态、各品种的性价比对比，以及接下来的应对策略。语言简洁有力，像 E大 给投资者的建议。"
}}

重要约束：
1. position_advice 的 stock_pct 范围 5-80，bond_pct 范围 10-50，cash_pct 范围 10-60，三者之和必须为 100
2. etf_recommendations 中每个品种的 action 为 "buy" 或 "sell" 时，shares 必须为 1（E大 每次只操作 1 份）
3. action 为 "hold" 时，shares 必须为 0
4. action 只能是 "buy"、"sell" 或 "hold"
5. 只输出 JSON，不要包含其他任何文字。"""


# ===================== Main Function =====================


def call_llm_analysis(
    config: dict[str, Any],
    temperature: dict,
) -> dict[str, Any] | None:
    """执行 LLM 投资分析，返回结构化分析结果。

    单次调用 DeepSeek（deepseek-chat），
    同时输出仓位建议、ETF 推荐和市场解读。

    Args:
        config: advisor 配置字典
        temperature: 市场温度计算结果（来自 temperature.py 的 calculate_market_temperature）

    Returns:
        dict | None: 成功时返回包含 position_advice、etf_recommendations、market_commentary 的字典；
                     失败时返回 None
    """
    llm_cfg = config.get("llm_analysis", {})
    if not llm_cfg.get("enabled", True):
        logger.info("LLM 分析已禁用（llm_analysis.enabled=false），跳过")
        return None

    # 构建参数
    principles_text = _build_principles_section(config)
    index_details = _build_index_details(temperature)
    etf_table = _build_etf_table(config.get("etf_pool", []))

    allocation = config.get("allocation", {})
    total_shares = allocation.get("total_shares", 150)
    nav_per_share = allocation.get("nav_per_share", 10000)
    disclaimer = config.get("disclaimer", "以上分析仅供参考，不构成投资建议。")

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        principles=principles_text,
        temperature_label=temperature.get("label", "未知"),
        avg_percentile=temperature.get("avg_percentile", 50),
        confidence=temperature.get("confidence", "未知"),
        index_details=index_details,
        etf_table=etf_table,
        total_shares=total_shares,
        nav_per_share=nav_per_share,
        disclaimer=disclaimer,
    )

    model = llm_cfg.get("model", "deepseek-chat")
    max_tokens = llm_cfg.get("max_tokens", 4000)
    timeout = llm_cfg.get("timeout", 120)

    logger.info("调用 DeepSeek 进行综合分析...")

    try:
        client = build_llm_client(config)
        result = call_deepseek_json(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            timeout=timeout,
        )

        # 验证结果结构
        validated = _validate_and_normalize(result, config, temperature)
        if validated:
            logger.info(
                "LLM 分析完成: 仓位 A股%d%%/债券%d%%/现金%d%%, "
                "ETF 操作 %d 条, 市场解读 %d 字",
                validated["position_advice"]["stock_pct"],
                validated["position_advice"]["bond_pct"],
                validated["position_advice"]["cash_pct"],
                len(validated.get("etf_recommendations", [])),
                len(validated.get("market_commentary", "")),
            )
            return validated

        logger.warning("LLM 分析结果校验未通过，返回 None")
        return None

    except Exception as e:
        logger.error("LLM 综合分析失败: %s", e, exc_info=True)
        return None


def _load_position_bounds(config: dict[str, Any]) -> dict[str, dict[str, int]] | None:
    """从 advisor 配置读取仓位上下界。

    Returns:
        dict | None: 形如 {"stock_pct": {"min": 5, "max": 80}, ...}；
                     配置缺失或格式错误时返回 None（禁止硬编码 fallback）
    """
    bounds = config.get("position_bounds")
    if not isinstance(bounds, dict):
        logger.error("缺少 position_bounds 配置节，无法进行仓位归一化")
        return None
    required = ("stock_pct", "bond_pct", "cash_pct")
    parsed: dict[str, dict[str, int]] = {}
    for key in required:
        entry = bounds.get(key)
        if not isinstance(entry, dict):
            logger.error("position_bounds 缺少 %s 条目", key)
            return None
        mn, mx = entry.get("min"), entry.get("max")
        if not isinstance(mn, (int, float)) or not isinstance(mx, (int, float)):
            logger.error("position_bounds.%s 的 min/max 必须是数字: min=%s max=%s", key, mn, mx)
            return None
        mn, mx = int(mn), int(mx)
        if mn > mx:
            logger.error("position_bounds.%s 的 min=%d 不能大于 max=%d", key, mn, mx)
            return None
        parsed[key] = {"min": mn, "max": mx}
    # 三项下界之和必须 <= 100，否则算法根本不可能凑出总和 100
    lower_sum = sum(v["min"] for v in parsed.values())
    upper_sum = sum(v["max"] for v in parsed.values())
    if lower_sum > 100:
        logger.error("仓位下界之和 %d > 100，无法满足总和约束", lower_sum)
        return None
    if upper_sum < 100:
        logger.error("仓位上界之和 %d < 100，无法满足总和约束", upper_sum)
        return None
    return parsed


def _normalize_position_with_bounds(
    raw: dict[str, float],
    bounds: dict[str, dict[str, int]],
) -> dict[str, int] | None:
    """有上下界约束的总和归一化。

    算法：先把每项 clamp 到各自下界；之后每轮计算当前总和与 100 的差值，
    只把剩余差额按比例分配给尚未触及上/下界的类别。最多迭代若干轮防死循环。

    Args:
        raw: LLM 原始输出的仓位比例字典（键为 stock_pct / bond_pct / cash_pct）
        bounds: _load_position_bounds 返回的上下界配置

    Returns:
        dict | None: 成功返回 int 类型的归一化结果（总和精确 100 且各项在界内）；
                     无法满足约束时返回 None（禁止输出越界结果）
    """
    keys = ("stock_pct", "bond_pct", "cash_pct")
    # 初始值：同时 clamp 到各自上下界（先把明显越界的拉回来）
    vals: dict[str, float] = {}
    for k in keys:
        try:
            raw_val = float(raw.get(k, 0))
        except (TypeError, ValueError):
            logger.error("仓位比例 %s 不是数字: %r", k, raw.get(k))
            return None
        vals[k] = max(bounds[k]["min"], min(bounds[k]["max"], raw_val))

    max_iter = 30  # 足够收敛，防死循环
    for _ in range(max_iter):
        total = sum(vals.values())
        diff = 100 - total
        if diff == 0:
            break

        if diff > 0:
            # 需要加：找还有上界空间的类别
            candidates = [
                (k, bounds[k]["max"] - vals[k])
                for k in keys
                if vals[k] < bounds[k]["max"]
            ]
            if not candidates:
                logger.error("仓位无法归一化到 100: 还需 +%.1f 但所有类别都已触及上界", diff)
                return None
            capacity = sum(c for _, c in candidates)
            for k, cap in candidates:
                add = min(cap, diff * cap / capacity)
                vals[k] += add
        else:
            # 需要减：找还有下界空间的类别
            candidates = [
                (k, vals[k] - bounds[k]["min"])
                for k in keys
                if vals[k] > bounds[k]["min"]
            ]
            if not candidates:
                logger.error("仓位无法归一化到 100: 还需 %.1f 但所有类别都已触及下界", diff)
                return None
            reducible = sum(r for _, r in candidates)
            for k, r in candidates:
                sub = min(r, (-diff) * r / reducible)
                vals[k] -= sub
    else:
        logger.error("仓位归一化超过最大迭代次数仍未收敛")
        return None

    # 收尾：四舍五入为整数，同时再次校验总和和上下界
    result = {k: int(round(vals[k])) for k in keys}
    final_total = sum(result.values())
    if final_total != 100:
        # 用差值修正最大一项（整数四舍五入导致的 ±1~2 误差）
        delta = 100 - final_total
        # 找剩余容量最大的方向（加或减）
        if delta > 0:
            pick = max(keys, key=lambda k: bounds[k]["max"] - result[k])
        else:
            pick = max(keys, key=lambda k: result[k] - bounds[k]["min"])
        result[pick] += delta

    # 最终硬性断言
    if sum(result.values()) != 100:
        logger.error("仓位归一化后总和仍为 %d，放弃输出", sum(result.values()))
        return None
    for k in keys:
        if not (bounds[k]["min"] <= result[k] <= bounds[k]["max"]):
            logger.error(
                "仓位归一化后 %s=%d 越界 [%d,%d]，放弃输出",
                k, result[k], bounds[k]["min"], bounds[k]["max"],
            )
            return None
    return result


def _validate_and_normalize(
    result: Any,
    config: dict[str, Any],
    temperature: dict,
) -> dict[str, Any] | None:
    """验证并规范化 LLM 返回的分析结果。

    确保结果包含有效的 position_advice、etf_recommendations 和 market_commentary。
    仓位处理使用「有上下界的总和归一化」，禁止输出越界结果；
    上下界从 config.position_bounds 读取。
    ETF 推荐列表应用 max_shares 约束修正。

    Args:
        result: LLM 返回的原始 JSON 对象
        config: 配置字典（读取 position_bounds 和 etf_pool）
        temperature: 市场温度计算结果（兜底 temperature_label 用）

    Returns:
        dict | None: 规范化后的结果；关键约束无法满足时返回 None
    """
    if not isinstance(result, dict):
        logger.warning("LLM 返回非 dict 类型: %s", type(result).__name__)
        return None

    # 加载仓位上下界（只从 config 读，缺失直接失败）
    bounds = _load_position_bounds(config)
    if bounds is None:
        logger.error("仓位上下界加载失败，拒绝本次 LLM 输出")
        return None

    # 仓位建议验证
    pa = result.get("position_advice")
    if not isinstance(pa, dict):
        logger.warning("缺少 position_advice")
        return None

    stock = pa.get("stock_pct")
    bond = pa.get("bond_pct")
    cash = pa.get("cash_pct")

    if not all(isinstance(v, (int, float)) for v in (stock, bond, cash)):
        logger.warning("仓位比例类型错误: stock=%s bond=%s cash=%s", type(stock), type(bond), type(cash))
        return None

    normalized = _normalize_position_with_bounds(
        {"stock_pct": float(stock), "bond_pct": float(bond), "cash_pct": float(cash)},
        bounds,
    )
    if normalized is None:
        logger.error(
            "仓位比例 stock=%s bond=%s cash=%s 无法在约束内归一化，放弃输出",
            stock, bond, cash,
        )
        return None

    stock, bond, cash = normalized["stock_pct"], normalized["bond_pct"], normalized["cash_pct"]
    logger.info("仓位归一化完成: stock=%d bond=%d cash=%d (原始 LLM: stock=%s bond=%s cash=%s)",
                stock, bond, cash, stock, bond, cash)

    # ETF 推荐验证
    etf_recs = result.get("etf_recommendations", [])
    if not isinstance(etf_recs, list):
        etf_recs = []
        logger.warning("etf_recommendations 不是列表，已重置为空")

    # 构建 etf_pool 查找表（用于 shares_per_trade 和 max_shares 约束）
    etf_pool: list[dict] = config.get("etf_pool", [])
    etf_max_shares: dict[str, int] = {
        e.get("code", ""): e.get("max_shares", 10) for e in etf_pool
    }
    etf_shares_per_trade: dict[str, int] = {
        e.get("code", ""): e.get("shares_per_trade", 1) for e in etf_pool
    }

    validated_etf_recs = []
    for rec in etf_recs:
        if not isinstance(rec, dict):
            continue
        code = rec.get("etf_code", "")
        action = rec.get("action", "hold")
        shares = rec.get("shares", 0)
        name = rec.get("etf_name", "")

        # 白名单校验：LLM 返回的 etf_code 必须在 etf_pool 中，否则丢弃，
        # 防止模型幻觉出不存在的品种（否则会以默认 max_shares 混入买卖建议）
        if code not in etf_max_shares:
            logger.warning("LLM 返回未在 etf_pool 中的品种 %s(%s)，已丢弃", name, code)
            continue

        if action not in ("buy", "sell", "hold"):
            action = "hold"

        # shares_per_trade 约束：E大 每次只操作 1 份（或配置中指定的份数）
        max_per_trade = etf_shares_per_trade.get(code, 1)
        if action in ("buy", "sell") and shares > max_per_trade:
            logger.info("ETF %s(%s) 建议份数 %d 超过单次上限 %d，已修正为 %d",
                        name, code, shares, max_per_trade, max_per_trade)
            shares = max_per_trade
        if action == "hold":
            shares = 0

        validated_etf_recs.append({
            "etf_code": code,
            "etf_name": name,
            "action": action,
            "shares": shares,
            "reasoning": rec.get("reasoning", ""),
        })

    # 市场解读
    commentary = result.get("market_commentary", "")
    if not isinstance(commentary, str):
        commentary = str(commentary) if commentary else ""

    return {
        "position_advice": {
            "stock_pct": stock,
            "bond_pct": bond,
            "cash_pct": cash,
            "reasoning": pa.get("reasoning", ""),
            "temperature_label": pa.get("temperature_label", temperature.get("label", "未知")),
        },
        "etf_recommendations": validated_etf_recs,
        "market_commentary": commentary,
    }
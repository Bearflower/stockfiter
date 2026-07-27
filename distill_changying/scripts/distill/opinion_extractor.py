"""
观点提取模块

从三个来源提取结构化观点，合并为统一的观点库：
1. 操作记录 (operation)：从 state.json 的发车帖 entries 中提取买卖操作
2. 市场判断 (observation)：从 state.json 的微博精选 entries 中提取独立观点
3. 通用原则 (principle)：从核心观点矿脉.md + 金句库.md 通过 LLM 提取

输出 观点库.jsonl，供后续向量检索和市场语境匹配使用。

每条观点包含：
  - opinion: 观点内容
  - source_type: 来源类型（operation / observation / principle）
  - time: ISO 时间戳
  - topic: 所属主题
  - market_condition: 适用市场条件标签
  - keywords: 关键词列表
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from scripts.distill.record_extractor import _build_blog_date_map
from scripts.shared.llm_utils import build_llm_client, parse_json_response

logger = logging.getLogger(__name__)

EXTRACT_PROMPT_TEMPLATE = """你是一位知识管理专家。请从以下投资博主的分析报告中，提取结构化的观点条目。

要求：
1. 每条观点用一句话清晰表达（20-60字）
2. 为每条观点标注主题标签（从以下选择：{topic_tags}）
3. 为每条观点标注适用市场条件（从以下选择：{market_condition_tags}）
4. 提取3-5个关键词

【核心观点矿脉】
{veins_text}

【金句库】
{quotes_text}

请以 JSON 数组格式输出，每个元素格式如下：
{{
  "opinion": "观点内容（简洁有力的一句话）",
  "topic": "主题标签",
  "market_condition": "适用市场条件",
  "keywords": ["关键词1", "关键词2", "关键词3"]
}}

只返回 JSON 数组，不要包含其他任何文字。"""


def load_source_texts(output_dir: str) -> tuple[str, str]:
    """加载蒸馏产物文本。

    Args:
        output_dir: 蒸馏产物输出目录绝对路径

    Returns:
        tuple[str, str]: (核心观点矿脉文本, 金句库文本)
    """
    veins_path = os.path.join(output_dir, "核心观点矿脉.md")
    quotes_path = os.path.join(output_dir, "金句库.md")

    veins_text = ""
    quotes_text = ""

    if os.path.isfile(veins_path):
        with open(veins_path, "r", encoding="utf-8") as f:
            veins_text = f.read()
        logger.info("已加载核心观点矿脉: %d 字符", len(veins_text))
    else:
        logger.warning("核心观点矿脉.md 不存在: %s", veins_path)

    if os.path.isfile(quotes_path):
        with open(quotes_path, "r", encoding="utf-8") as f:
            quotes_text = f.read()
        logger.info("已加载金句库: %d 字符", len(quotes_text))
    else:
        logger.warning("金句库.md 不存在: %s", quotes_path)

    return veins_text, quotes_text


def _truncate_text(text: str, max_chars: int) -> str:
    """截断文本到最大字符数，保持结构完整。

    Args:
        text: 原始文本
        max_chars: 最大字符数

    Returns:
        str: 截断后的文本
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n（文本过长，已截断）"


def _load_state_json(path: str) -> dict[str, Any]:
    """加载 state.json 并过滤无效条目。

    只保留至少包含 operations、weibo_opinions 或 product_opinions 之一的条目。

    Args:
        path: state.json 的绝对路径

    Returns:
        dict[str, Any]: 过滤后的 state 字典
    """
    if not os.path.isfile(path):
        logger.warning("state.json 不存在: %s", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("加载 state.json 失败: %s", e)
        return {}

    if not isinstance(state, dict):
        logger.error("state.json 格式错误，期望 dict")
        return {}

    # 过滤无效条目：无 operations、无 weibo_opinions 且无 product_opinions 的跳过
    valid_entries = {}
    for file_id, entry in state.items():
        has_ops = bool(entry.get("operations") and isinstance(entry["operations"], list))
        has_obs = bool(entry.get("weibo_opinions") and isinstance(entry["weibo_opinions"], list))
        has_prod = bool(entry.get("product_opinions") and isinstance(entry["product_opinions"], list))
        if has_ops or has_obs or has_prod:
            valid_entries[file_id] = entry

    skipped = len(state) - len(valid_entries)
    if skipped:
        logger.info("跳过了 %d 个无有效数据的 state 条目", skipped)
    logger.info("加载 state.json: %d 个有效条目", len(valid_entries))
    return valid_entries


def _extract_operations_from_state(
    state: dict[str, Any],
    blog_date_map: dict[str, str] | None = None,
) -> list[dict]:
    """从 state 中提取操作记录观点。

    遍历每个条目的 operations 字段，构造标准观点格式输出。

    Args:
        state: 已过滤的 state 字典
        blog_date_map: 文件标识符到实际发布日期的映射，传入后将使用
                      真实发布日期而非 processed_at

    Returns:
        list[dict]: 操作记录观点列表
    """
    opinions: list[dict] = []
    date_map = blog_date_map or {}

    for file_id, entry in state.items():
        ops = entry.get("operations")
        if not ops or not isinstance(ops, list):
            continue

        # 优先使用从文件名提取的实际发布日期，无法提取时回退 processed_at
        extracted_date = date_map.get(file_id, "")
        record_time = extracted_date if extracted_date else entry.get("processed_at", "")

        for op in ops:
            if not isinstance(op, dict):
                continue

            # 构造观点文本: {plan}计划 {action}{fund}({code})
            plan = op.get("plan", "")
            action = op.get("action", "")
            fund = op.get("fund", "")
            code = op.get("code", "")

            parts = []
            if plan:
                parts.append(f"{plan}计划")
            action_fund = f"{action}{fund}" if action or fund else ""
            if code:
                action_fund += f"({code})"
            if action_fund:
                parts.append(action_fund)
            opinion_text = " ".join(parts) if parts else "未知操作"

            # 从操作记录中提取关键词
            keywords = []
            for kw in [action, fund, code, plan]:
                if kw and str(kw).strip():
                    keywords.append(str(kw).strip())

            opinions.append({
                "opinion": opinion_text,
                "source_type": "operation",
                "time": record_time,
                "topic": "交易操作",
                "market_condition": "通用",
                "keywords": keywords,
            })

    logger.info("从 state 中提取了 %d 条操作记录观点", len(opinions))
    return opinions


def _extract_observations_from_state(
    state: dict[str, Any],
    blog_date_map: dict[str, str] | None = None,
) -> list[dict]:
    """从 state 中提取市场判断观点。

    遍历每个条目的 weibo_opinions 字段，构造标准观点格式输出。

    Args:
        state: 已过滤的 state 字典
        blog_date_map: 文件标识符到实际发布日期的映射，传入后将使用
                      真实发布日期而非 processed_at

    Returns:
        list[dict]: 市场判断观点列表
    """
    opinions: list[dict] = []
    date_map = blog_date_map or {}

    for file_id, entry in state.items():
        weibo_opinions = entry.get("weibo_opinions")
        if not weibo_opinions or not isinstance(weibo_opinions, list):
            continue

        # 优先使用从文件名提取的实际发布日期，无法提取时回退 processed_at
        extracted_date = date_map.get(file_id, "")
        record_time = extracted_date if extracted_date else entry.get("processed_at", "")

        topic = entry.get("topic", "")
        entry_keywords = entry.get("keywords", [])

        for opinion_text in weibo_opinions:
            if not isinstance(opinion_text, str) or not opinion_text.strip():
                continue

            opinions.append({
                "opinion": opinion_text.strip(),
                "source_type": "observation",
                "time": record_time,
                "topic": topic,
                "market_condition": "通用",
                "keywords": list(entry_keywords) if isinstance(entry_keywords, list) else [],
            })

    logger.info("从 state 中提取了 %d 条市场判断观点", len(opinions))
    return opinions


def _extract_product_opinions_from_state(
    state: dict[str, Any],
    blog_date_map: dict[str, str] | None = None,
) -> list[dict]:
    """从 state 中提取品种观点。

    遍历每个条目的 product_opinions 字段，构造标准观点格式输出。
    向下兼容：旧 state.json 没有 product_opinions 字段时不出错。

    Args:
        state: 已过滤的 state 字典
        blog_date_map: 文件标识符到实际发布日期的映射，传入后将使用
                      真实发布日期而非 processed_at

    Returns:
        list[dict]: 品种观点列表
    """
    opinions: list[dict] = []
    date_map = blog_date_map or {}

    for file_id, entry in state.items():
        product_opinions = entry.get("product_opinions")
        if not product_opinions or not isinstance(product_opinions, list):
            continue

        extracted_date = date_map.get(file_id, "")
        record_time = extracted_date if extracted_date else entry.get("processed_at", "")

        for po in product_opinions:
            if not isinstance(po, dict):
                continue
            opinion_text = po.get("opinion", "")
            if not opinion_text:
                continue

            # 构造 opinion："{品种}: {观点}"
            product = po.get("product", "")
            aspect = po.get("aspect", "评价")
            full_opinion = f"{product}: {opinion_text}" if product else opinion_text

            opinions.append({
                "opinion": full_opinion,
                "source_type": "product_opinion",
                "time": record_time,
                "topic": f"品种分析-{product}" if product else "品种分析",
                "market_condition": "通用",
                "keywords": [product, aspect] if product else [aspect],
            })

    logger.info("从 state 中提取了 %d 条品种观点", len(opinions))
    return opinions


def _extract_principles_from_source(
    config: dict[str, Any],
    output_dir: str,
) -> list[dict]:
    """从核心观点矿脉 + 金句库中通过 LLM 提取通用原则。

    保留原有的 LLM 提取逻辑。

    Args:
        config: 完整配置字典
        output_dir: 蒸馏产物输出目录

    Returns:
        list[dict]: 通用原则观点列表
    """
    veins_text, quotes_text = load_source_texts(output_dir)

    if not veins_text and not quotes_text:
        logger.warning("无有效的蒸馏产物文本，跳过原则提取")
        return []

    max_chars = config.get("opinion_extraction", {}).get("max_source_chars", 8000)
    veins_text = _truncate_text(veins_text, max_chars)
    quotes_text = _truncate_text(quotes_text, max_chars)

    extract_config = config.get("opinion_extraction", {})
    topic_tags = extract_config.get("topic_tags", ["估值判断", "仓位管理", "逆向投资", "风险控制", "资产配置", "投资心法", "市场周期", "策略执行"])
    market_condition_tags = extract_config.get("market_condition_tags", ["泡沫高估", "高估区", "正常区", "低估区", "钻石坑", "市场恐慌", "市场狂热", "通用"])

    prompt = EXTRACT_PROMPT_TEMPLATE.format(
        veins_text=veins_text,
        quotes_text=quotes_text,
        topic_tags="、".join(topic_tags),
        market_condition_tags="、".join(market_condition_tags),
    )

    client = build_llm_client(config)
    api_config = config["api"]

    logger.info("调用 LLM 从蒸馏产物中提取通用原则...")
    try:
        response = client.chat.completions.create(
            model=api_config.get("deep_model", api_config["model"]),
            messages=[
                {"role": "system", "content": "你是一位知识管理专家，擅长从文本中提取结构化信息。请只输出 JSON 数组。"},
                {"role": "user", "content": prompt},
            ],
            temperature=extract_config.get("temperature", 0.2),
            max_tokens=extract_config.get("max_tokens", 4000),
        )
        content = response.choices[0].message.content or ""
    except Exception as e:
        logger.error("LLM 调用失败: %s", e)
        return []

    try:
        raw_opinions = parse_json_response(content)
    except json.JSONDecodeError as e:
        logger.error("LLM 返回内容无法解析为 JSON: %s", e)
        logger.debug("原始返回内容: %s", content[:500])
        return []

    if not isinstance(raw_opinions, list):
        logger.error("LLM 返回的不是 JSON 数组")
        return []

    # 原则记录使用当前处理时间作为时间戳（没有具体发布日期）
    now_iso = datetime.now(timezone.utc).isoformat()

    opinions = []
    for item in raw_opinions:
        if not isinstance(item, dict):
            continue
        if not item.get("opinion"):
            continue
        opinions.append({
            "opinion": item.get("opinion", ""),
            "source_type": "principle",
            "time": now_iso,
            "topic": item.get("topic", "通用"),
            "market_condition": item.get("market_condition", "通用"),
            "keywords": item.get("keywords", []),
        })

    logger.info("成功提取 %d 条通用原则", len(opinions))
    return opinions


def extract_opinions(
    config: dict[str, Any],
    output_dir: str | None = None,
) -> list[dict]:
    """从四个来源提取结构化观点，合并为统一的观点库。

    来源包括：
    1. operation: 从 state.json 的发车帖 entries 中提取操作记录
    2. observation: 从 state.json 的微博精选 entries 中提取市场判断
    3. product_opinion: 从 state.json 的所有 entries 中提取品种观点
    4. principle: 从核心观点矿脉.md + 金句库.md 通过 LLM 提取通用原则

    Args:
        config: 完整配置字典
        output_dir: 输出目录，默认从 config 读取

    Returns:
        list[dict]: 结构化观点列表，包含 source_type 字段区分来源
    """
    if output_dir is None:
        output_dir = config["paths"]["output_dir"]

    all_opinions: list[dict] = []

    # --- 来源一：从 state.json 提取操作记录 ---
    state_path = config.get("paths", {}).get("state_file", "")
    if not state_path:
        state_path = os.path.join(output_dir, ".state.json")
    if not os.path.isabs(state_path):
        state_path = os.path.join(os.path.dirname(output_dir), state_path)
    state = _load_state_json(state_path)
    if state:
        # 构建博客日期映射，用于替换 processed_at 为实际发布日期
        blog_dir = config.get("paths", {}).get("blog_dir", "")
        blog_date_map = _build_blog_date_map(blog_dir) if blog_dir and os.path.isdir(blog_dir) else {}

        all_opinions.extend(_extract_operations_from_state(state, blog_date_map))
        all_opinions.extend(_extract_observations_from_state(state, blog_date_map))
        # 来源三：提取品种观点（仅当配置启用时）
        product_config = config.get("product_opinion", {})
        if product_config.get("enabled", True):
            all_opinions.extend(_extract_product_opinions_from_state(state, blog_date_map))
    else:
        logger.warning("state.json 为空或不存在，跳过操作记录、市场判断和品种观点提取")

    # --- 来源四：从蒸馏产物提取通用原则 ---
    principles = _extract_principles_from_source(config, output_dir)
    all_opinions.extend(principles)

    logger.info(
        "观点提取完成: 操作记录 %d 条, 市场判断 %d 条, 品种观点 %d 条, 通用原则 %d 条, 共 %d 条",
        sum(1 for o in all_opinions if o.get("source_type") == "operation"),
        sum(1 for o in all_opinions if o.get("source_type") == "observation"),
        sum(1 for o in all_opinions if o.get("source_type") == "product_opinion"),
        sum(1 for o in all_opinions if o.get("source_type") == "principle"),
        len(all_opinions),
    )
    return all_opinions


def write_opinions(opinions: list[dict], output_dir: str) -> str:
    """将观点列表写入 观点库.jsonl。

    Args:
        opinions: 观点列表
        output_dir: 输出目录绝对路径

    Returns:
        str: 写入的文件绝对路径
    """
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, "观点库.jsonl")

    with open(file_path, "w", encoding="utf-8") as f:
        for opinion in opinions:
            f.write(json.dumps(opinion, ensure_ascii=False) + "\n")

    logger.info("观点库已写入: %s (%d 条)", file_path, len(opinions))
    return file_path


def load_opinions(opinions_path: str) -> list[dict]:
    """从 观点库.jsonl 加载观点列表。

    自动检测旧格式并补充缺失字段（向下兼容）。

    Args:
        opinions_path: 观点库文件路径

    Returns:
        list[dict]: 观点列表
    """
    if not os.path.isfile(opinions_path):
        logger.warning("观点库文件不存在: %s", opinions_path)
        return []

    opinions = []
    compat_count = 0
    with open(opinions_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("跳过无法解析的行: %s", line[:80])
                continue

            # 向下兼容：旧格式缺少 source_type 和 time 字段
            if "source_type" not in item:
                item["source_type"] = "principle"
                item["time"] = datetime.now(timezone.utc).isoformat()
                compat_count += 1

            opinions.append(item)

    if compat_count:
        logger.info("已兼容处理 %d 条旧格式观点（补充 source_type=principle）", compat_count)
    logger.info("已加载 %d 条观点", len(opinions))
    return opinions
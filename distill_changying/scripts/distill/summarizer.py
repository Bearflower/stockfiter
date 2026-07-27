"""
LLM 摘要引擎

调用 OpenAI API 对博客文章进行摘要提取、关键词提取和主题分类。
支持三种内容类型：
- 发车帖（交易记录）：提取结构化操作记录
- 微博精选（多条目）：提取多条独立观点
- 独立文章（标准）：提取核心观点摘要
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from scripts.shared.llm_utils import build_llm_client, parse_json_response

logger = logging.getLogger(__name__)

# 标准摘要 Prompt
_STANDARD_PROMPT = """你是一位专业的投资内容分析师。请仔细阅读以下投资博主的文章，完成以下任务：
1. 用一句不超过100字的中文概括其核心观点。
2. 提取3-5个最能代表本文主题的关键词。
3. 判断文章主题分类（投资理念/交易操作/市场分析/品种分析/问答互动/年度回顾/其他）。
4. 提取文中涉及的具体品种讨论（product_opinions）。
   关注的品种包括但不限于：{product_list}
   如果文中讨论了具体品种，提取其估值判断、买卖时机、收益率数据、长期评价等。
   品种名称必须使用标准名称，如"红利"而非"中证红利"、"中证500"而非"500"。
   如果文中没有涉及任何品种，product_opinions 可以为空数组。
请严格按以下JSON格式输出，不要包含任何其他文字：
{{"summary": "...", "keywords": ["...", "..."], "topic": "...", "product_opinions": [{{"product": "品种名称", "opinion": "品种相关观点", "aspect": "估值/买卖/评价/策略"}}]}}

文章内容：
{content}"""

# 发车帖专用 Prompt：提取结构化操作记录
_TRANSACTION_PROMPT = """你是一位投资记录分析师。以下是E大(ETF拯救世界)的一篇"发车帖"(交易操作公告)，
其中包含基金买卖操作和简短评论。请提取结构化信息。

要求：
1. 用一句话概括本次操作（含计划名、品种、动作、收益率）。
2. 提取3-5个关键词。
3. 主题固定为"交易操作"。
4. 按以下JSON格式输出operations数组（可以有多条操作）：
5. 从发车帖的评论中提取对品种的定性评价，作为product_opinions（如涉及品种的估值判断、策略定位等）。
   品种名称必须使用标准名称，如"红利"而非"中证红利"、"中证500"而非"500"。
   如果评论中没有涉及品种评价，product_opinions 可以为空数组。

{{
  "summary": "2026年6月: 150计划卖出一份建信中证500(000478)，收益率76%，计划清仓",
  "keywords": ["建信中证500", "卖出", "150计划", "止盈"],
  "topic": "交易操作",
  "operations": [
    {{
      "plan": "150/S/无",
      "action": "买入/卖出/调仓",
      "fund": "基金名称",
      "code": "基金代码(如有)",
      "yield_pct": 收益率数值(无则0),
      "quantity": 份数(无则0),
      "memo": "简短备注"
    }}
  ],
  "product_opinions": [
    {{"product": "品种标准名称", "opinion": "品种相关定性评价", "aspect": "估值/买卖/评价/策略"}}
  ]
}}

文章内容：
{content}"""

# 微博精选专用 Prompt：提取多条独立观点（含品种观点分层）
_WEIBO_PROMPT = """你是一位投资内容分析师。以下是E大(ETF拯救世界)的一篇微博精选合集，
包含多条在不同日期、不同市场环境中发布的短微博。

请完成以下任务：
1. 用一句话概括本月微博的整体主题。
2. 提取3-5个最高频的关键词。
3. 判断整体主题分类（投资理念/交易操作/市场分析/品种分析/问答互动/年度回顾/其他）。
4. 提取观点（分两层）：
   a. 品种观点(product_opinions)：对具体品种的讨论。
      关注的品种包括但不限于：{product_list}
      每条观点必须包含品种名称，至少提取3条，上限不限。
      重点关注：品种的估值判断、买卖时机、收益率数据、长期评价、策略定位（如"非卖品"、"核心持仓"等）。
      品种名称必须使用标准名称，如"红利"而非"中证红利"、"中证500"而非"500"。
   b. 通用观点(weibo_opinions)：投资理念、市场判断等不针对特定品种的观点，3-5条。

请严格按以下JSON格式输出：

{{
  "summary": "整体主题概括",
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "topic": "主题分类",
  "product_opinions": [
    {{"product": "品种标准名称", "opinion": "品种相关观点", "aspect": "估值/买卖/评价/策略"}}
  ],
  "weibo_opinions": [
    "独立观点1",
    "独立观点2",
    "独立观点3"
  ]
}}

文章内容：
{content}"""


def _build_product_list_str(config: dict[str, Any]) -> str:
    """从配置中构建品种列表字符串，用于注入 Prompt。

    Args:
        config: 完整的配置字典

    Returns:
        str: 以"、"分隔的品种名称列表，如"红利、中证500、医药、..."
    """
    product_config = config.get("product_opinion", {})
    if not product_config.get("enabled", True):
        return ""
    products = product_config.get("target_products", [])
    if not products:
        return ""
    return "、".join(products)


def classify_content(filename: str, content: str, config: dict[str, Any] | None = None) -> str:
    """根据文件名和内容判断博客类型。

    分类关键词从配置文件的 content_classification 节读取，
    不硬编码在代码中。

    Args:
        filename: 文件名（含扩展名）
        content: 文章内容
        config: 完整的配置字典（可选，为空时使用默认关键词）

    Returns:
        str: 类型标识："transaction" / "weibo" / "standard"
    """
    # 从配置读取分类关键词，配置缺失时使用默认值
    cc = (config or {}).get("content_classification", {})
    trans_kw = cc.get("transaction_keywords", ["长赢.*投资计划", "ETF计划", "文字发车"])
    weibo_kw = cc.get("weibo_keywords", ["微博精选", "微博"])

    # 发车帖
    trans_pattern = "|".join(trans_kw)
    if re.search(trans_pattern, filename):
        return "transaction"

    # 微博精选
    weibo_pattern = "|".join(weibo_kw)
    if re.search(weibo_pattern, filename):
        return "weibo"

    return "standard"


def summarize_article(content: str, config: dict[str, Any], filename: str = "") -> dict[str, Any]:
    """对单篇文章进行摘要提取、关键词提取和主题分类。

    根据文件类型自动分派不同的处理 prompt：
    - 发车帖 → 提取结构化操作记录
    - 微博精选 → 提取多条独立观点（含品种观点分层）
    - 独立文章 → 标准摘要（含品种观点提取）

    Args:
        content: 博客文章的纯文本内容
        config: 完整的配置字典
        filename: 文件名（用于判断类型），可为空

    Returns:
        dict: 包含 summary、keywords、topic 的字典，
              发车帖额外包含 operations 和 product_opinions 字段，
              微博精选额外包含 weibo_opinions 和 product_opinions 字段，
              独立文章额外包含 product_opinions 字段。
    """
    max_chars = config["truncation"]["max_chars"]
    if len(content) > max_chars:
        logger.warning(
            "文章内容过长（%d 字符），截断到 %d 字符（上限）",
            len(content),
            max_chars,
        )
        content = content[:max_chars]

    # 判断内容类型
    content_type = classify_content(filename, content, config)
    logger.info("内容类型识别: %s (file=%s)", content_type, filename or "未知")

    # 构建品种列表字符串（用于注入 Prompt）
    product_list_str = _build_product_list_str(config)

    if content_type == "transaction":
        prompt = _TRANSACTION_PROMPT.replace("{content}", content)
    elif content_type == "weibo":
        prompt = _WEIBO_PROMPT.replace("{content}", content)
        if product_list_str:
            prompt = prompt.replace("{product_list}", product_list_str)
    else:
        prompt = _STANDARD_PROMPT.replace("{content}", content)
        if product_list_str:
            prompt = prompt.replace("{product_list}", product_list_str)

    client = build_llm_client(config)

    try:
        response = client.chat.completions.create(
            model=config["api"]["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=config["api"]["temperature"],
            max_tokens=config["api"]["max_tokens"],
        )
    except Exception as e:
        logger.error("API 调用失败: %s", e)
        raise

    raw_text = response.choices[0].message.content or ""
    logger.debug("API 原始返回: %s", raw_text[:200])

    try:
        result = parse_json_response(raw_text)
        # 验证必要字段
        if "summary" not in result or "keywords" not in result or "topic" not in result:
            logger.warning("API 返回字段不完整，缺少必要字段: %s", list(result.keys()))
            result.setdefault("summary", raw_text[:100])
            result.setdefault("keywords", [])
            result.setdefault("topic", "其他")
        # 记录提取到的额外信息
        if "operations" in result:
            logger.info("成功提取 %d 条操作记录", len(result["operations"]))
        if "product_opinions" in result:
            logger.info("成功提取 %d 条品种观点", len(result["product_opinions"]))
        if "weibo_opinions" in result:
            logger.info("成功提取 %d 条微博观点", len(result["weibo_opinions"]))
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("JSON 解析失败: %s，原始返回: %s", e, raw_text[:200])
        return {
            "summary": "[JSON解析失败]",
            "keywords": [],
            "topic": "其他",
        }


def summarize_with_retry(content: str, config: dict[str, Any], filename: str = "") -> dict[str, Any]:
    """带指数退避重试的摘要提取。

    对 API 调用错误（限流 429、超时、服务端错误等）进行重试；
    JSON 解析失败不重试（返回错误标记）。

    Args:
        content: 博客文章的纯文本内容
        config: 完整的配置字典
        filename: 文件名（用于判断类型），可为空

    Returns:
        dict: 与 summarize_article 返回结构一致的字典
    """
    max_retries = config["rate_limit"]["max_retries"]
    retry_base_delay = config["rate_limit"]["retry_base_delay"]

    for attempt in range(max_retries + 1):
        try:
            return summarize_article(content, config, filename=filename)
        except json.JSONDecodeError:
            # JSON 解析失败不重试，直接返回错误标记
            logger.error("JSON 解析失败（不重试），跳过该篇")
            return {
                "summary": "[JSON解析失败]",
                "keywords": [],
                "topic": "其他",
            }
        except ValueError:
            # 配置错误（如 API Key 缺失），不重试
            logger.error("配置错误（不重试），请检查 API Key 配置")
            raise
        except Exception as e:
            if attempt < max_retries:
                delay = retry_base_delay * (2 ** attempt)
                logger.warning(
                    "API 调用失败（第 %d/%d 次尝试），%s 秒后重试: %s",
                    attempt + 1,
                    max_retries + 1,
                    delay,
                    e,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "API 调用失败已达最大重试次数（%d 次）: %s",
                    max_retries + 1,
                    e,
                )
                return {
                    "summary": "[API调用失败]",
                    "keywords": [],
                    "topic": "其他",
                }

    # 理论上不会走到这里，但保持类型安全
    return {
        "summary": "[未知错误]",
        "keywords": [],
        "topic": "其他",
    }
"""
共享 LLM 工具模块

提供 LLM 客户端构建和 JSON 响应解析等基础能力，
供 distill（摘要/分析）等模块共同使用。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

# V4 思考模式默认参数
_V4_THINKING_MODE = {"thinking": {"type": "enabled"}}
_V4_NON_THINKING_MODE = {"thinking": {"type": "disabled"}}


def build_llm_client(config: dict[str, Any]) -> OpenAI:
    """根据配置构建 OpenAI 客户端。

    从 config["api"]["api_key_env"] 读取环境变量名，
    通过 os.environ.get() 获取 API Key。
    有 base_url 时使用 base_url 构建客户端，否则使用默认。

    Args:
        config: 完整的配置字典

    Returns:
        OpenAI: 配置好的 OpenAI 客户端实例

    Raises:
        ValueError: 环境变量中找不到 API Key 时抛出
    """
    api_key_env = config["api"]["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(
            f"环境变量 {api_key_env} 未设置，请先通过 export {api_key_env}=your-key 设置 API Key"
        )

    base_url = config["api"].get("base_url", "")
    if base_url:
        logger.info("使用自定义 API 端点: %s", base_url)
        return OpenAI(api_key=api_key, base_url=base_url)
    else:
        return OpenAI(api_key=api_key)


def _extract_json_braces(text: str) -> str | None:
    """用括号匹配法提取最外层 `{}` 包裹的 JSON 子串。

    思考模式下的 LLM 输出可能夹杂思考内容，此方法只提取从第一个 `{`
    到最后一个 `}` 之间的内容，并尝试用简单修复让 JSON 合法。

    Returns:
        提取到的 JSON 字符串，或 None
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        pass
    # 试着逐层缩小括号范围
    depth = 0
    for i, ch in enumerate(candidate):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and i < len(candidate) - 1:
                narrower = candidate[: i + 1]
                try:
                    json.loads(narrower)
                    return narrower
                except json.JSONDecodeError:
                    pass
    return None


def _repair_json(text: str) -> Any:
    """逐层修复并尝试解析 JSON 文本。

    处理步骤：
    1. 替换字符串中未转义的控制字符（换行、制表等）
    2. 修复未闭合的字符串（末尾缺少引号）
    3. 移除尾部的多余逗号
    """
    # 步骤1：将字符串值中的换行替换为 \\n（避免干扰 JSON 结构）
    #      简单策略：先将字符串外的换行保护起来
    import re
    in_string = False
    escaped = False
    result_chars: list[str] = []
    for ch in text:
        if escaped:
            result_chars.append(ch)
            escaped = False
            continue
        if ch == "\\":
            result_chars.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            result_chars.append(ch)
            continue
        if in_string and ch in "\n\r\t":
            result_chars.append("\\n")
        else:
            result_chars.append(ch)
    text = "".join(result_chars)
    # 步骤2：修复未闭合的字符串（以双引号开始但未结束的字段值）
    if text.count('"') % 2 != 0:
        text += '"'
    # 步骤3：移除对象/数组末尾多余的逗号
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


def parse_json_response(text: str) -> Any:
    """解析 API 返回的 JSON 文本。

    先用括号匹配法提取外层 `{}` 内容，再依次尝试：
    1. json.loads 直接解析
    2. markdown 代码块提取（```json / ```）
    3. 简单修复后重试
    4. 括号逐层缩小

    Args:
        text: API 返回的原始文本

    Returns:
        Any: 解析后的结果（dict 或 list），
             所有方法均失败时返回 None
    """
    text = text.strip()

    # 空内容检查：API 返回空字符串时直接返回 None
    # （如 DeepSeek API 偶发的 HTTP 200 + 空响应）
    if not text:
        logger.warning("API 返回内容为空，请检查 API/模型是否正常响应")
        return None

    # 方法1：直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 方法2：括号提取
    braces = _extract_json_braces(text)
    if braces is not None:
        try:
            return json.loads(braces)
        except json.JSONDecodeError:
            pass

    # 方法3：markdown 代码块
    if "```json" in text:
        start = text.find("```json") + len("```json")
        end = text.find("```", start)
        if end > start:
            text = text[start:end].strip()

    if "```" in text:
        start = text.find("```") + len("```")
        end = text.find("```", start)
        if end > start:
            text = text[start:end].strip()

    # 方法4：直接解析（此时可能已经是干净的代码块内容）
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 方法5：用括号再次提取并修复
    braces2 = _extract_json_braces(text)
    if braces2 is not None:
        try:
            return _repair_json(braces2)
        except json.JSONDecodeError:
            pass

    # 方法6：尝试修复整个文本
    try:
        return _repair_json(text)
    except json.JSONDecodeError:
        pass

    # 全部失败，返回 None 让上层降级处理
    logger.warning("JSON 解析全部方法失败（内容前 200 字: %s...）",
                   text[:200])
    return None


def call_v4_pro_with_thinking(
    client: OpenAI,
    model: str,
    messages: list[dict],
    reasoning_effort: str = "high",
    max_tokens: int = 4000,
    response_format: dict | None = None,
    timeout: int = 120,
) -> str:
    """调用 DeepSeek V4 Pro 并启用思考模式。

    支持思考模式 (thinking) + reasoning_effort 控制推理深度。
    思考模式下 temperature/top_p 不生效，由模型自动管理。
    超时通过 httpx 的 timeout 参数控制，避免卡死。

    Args:
        client: OpenAI 客户端实例
        model: 模型名，如 "deepseek-v4-pro"
        messages: 消息列表（system + user）
        reasoning_effort: 思考强度，"high" 或 "max"
        max_tokens: 最大输出 token 数
        response_format: 可选，如 {"type": "json_object"}
        timeout: 请求超时秒数（默认 120 秒）

    Returns:
        str: 模型返回的文本内容

    Raises:
        Exception: API 调用失败时抛出，由调用方处理
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
        "timeout": timeout,
        "extra_body": _V4_THINKING_MODE,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format

    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""
    if not content:
        logger.warning("API 返回空内容（HTTP 200，response_id=%s）",
                       response.id or "未知")
    else:
        logger.debug("API 返回内容长度: %d 字符 (response_id=%s)",
                     len(content), response.id or "未知")
    return content


def call_v4_pro_json(
    client: OpenAI,
    model: str,
    messages: list[dict],
    reasoning_effort: str = "high",
    max_tokens: int = 4000,
    timeout: int = 120,
) -> Any:
    """调用 DeepSeek V4 Pro（思考模式）并自动解析 JSON 响应。

    思考模式下 response_format 不生效，因此在 prompt 中要求输出 JSON，
    返回后通过 parse_json_response 进行弹性解析。

    Args:
        client: OpenAI 客户端实例
        model: 模型名
        messages: 消息列表
        reasoning_effort: 思考强度
        max_tokens: 最大输出 token
        timeout: 超时秒数

    Returns:
        Any: 解析后的 JSON 对象（dict 或 list），
             API 返回空内容或解析失败时返回 None
    """
    text = call_v4_pro_with_thinking(
        client=client,
        model=model,
        messages=messages,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    if not text.strip():
        logger.warning("API 返回空内容，call_v4_pro_json 返回 None")
        return None

    result = parse_json_response(text)
    if result is None:
        logger.warning("JSON 解析失败，call_v4_pro_json 返回 None")
    return result
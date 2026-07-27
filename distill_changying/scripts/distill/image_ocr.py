"""
图片 OCR 识别脚本

使用 Vision API（OpenAI 兼容格式，默认 GPT-4o-mini）识别持仓相关图片中的数字数据，
包括饼图中的品种占比、表格中的持仓比例和盈亏率等。
将识别结果以结构化 JSON 和可读表格形式保存到 Markdown 文件。

注意：DeepSeek API 不支持 vision/image_url 输入，因此使用独立的 vision provider。
配置位于 config.yaml 的 vision 节。

用法：
    export OPENAI_API_KEY="sk-..."
    cd /Users/yl/vscode/stockfilter_v3/distill_changying
    PYTHONPATH=. python3 scripts/distill/image_ocr.py
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

from scripts.distill.config import get_config, get_project_root
from scripts.shared.llm_utils import parse_json_response

logger = logging.getLogger(__name__)

# ---- 图片配置列表 ----
# 每张图片的描述和 OCR 目标的 Prompt
_IMAGE_TASKS: list[dict[str, str]] = [
    {
        "filename": "test.png",
        "title": "2019年2月 三层资产配置图（饼图）",
        "description": "一张三层资产配置饼图，展示各类资产的配置比例",
        "prompt": """请仔细识别这张饼图中的所有数据：
1. 提取饼图中每个扇区对应的**品种名称**和**占比百分比**。
2. 注意识别"三层"的层级结构（如：第一层、第二层、第三层），如果图中标注了层级信息请一并提取。
3. 如果有图例和图注，也请一并提取。

请严格按以下 JSON 格式返回，不要包含任何其他文字：
{
  "图表标题": "...",
  "层级结构": [
    {
      "层级名称": "第一层",
      "品种": [
        {"品种名称": "沪深300", "占比": "30%"},
        {"品种名称": "中证500", "占比": "20%"}
      ]
    }
  ],
  "图注": []
}""",
    },
    {
        "filename": "三周年-资产配置各品种.png",
        "title": "2018年8月 各品种持仓比例表",
        "description": "一张表格，展示各个品种的持仓份数和持仓比例",
        "prompt": """请仔细识别这张表格中的所有数据。这是一张各品种持仓比例表，请提取：
1. 表格的**表头**（列名）。
2. 每一行的数据，包括**品种名称**、**持仓份数**、**持仓比例**、**持仓金额**等所有可见的数值。
3. 注意区分百分比和绝对数值。

请严格按以下 JSON 格式返回，不要包含任何其他文字：
{
  "图表标题": "...",
  "日期": "...",
  "表头": ["品种", "持仓份数", "持仓比例", "...其他列"],
  "数据行": [
    {"品种": "A股", "持仓份数": 50, "持仓比例": "33.3%", "备注": "..."},
    {"品种": "港股", "持仓份数": 20, "持仓比例": "13.3%", "备注": "..."}
  ],
  "合计行": {"持仓份数": 150, "持仓比例": "100%"}
}""",
    },
    {
        "filename": "三周年-非A股浮动收益.png",
        "title": "2018年8月 非A股品种浮动盈亏表",
        "description": "一张表格，展示非A股各品种的浮动盈亏数据",
        "prompt": """请仔细识别这张表格中的所有数据。这是一张非A股品种浮动盈亏表，请提取：
1. 表格的**表头**（列名）。
2. 每一行的数据，包括**品种名称**、**买入成本**、**当前市值**、**浮动盈亏金额**、**浮动盈亏率**等所有可见数值。
3. 盈亏率保留正负号（如 +12.5%、-3.2%）。

请严格按以下 JSON 格式返回，不要包含任何其他文字：
{
  "图表标题": "...",
  "日期": "...",
  "表头": ["品种", "买入成本", "当前市值", "浮动盈亏", "盈亏率", "...其他列"],
  "数据行": [
    {"品种": "恒生ETF", "买入成本": 10000, "当前市值": 11250, "浮动盈亏": 1250, "盈亏率": "+12.5%"}
  ],
  "合计行": {"买入成本": 50000, "当前市值": 52000, "浮动盈亏": 2000, "盈亏率": "+4.0%"}
}""",
    },
    {
        "filename": "三周年-S定投持仓比例.png",
        "title": "2018年8月 S定投持仓比例及盈亏表",
        "description": "S定投计划的图表，可能包含持仓明细或收益指标",
        "prompt": """请仔细识别这张图片中的所有数据。这是一张关于S定投的图表。
如果图中包含表格数据（品种、份数、比例、盈亏等），请完整提取所有行和列。
如果图中包含的是基本指标（收益率、净值、回撤等）和走势图，也请完整提取。

请严格按以下 JSON 格式返回：
{
  "图表标题": "...",
  "日期": "...",
  "数据类型": "持仓表" 或 "指标与走势",
  "数据内容": { ... 提取到的所有结构化数据 ... }
}""",
    },
]


def _build_images_dir(config: dict[str, Any]) -> str:
    """根据配置获取图片目录的绝对路径。

    Args:
        config: 完整的配置字典

    Returns:
        str: 图片目录的绝对路径
    """
    return os.path.join(get_project_root(), "docs", "distilled", "images")


def _encode_image(image_path: str) -> str:
    """将图片文件编码为 base64 data URL。

    Args:
        image_path: 图片文件的绝对路径

    Returns:
        str: base64 编码的 data URL（格式：data:image/png;base64,...）

    Raises:
        FileNotFoundError: 图片文件不存在时抛出
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    # 根据扩展名确定 MIME 类型
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime_type = mime_map.get(ext, "image/png")

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{image_data}"


def _build_vision_client(config: dict[str, Any]) -> Any:
    """根据 vision 配置构建 Vision API 客户端。

    DeepSeek API 不支持 vision/image_url 输入，因此 vision 任务使用独立的
    vision provider（默认 OpenAI GPT-4o-mini）。

    Args:
        config: 完整的配置字典

    Returns:
        OpenAI: 配置好的 OpenAI 客户端实例

    Raises:
        ValueError: 环境变量中找不到 Vision API Key 时抛出
    """
    vision_cfg = config["vision"]
    api_key_env = vision_cfg["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(
            f"Vision API 环境变量 {api_key_env} 未设置，"
            f"请先通过 export {api_key_env}=your-key 设置 API Key"
        )
    base_url = vision_cfg.get("base_url", "")
    from openai import OpenAI
    logger.info("Vision API 端点: %s, 模型: %s", base_url, vision_cfg["model"])
    return OpenAI(api_key=api_key, base_url=base_url)


def _ocr_image(
    client: Any,
    config: dict[str, Any],
    image_data_url: str,
    prompt: str,
    title: str,
) -> dict[str, Any]:
    """对单张图片调用 Vision API 进行 OCR 识别。

    使用 config["vision"] 中的模型、温度、最大 token 数配置。

    Args:
        client: OpenAI 客户端实例（vision provider）
        config: 完整的配置字典
        image_data_url: base64 编码的图片 data URL
        prompt: OCR 识别 Prompt
        title: 图片标题（用于日志）

    Returns:
        dict: 识别结果字典
    """
    model = config["vision"]["model"]
    max_tokens = config["vision"]["max_tokens"]
    temperature = config["vision"]["temperature"]

    logger.info("正在识别: %s", title)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    ]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    raw_text = response.choices[0].message.content or ""
    logger.debug("API 原始返回（前 500 字符）: %s", raw_text[:500])

    # 先尝试直接解析
    try:
        result = json.loads(raw_text.strip())
        return result
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取 JSON
    cleaned = raw_text.strip()
    for marker in ["```json", "```"]:
        if marker in cleaned:
            start = cleaned.find(marker) + len(marker)
            end = cleaned.find("```", start)
            if end > start:
                cleaned = cleaned[start:end].strip()
                break

    try:
        result = json.loads(cleaned)
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("JSON 解析失败: %s，返回原始文本（前500字符）: %s", e, raw_text[:500] if raw_text else "(空)")
        return {"原始返回文本": raw_text or "(空)", "解析状态": "JSON解析失败"}


def _ocr_with_retry(
    client: Any,
    config: dict[str, Any],
    image_data_url: str,
    prompt: str,
    title: str,
) -> dict[str, Any]:
    """带重试的 OCR 识别。

    对 API 调用错误进行指数退避重试，JSON 解析失败不重试。

    Args:
        client: OpenAI 客户端实例
        config: 完整的配置字典
        image_data_url: base64 编码的图片 data URL
        prompt: OCR 识别 Prompt
        title: 图片标题

    Returns:
        dict: 识别结果字典
    """
    max_retries = config["rate_limit"]["max_retries"]
    retry_base_delay = config["rate_limit"]["retry_base_delay"]

    for attempt in range(max_retries + 1):
        try:
            return _ocr_image(client, config, image_data_url, prompt, title)
        except json.JSONDecodeError:
            logger.error("JSON 解析失败（不重试）")
            return {"原始返回文本": "JSON解析失败", "解析状态": "失败"}
        except ValueError:
            logger.error("配置错误（不重试）")
            raise
        except Exception as e:
            if attempt < max_retries:
                delay = retry_base_delay * (2**attempt)
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
                return {"错误": str(e), "原始返回文本": "API调用失败"}

    return {"错误": "未知错误"}


def _json_to_markdown_table(data: dict[str, Any]) -> str:
    """将识别出的 JSON 数据转换为 Markdown 表格。

    Args:
        data: OCR 识别结果的字典

    Returns:
        str: Markdown 格式的表格字符串
    """
    lines: list[str] = []

    # 图表标题
    title = data.get("图表标题", "")
    if title:
        lines.append(f"**图表标题**: {title}")
        lines.append("")

    # 日期
    date_str = data.get("日期", "")
    if date_str:
        lines.append(f"**日期**: {date_str}")
        lines.append("")

    # 层级结构（饼图）
    hierarchy = data.get("层级结构")
    if hierarchy:
        lines.append("### 层级结构")
        lines.append("")
        for layer in hierarchy:
            layer_name = layer.get("层级名称", "")
            lines.append(f"#### {layer_name}")
            lines.append("")
            items = layer.get("品种", [])
            if items:
                # 获取所有字段
                all_keys = set()
                for item in items:
                    all_keys.update(item.keys())
                headers = sorted(all_keys)
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                for item in items:
                    row = [str(item.get(k, "")) for k in headers]
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")

    # 表格数据
    table_headers = data.get("表头")
    if table_headers:
        lines.append("### 数据表格")
        lines.append("")
        rows = data.get("数据行", [])
        if rows:
            lines.append("| " + " | ".join(table_headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(table_headers)) + " |")
            for row in rows:
                row_values = [str(row.get(h, "")) for h in table_headers]
                lines.append("| " + " | ".join(row_values) + " |")
            lines.append("")

        # 合计行
        total_row = data.get("合计行")
        if total_row and table_headers:
            total_values = [str(total_row.get(h, "")) for h in table_headers]
            lines.append("| " + " | ".join(total_values) + " |")
            lines.append("")

    # 图注
    legend = data.get("图注")
    if legend:
        lines.append("### 图注")
        lines.append("")
        for note in legend:
            lines.append(f"- {note}")
        lines.append("")

    # 原始返回（如果有解析失败的情况）
    raw = data.get("原始返回文本", "")
    if raw:
        lines.append("### 原始返回文本")
        lines.append("")
        lines.append("```")
        lines.append(raw)
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def _generate_markdown_report(
    results: list[dict[str, Any]],
    model_name: str = "gpt-4o-mini",
) -> str:
    """生成最终的 Markdown 报告。

    Args:
        results: 每张图片的识别结果列表，每条包含 title、json_data 等字段

    Returns:
        str: 完整的 Markdown 报告内容
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = [
        "# 图片 OCR 识别结果",
        "",
        f"**生成时间**: {now}",
        f"**识别模型**: {model_name}",
        "",
        "---",
        "",
    ]

    for result in results:
        title = result.get("title", "未知图片")
        json_data = result.get("json_data", {})
        filename = result.get("filename", "")

        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"**源文件**: `{filename}`")
        lines.append("")

        # JSON 原始数据
        lines.append("### 原始 JSON 数据")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(json_data, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

        # 可读表格
        lines.append("### 可读表格")
        lines.append("")
        table = _json_to_markdown_table(json_data)
        if table.strip():
            lines.append(table)
        else:
            lines.append("（无法生成结构化表格，请参考上方原始 JSON 数据）")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """主函数：依次识别四张图片并输出结果到 Markdown 文件。"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("===== 图片 OCR 识别脚本启动 =====")

    # 加载配置
    config = get_config()
    images_dir = _build_images_dir(config)

    # 构建 Vision API 客户端（使用独立的 vision provider）
    try:
        client = _build_vision_client(config)
    except ValueError as e:
        logger.error("Vision API 客户端构建失败: %s", e)
        sys.exit(1)

    logger.info("图片目录: %s", images_dir)
    logger.info("Vision 模型: %s", config["vision"]["model"])

    results: list[dict[str, Any]] = []

    for task in _IMAGE_TASKS:
        filename = task["filename"]
        title = task["title"]
        prompt = task["prompt"]

        image_path = os.path.join(images_dir, filename)

        # 检查文件存在
        if not os.path.isfile(image_path):
            logger.warning("图片文件不存在，跳过: %s", image_path)
            results.append({
                "filename": filename,
                "title": title,
                "json_data": {"错误": f"文件不存在: {image_path}"},
            })
            continue

        # 编码图片
        try:
            image_data_url = _encode_image(image_path)
            file_size = os.path.getsize(image_path)
            logger.info("已加载图片: %s (%.0f KB)", filename, file_size / 1024)
        except Exception as e:
            logger.error("图片编码失败: %s - %s", filename, e)
            results.append({
                "filename": filename,
                "title": title,
                "json_data": {"错误": f"图片编码失败: {e}"},
            })
            continue

        # OCR 识别
        json_data = _ocr_with_retry(client, config, image_data_url, prompt, title)
        results.append({
            "filename": filename,
            "title": title,
            "json_data": json_data,
        })

        # 图片间延迟
        delay = config["rate_limit"]["delay_between_articles"]
        logger.info("等待 %d 秒后处理下一张图片...", delay)
        time.sleep(delay)

    # 生成报告
    report = _generate_markdown_report(results, model_name=config["vision"]["model"])

    # 保存到文件
    output_path = os.path.join(get_project_root(), "docs", "distilled", "图片OCR识别结果.md")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("识别结果已保存到: %s", output_path)
    logger.info("===== 图片 OCR 识别脚本完成 =====")


if __name__ == "__main__":
    main()
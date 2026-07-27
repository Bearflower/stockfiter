"""
跨文章深度分析引擎

将第一层蒸馏产生的全部博文摘要（blog-index.csv）汇总后，
调用 LLM 进行跨文章深度提炼，生成结构化的思想矿脉报告。

核心流程：
1. 加载 blog-index.csv 中的摘要数据
2. 构建跨文章分析 Prompt（遵循需求文档模板）
3. 按需分批调用 LLM（单批超过 max_articles_per_batch 时分批处理）
4. 合并各批结果，输出完整 Markdown 分析报告
"""

import csv
import logging
import os
import re
import time
from typing import Any

from scripts.shared.llm_utils import build_llm_client

logger = logging.getLogger(__name__)


def load_summaries(output_dir: str) -> list[dict[str, str]]:
    """从 blog-index.csv 加载全部博文摘要。

    读取第一层蒸馏产物，解析 CSV 中的 file、summary、keywords、topic 列，
    并从文件名中提取年份信息。

    Args:
        output_dir: 输出目录绝对路径，blog-index.csv 应在此目录下

    Returns:
        list[dict]: 摘要列表，每条包含 file、summary、keywords、topic、year 字段

    Raises:
        FileNotFoundError: blog-index.csv 不存在时抛出
    """
    csv_path = os.path.join(output_dir, "blog-index.csv")
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"第一层蒸馏产物不存在: {csv_path}，请先运行 summarize 子命令")

    summaries: list[dict[str, str]] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            file_name = row.get("file", "")
            # 从文件名中提取年份（如 "2020-01-xxx.md" -> "2020"）
            year = _extract_year(file_name)

            summaries.append({
                "file": file_name,
                "summary": row.get("summary", ""),
                "keywords": row.get("keywords", ""),
                "topic": row.get("topic", ""),
                "year": year,
            })

    logger.info("从 %s 加载了 %d 条博文摘要", csv_path, len(summaries))
    return summaries


def _extract_year(file_name: str) -> str:
    """从文件名中提取四位年份。

    支持多种命名格式：
    - "2020-01-15-xxx.md"
    - "xxx-2020-xxx.md"
    - "2020_xxx.md"

    Args:
        file_name: 文件名

    Returns:
        str: 四位年份字符串，提取失败返回空字符串
    """
    match = re.search(r"(20\d{2})", file_name)
    return match.group(1) if match else ""


def build_analysis_prompt(summaries: list[dict[str, str]]) -> str:
    """构建跨文章深度分析 Prompt。

    将所有摘要格式化为 CSV 表格嵌入 Prompt，按照需求文档中的
    "终极跨文章提炼 Prompt 模板" 组织分析任务。

    Args:
        summaries: 摘要列表

    Returns:
        str: 完整的分析 Prompt 文本
    """
    # 构建 CSV 表格头
    table_lines = ["| 文件 | 核心观点 | 关键词 | 主题 | 年份 |"]
    table_lines.append("|------|---------|--------|------|------|")

    for s in summaries:
        # 转义 CSV 中的管道符，避免破坏表格格式
        file_escaped = s["file"].replace("|", "\\|")
        summary_escaped = s["summary"].replace("|", "\\|")
        keywords_escaped = s["keywords"].replace("|", "\\|")
        topic_escaped = s["topic"].replace("|", "\\|")
        year = s["year"]
        table_lines.append(
            f"| {file_escaped} | {summary_escaped} | {keywords_escaped} | {topic_escaped} | {year} |"
        )

    table_content = "\n".join(table_lines)

    prompt = f"""你是一位顶级知识管理专家和认知科学家。我需要你帮我深度萃取一位投资博主"ETF拯救世界"(E大)的全部思想。

注意：本分析仅聚焦于E大的通用投资原则和哲学理念。
操作记录（发车帖交易）和市场判断（微博短评）不在本分析范围内，
它们已经通过独立的通道提取和存储到专门的数据库中。
请聚焦于：哪些投资原则是跨时间不变的？哪些思维方式贯穿始终？

以下是他的全部文章摘要，格式为：| 文件 | 核心观点 | 关键词 | 主题 | 年份 |

【所有文章摘要】
{table_content}

请完成以下任务，并以结构化 Markdown 输出，便于我直接保存为永久笔记：

1. **核心命题聚类**：将他讨论的所有话题归类为 5-8 个核心命题，并为每个命题写一段 50 字的描述。

2. **十大核心观点**：提炼出他最核心、最反复强调的 10 个观点。每个观点需包含：
   - 观点陈述
   - 一句最具代表性的原文片段（从摘要中推断典型表述）
   - 出现频次估计（高/中/低）

3. **思维模型工具箱**：识别他文章里隐含或明确使用的思维模型（如周期思维、资产配置、安全边际、逆向投资等），列出 5-10 个，并简要说明他如何运用。

4. **价值观罗盘**：从字里行间提取他珍视什么、反对什么，列成简洁的清单。

5. **演化轨迹**：按时间线描述观点变化，用"早期…中期…近期…"的句式概括。

6. **影响源推测**：根据关键词和观点，推测他可能受到哪些书籍、人物或思想流派的影响。

7. **矛盾与张力**：指出观点间的潜在矛盾或未解决的张力，这往往是思想最有趣的地方。

请确保输出格式精美，使用标题、列表和引用。"""
    return prompt


def run_analysis(config: dict[str, Any]) -> str:
    """执行跨文章深度分析。

    加载摘要、构建 Prompt、调用 LLM，处理分批逻辑，
    最终返回完整的 Markdown 分析报告文本。

    Args:
        config: 完整配置字典（来自 get_config()）

    Returns:
        str: LLM 生成的完整 Markdown 分析报告

    Raises:
        FileNotFoundError: blog-index.csv 不存在
        RuntimeError: API 调用全部失败
    """
    output_dir = config["paths"]["output_dir"]
    max_batch = config["deep_analysis"]["max_articles_per_batch"]

    # 1. 加载摘要
    summaries = load_summaries(output_dir)
    total_count = len(summaries)
    logger.info("共加载 %d 篇博文摘要，准备深度分析", total_count)

    # 2. 判断是否需要分批处理
    if total_count <= max_batch:
        logger.info("摘要数量未超过分批阈值(%d)，单次调用 LLM", max_batch)
        prompt = build_analysis_prompt(summaries)
        return call_llm(config, prompt, task_name="跨文章深度分析")
    else:
        logger.info("摘要数量(%d)超过分批阈值(%d)，启动分批处理", total_count, max_batch)
        return _run_batched_analysis(config, summaries, max_batch)


def _run_batched_analysis(
    config: dict[str, Any],
    summaries: list[dict[str, str]],
    max_batch: int,
) -> str:
    """分批执行深度分析，最后合并各批结果。

    每批独立调用 LLM 生成部分分析，然后将各批结果合并为一个完整的报告。

    Args:
        config: 配置字典
        summaries: 全部摘要列表
        max_batch: 每批最多处理篇数

    Returns:
        str: 合并后的完整 Markdown 分析报告
    """
    batch_count = (len(summaries) + max_batch - 1) // max_batch
    batch_results: list[str] = []

    for i in range(batch_count):
        start = i * max_batch
        end = min(start + max_batch, len(summaries))
        batch_summaries = summaries[start:end]

        logger.info("处理第 %d/%d 批（%d-%d 篇，共 %d 篇）",
                     i + 1, batch_count, start + 1, end, len(batch_summaries))

        prompt = build_analysis_prompt(batch_summaries)
        batch_result = call_llm(
            config, prompt,
            task_name=f"分批深度分析 第{i + 1}/{batch_count}批",
        )
        batch_results.append(batch_result)

        # 批次间短暂暂停，避免触发速率限制
        if i < batch_count - 1:
            pause = config["rate_limit"].get("batch_pause", 10)
            logger.info("批次间暂停 %d 秒...", pause)
            time.sleep(pause)

    # 合并各批结果
    if batch_count == 1:
        return batch_results[0]

    logger.info("所有批次分析完成，开始合并 %d 批结果", batch_count)
    merge_prompt = _build_merge_prompt(batch_results)
    merged = call_llm(config, merge_prompt, task_name="合并分批分析结果")
    return merged


def _build_merge_prompt(batch_results: list[str]) -> str:
    """构建用于合并分批量结果的 Prompt。

    Args:
        batch_results: 各批 LLM 返回的分析文本列表

    Returns:
        str: 合并 Prompt
    """
    results_text = "\n\n---\n\n".join([
        f"## 第 {i + 1} 批分析结果\n\n{text}"
        for i, text in enumerate(batch_results)
    ])

    prompt = f"""你是一位顶级知识管理专家。以下是针对一位投资博主的多批文章摘要分别生成的分析报告。

请将以下各批分析结果整合为一份统一的、无冗余的完整报告，保持原文的结构化 Markdown 格式：

1. **核心命题聚类**（5-8个，去重合并）
2. **十大核心观点**（精选最重要的10个，跨批次整合）
3. **思维模型工具箱**（5-10个，去重）
4. **价值观罗盘**（整合珍视/反对清单）
5. **演化轨迹**（按时间线整合）
6. **影响源推测**（合并推测）
7. **矛盾与张力**（合并所有张力点）

{results_text}

请输出完整的合并后 Markdown 报告。"""
    return prompt


def call_llm(
    config: dict[str, Any],
    prompt: str,
    task_name: str = "LLM调用",
) -> str:
    """调用 OpenAI API，带指数退避重试。

    Args:
        config: 配置字典
        prompt: 用户 Prompt
        task_name: 任务名称，用于日志标识

    Returns:
        str: LLM 返回的文本内容

    Raises:
        RuntimeError: 全部重试失败后抛出
    """
    api_config = config["api"]
    rate_config = config["rate_limit"]

    client = build_llm_client(config)

    model = api_config["deep_model"]
    temperature = api_config["temperature"]
    max_tokens = api_config["max_tokens"]
    max_retries = rate_config.get("max_retries", 3)
    retry_base_delay = rate_config.get("retry_base_delay", 5)

    logger.info("调用 LLM [%s]: model=%s, prompt 长度=%d 字符",
                task_name, model, len(prompt))

    # 深度分析可能需要更多 tokens，根据 prompt 长度动态调整
    actual_max_tokens = max(max_tokens, 4000)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一位顶级知识管理专家和认知科学家，擅长深度分析和结构化输出。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=actual_max_tokens,
            )
            content = response.choices[0].message.content or ""
            logger.info("LLM [%s] 调用成功，返回 %d 字符", task_name, len(content))
            return content

        except Exception as e:
            last_error = e
            logger.warning("LLM [%s] 第 %d/%d 次调用失败: %s",
                          task_name, attempt, max_retries, e)

            if attempt < max_retries:
                delay = retry_base_delay * (2 ** (attempt - 1))
                logger.info("等待 %.1f 秒后重试...", delay)
                time.sleep(delay)

    raise RuntimeError(
        f"LLM [{task_name}] 全部 {max_retries} 次重试均失败，最后错误: {last_error}"
    )
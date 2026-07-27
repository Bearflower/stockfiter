"""
持仓还原模块

从所有发车博客中提取买入/卖出操作记录，累加计算理论持仓，
并导出为 Markdown 表格与 CSV 文件。

支持多种发车帖格式：
  1. 标准格式（2024年+）：150计划：\n卖出一份\n建信500（场外000478）
  2. 跨行格式（2020年早期）：150计划\n卖出一份\n：\n卖出中国海外互联一份（场外164906）
  3. 内联格式（2023年）：卖出两份中证传媒（场外004752）
  4. 文字发车格式（2019年）：临时发车一次，卖出中证500C一份（场外C类002903）
  5. 跨行操作+基金分离：卖出一份\n建信500（场外000478）

核心解析策略：多行上下文扫描，不做过度合并，逐行检测买入/卖出+基金代码模式。

所有参数从 config.yaml 读取，禁止硬编码。
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
from typing import Any

from scripts.distill.config import get_config, get_project_root
from scripts.distill.file_handler import list_blog_files, read_blog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 中文数字 → 阿拉伯数字映射
# ---------------------------------------------------------------------------

_CN_NUM_MAP: dict[str, int] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_shares(raw: str) -> float:
    """将中文或数字份额字符串转为浮点数。

    Args:
        raw: 份额原始字符串，如 "一"、"两"、"3"、"10"

    Returns:
        float: 份额数值，默认 1.0
    """
    raw = raw.strip()
    if raw.isdigit():
        return float(raw)
    if raw in _CN_NUM_MAP:
        return float(_CN_NUM_MAP[raw])
    if raw == "两":
        return 2.0
    return 1.0


# ---------------------------------------------------------------------------
# 发车帖筛选
# ---------------------------------------------------------------------------

# 用于内容回退检测的正则：买入/卖出 + 基金代码模式
_CONTENT_FUND_OP_PATTERN = re.compile(
    r"(买入|卖出).*?[（(]场[外内].*?\d{6}"
)


def _is_fache_post(
    filepath: str, keywords: list[str], content: str | None = None,
) -> bool:
    """判断文件是否属于发车帖。

    优先级：
      1. 文件名匹配关键词 → 直接返回 True
      2. 文件名未匹配但有内容 → 检查内容是否包含买入/卖出+基金代码模式

    Args:
        filepath: 文件的绝对路径
        keywords: 发车帖关键词列表
        content: 可选的文件内容，用于内容回退检测

    Returns:
        bool: 是否为发车帖
    """
    basename = os.path.basename(filepath)
    for kw in keywords:
        if kw in basename:
            return True

    # 内容回退：文件名不匹配但内容包含买入/卖出+基金代码
    if content is not None and _CONTENT_FUND_OP_PATTERN.search(content):
        logger.debug("内容回退匹配发车帖: %s", basename)
        return True

    return False


# ---------------------------------------------------------------------------
# 内容预处理
# ---------------------------------------------------------------------------

def _normalize_content(content: str) -> str:
    """轻量预处理：仅合并跨行的基金代码括号和断行。

    处理以下跨行情况：
      - "基金名\\n（场外XXXXXX）" → "基金名（场外XXXXXX）"
      - "（场内XXXX或\\n场外XXXXXX）" → "（场内XXXX或场外XXXXXX）"
      - "（场外\\n162412\\n）" → "（场外162412）"

    不做过度合并，后续由多行上下文解析器逐行处理。

    Args:
        content: 原始博客内容

    Returns:
        str: 预处理后的内容
    """
    # 合并：基金名 换行 （场外/场内 ...
    # 例如 "建信500\n（场外000478）" → "建信500（场外000478）"
    content = re.sub(
        r"([^\s\n（(])\s*\n\s*[（(]\s*(场[外内])",
        r"\1（\2",
        content,
    )
    # 合并：场内/场外代码被换行打断
    # 例如 "（场内513050或\n场外164906）" → "（场内513050或场外164906）"
    content = re.sub(
        r"([或;；])\s*\n\s*(场[外内])",
        r"\1\2",
        content,
    )
    # 合并：场外后换行跟代码再换行跟右括号
    # 例如 "（场外\n162412\n）" → "（场外162412）"
    # 例如 "（场外\n162412\n——该品种..." → "（场外162412——该品种..."
    content = re.sub(
        r"([（(]\s*场[外内](?:C类)?)\s*\n\s*(\d{6})\s*\n\s*([）)])",
        r"\1\2\3",
        content,
    )
    return content


# ---------------------------------------------------------------------------
# 跨行操作合并
# ---------------------------------------------------------------------------

# 操作前缀模式：买入/卖出 + 份额
_OP_PREFIX_PATTERN = re.compile(
    r"^(买入|卖出)\s*(一|两|三|[一二三四五六七八九十]|\d+)份$"
)


def _merge_operation_continuations(lines: list[str]) -> list[str]:
    """合并跨行的操作行与基金代码行。

    处理以下模式：
      - ["卖出一份", "建信500（场外000478）"] → ["卖出一份建信500（场外000478）"]
      - ["买入全指", "医药", "一份（场内159938 或 场外001180）"]
        → ["买入全指医药一份（场内159938 或 场外001180）"]

    如果基金代码行本身已含买入/卖出词，则不合并（该行自身就是完整操作）。

    Args:
        lines: 预处理后的行列表（每行已 strip）

    Returns:
        list[str]: 合并后的行列表
    """
    result: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line:
            i += 1
            continue

        # --- 模式 1：纯操作前缀（买入/卖出+份额，无基金代码） ---
        if _OP_PREFIX_PATTERN.match(line):
            j = i + 1
            while j < n and not lines[j]:
                j += 1
            if j < n:
                next_line = lines[j]
                has_fund_code = bool(re.search(r"[（(]场[外内].*?\d{6}", next_line))
                has_action_word = "买入" in next_line or "卖出" in next_line
                if has_fund_code and not has_action_word:
                    line = line + next_line
                    i = j

            result.append(line)
            i += 1
            continue

        # --- 模式 2：操作词+部分基金名，基金名延续到下一行 ---
        # 例如 "买入全指" + "医药" + "一份（场外...）"
        # 或 "买入全指" + "医药"
        has_action = "买入" in line or "卖出" in line
        has_fund_code = bool(re.search(r"[（(]场[外内].*?\d{6}", line))
        has_plan_header = bool(re.search(r"(150|S)计划", line))

        if has_action and not has_fund_code and not has_plan_header:
            # 向前看：收集连续的碎片行，直到遇到含基金代码的行或另一个操作词
            j = i + 1
            merged = line
            while j < n and lines[j]:
                next_line = lines[j]
                next_has_action = "买入" in next_line or "卖出" in next_line
                next_has_fund_code = bool(re.search(r"[（(]场[外内].*?\d{6}", next_line))
                next_has_plan = bool(re.search(r"(150|S)计划", next_line))

                # 遇到计划头或另一操作词 → 停止
                if next_has_plan or (next_has_action and not next_has_fund_code):
                    break

                merged = merged + next_line
                i = j

                # 如果当前合并已含基金代码 → 完成
                if next_has_fund_code:
                    break

                j += 1

            line = merged

        result.append(line)
        i += 1

    return result


# ---------------------------------------------------------------------------
# 基金名称清洗
# ---------------------------------------------------------------------------

def _clean_fund_name(raw: str) -> str:
    """清洗基金名称，去除多余符号和数量词。

    Args:
        raw: 原始基金名称文本

    Returns:
        str: 清洗后的基金名称
    """
    name = raw.strip()
    # 去除开头的标点符号
    name = re.sub(r"^[：:，,、。；;）)\s]+", "", name)
    # 去除结尾的标点符号
    name = re.sub(r"[：:，,、。；;（(\s]+$", "", name)
    # 去除数量词残留（如 "一份"、"两份" 等）
    name = re.sub(r"(一|两|三|[一二三四五六七八九十]|\d+)份", "", name)
    # 去除 "C类" 等后缀（保留在基金名称中）
    name = name.strip()
    return name


# ---------------------------------------------------------------------------
# 单行操作解析
# ---------------------------------------------------------------------------

# 场外基金代码匹配正则（优先使用）
# 支持格式：
#   - （场外011309）
#   - （场内513050或场外164906）—— 提取场外代码
#   - （场外003376——该品种自目前价格预计最大跌幅40%）
#   - （场外C类002903）
_FUND_OUTER_PATTERN = re.compile(
    r"(买入|卖出)"                                  # 操作方向
    r"(.+?)"                                        # 中间文本（含份额和基金名）
    r"[（(]\s*"                                     # 左括号
    r".*?"                                          # 前缀之前的内容（可能含场内代码）
    r"场外(?:C类)?"                                 # 场外基金代码前缀
    r".*?"                                           # 前缀与代码之间的内容
    r"(\d{6})"                                       # 6 位基金代码
    r"[^）)]*"                                       # 代码与右括号之间的内容
    r"[）)]"                                         # 右括号
)

# 场内基金代码匹配正则（场外不可用时的回退方案）
_FUND_INNER_PATTERN = re.compile(
    r"(买入|卖出)"                                  # 操作方向
    r"(.+?)"                                        # 中间文本（含份额和基金名）
    r"[（(]\s*"                                     # 左括号
    r".*?"                                          # 前缀之前的内容
    r"场内(?:C类)?"                                 # 场内基金代码前缀
    r".*?"                                           # 前缀与代码之间的内容
    r"(\d{6})"                                       # 6 位基金代码
    r"[^）)]*"                                       # 代码与右括号之间的内容
    r"[）)]"                                         # 右括号
)

# 份额提取正则
_SHARES_PATTERN = re.compile(r"(一|两|三|[一二三四五六七八九十]|\d+)份")


def _parse_operation_line(line: str, fund_code_priority: str = "场外") -> list[dict[str, Any]]:
    """从单行文本中解析操作记录。

    优先使用场外基金代码，场外不可用时回退到场内代码。

    支持两种份额位置：
      - 份额在操作词后：买入一份富国消费C（场外011309）
      - 份额在基金名后：卖出中国海外互联一份（场内513050或场外164906）

    Args:
        line: 单行操作文本
        fund_code_priority: 基金代码优先级，"场外" 或 "场内"

    Returns:
        list[dict]: 操作记录列表，每条包含 action/shares/fund_name/fund_code
    """
    operations: list[dict[str, Any]] = []

    # 根据优先级选择正则顺序
    if fund_code_priority == "场外":
        patterns = [(_FUND_OUTER_PATTERN, "场外"), (_FUND_INNER_PATTERN, "场内")]
    else:
        patterns = [(_FUND_INNER_PATTERN, "场内"), (_FUND_OUTER_PATTERN, "场外")]

    # 使用第一个可用的正则匹配
    for pattern, _source in patterns:
        matches = list(pattern.finditer(line))
        if matches:
            for m in matches:
                try:
                    action = "buy" if m.group(1) == "买入" else "sell"
                    middle = m.group(2).strip()
                    fund_code = m.group(3)

                    # 从 middle 中提取份额和基金名称
                    shares_match = _SHARES_PATTERN.search(middle)
                    if shares_match:
                        shares = _parse_shares(shares_match.group(1))
                        fund_name = _SHARES_PATTERN.sub("", middle)
                    else:
                        shares = 1.0
                        fund_name = middle

                    fund_name = _clean_fund_name(fund_name)
                    if not fund_name:
                        logger.debug("无法提取基金名称，跳过: %s", line[:80])
                        continue

                    operations.append({
                        "action": action,
                        "shares": shares,
                        "fund_name": fund_name,
                        "fund_code": fund_code,
                    })
                except Exception:
                    logger.warning("解析操作行失败: %s", line[:100], exc_info=True)
            break  # 匹配成功，不再尝试下一个正则

    return operations


# ---------------------------------------------------------------------------
# 提取操作记录
# ---------------------------------------------------------------------------

# 计划头匹配：150计划 / S计划（可能出现在行首或行中）
_PLAN_HEADER_PATTERN = re.compile(r"(150|S)计划")

# ---------------------------------------------------------------------------
# 汇总式操作记录正则（路径C：微博精选汇总格式）
# ---------------------------------------------------------------------------

# 模式1: 150/S 卖出X份，买入Y份（或反过来）—— 同一句内包含买卖双方
_SUMMARY_SELL_BUY_PATTERN = re.compile(
    r"(150|S)\s*(?:计划)?\s*卖出\s*(\d+)\s*份.*?买入\s*(\d+)\s*份"
)
_SUMMARY_BUY_SELL_PATTERN = re.compile(
    r"(150|S)\s*(?:计划)?\s*买入\s*(\d+)\s*份.*?卖出\s*(\d+)\s*份"
)

# 模式2: 独立的 150/S 卖出X份 或 买入Y份（单边操作）
_SUMMARY_SINGLE_OP_PATTERN = re.compile(
    r"(150|S)\s*(?:卖出|买入)\s*(\d+)\s*份"
)


def _infer_plan_from_content(content: str) -> str | None:
    """从文件内容推断所属计划（回退策略）。

    搜索内容中是否明确提到某个计划的操作。

    Args:
        content: 文件内容

    Returns:
        str | None: 推断的计划名，无法推断时返回 None
    """
    # 搜索 "S计划买入"、"S计划卖出"、"S买入"、"S卖出" 等模式
    if re.search(r"S计划\s*(买入|卖出)|S(买入|卖出)", content):
        return "S"
    # 搜索 "150计划买入"、"150计划卖出"、"150买入"、"150卖出" 等模式
    if re.search(r"150计划\s*(买入|卖出)|150(买入|卖出)", content):
        return "150"
    # 文字发车文件中 "S计划" 出现
    if "S计划" in content:
        return "S"
    # 文字发车文件中 "150计划"或"计划150" 出现
    if "150计划" in content or "计划150" in content:
        return "150"
    return None


def _infer_plan_from_filename(filepath: str) -> str | None:
    """从文件名推断所属计划（用于文字发车等无计划头的文件）。

    Args:
        filepath: 文件路径

    Returns:
        str | None: 推断的计划名，无法推断时返回 None
    """
    basename = os.path.basename(filepath)
    # 文件名含 "S" 计划 → S
    if re.search(r"S计划|S买|S卖", basename):
        return "S"
    # 文件名含 "150" 计划 → 150
    if re.search(r"150计划|150买|150卖", basename):
        return "150"
    # 文字发车且无明确计划 → 默认 150（主计划）
    if "文字发车" in basename:
        return "150"
    return None


def extract_operations(
    content: str,
    fund_code_priority: str = "场外",
    filepath: str = "",
) -> list[dict[str, Any]]:
    """从一篇博客的 markdown 内容中提取所有操作记录。

    多行上下文解析策略：
      1. 轻量预处理：仅合并跨行的基金代码括号
      2. 合并跨行的操作前缀与基金代码行
      3. 按行扫描，识别计划头（150/S）进入上下文（支持行首和行中）
      4. 在计划上下文中，逐行检测包含买入/卖出+基金代码的行并解析
      5. 不忽略缩进行
      6. 无计划头时，从文件名推断计划

    Args:
        content: 博客的 markdown 内容
        fund_code_priority: 基金代码优先级，"场外" 或 "场内"
        filepath: 文件路径，用于计划推断

    Returns:
        list[dict]: 操作记录列表，每条格式：
            {"plan": "150"|"S", "action": "buy"|"sell",
             "shares": float, "fund_name": "建信500",
             "fund_code": "000478"}
    """
    # 步骤 1：轻量预处理，修复跨行基金代码
    normalized = _normalize_content(content)

    # 步骤 2：分割为行并 strip（保留缩进行）
    raw_lines = [line.strip() for line in normalized.split("\n")]

    # 步骤 3：合并跨行操作前缀与基金代码行
    lines = _merge_operation_continuations(raw_lines)

    # 步骤 4：多行上下文扫描
    operations: list[dict[str, Any]] = []
    current_plan: str | None = None

    for line in lines:
        if not line:
            continue

        # --- 检测计划段落头（支持行首和行中） ---
        plan_match = _PLAN_HEADER_PATTERN.search(line)
        if plan_match:
            current_plan = plan_match.group(1)
            # 检查计划头之后是否同时包含完整的操作信息
            after_header = line[plan_match.end():].strip()
            after_header = re.sub(r"^[：:]", "", after_header).strip()
            if after_header and ("买入" in after_header or "卖出" in after_header):
                ops = _parse_operation_line(after_header, fund_code_priority)
                for op in ops:
                    op["plan"] = current_plan
                    operations.append(op)
            continue

        # --- 在计划上下文中解析操作行 ---
        if current_plan is None:
            # 无计划上下文：先尝试从文件路径推断
            if filepath:
                inferred = _infer_plan_from_filename(filepath)
                if inferred:
                    current_plan = inferred
                    logger.debug("从文件名推断计划: %s → %s", os.path.basename(filepath), inferred)
            # 文件名推断失败，尝试从已解析内容推断
            if current_plan is None:
                inferred = _infer_plan_from_content(normalized)
                if inferred:
                    current_plan = inferred
                    logger.debug("从内容推断计划: %s → %s", os.path.basename(filepath) if filepath else "", inferred)
            if current_plan is None:
                continue

        # 核心条件：同时包含买入/卖出词 且 基金代码模式
        if ("买入" in line or "卖出" in line) and re.search(
            r"[（(]场[外内].*?\d{6}", line
        ):
            ops = _parse_operation_line(line, fund_code_priority)
            for op in ops:
                op["plan"] = current_plan
                operations.append(op)

    return operations


# ---------------------------------------------------------------------------
# 构建持仓
# ---------------------------------------------------------------------------

def build_positions(operations: list[dict[str, Any]]) -> dict[str, Any]:
    """累加所有操作记录，产出当前理论持仓。

    对每个计划下的每个品种，累加买入份数、卖出份数，计算净持仓。

    Args:
        operations: 操作记录列表

    Returns:
        dict: 持仓结构，格式：
            {
                "150": {
                    "建信500": {
                        "shares": 8.0,
                        "fund_code": "000478",
                        "total_buy": 12.0,
                        "total_sell": 4.0,
                    },
                    ...
                },
                "S": { ... }
            }
    """
    positions: dict[str, dict[str, dict[str, Any]]] = {}

    for op in operations:
        plan = op["plan"]
        fund_name = op["fund_name"]
        fund_code = op["fund_code"]
        action = op["action"]
        shares = op["shares"]

        if plan not in positions:
            positions[plan] = {}

        if fund_name not in positions[plan]:
            positions[plan][fund_name] = {
                "shares": 0.0,
                "fund_code": fund_code,
                "total_buy": 0.0,
                "total_sell": 0.0,
            }

        entry = positions[plan][fund_name]
        if action == "buy":
            entry["shares"] += shares
            entry["total_buy"] += shares
        else:
            entry["shares"] -= shares
            entry["total_sell"] += shares

        # 更新基金代码（以后出现的为准）
        entry["fund_code"] = fund_code

    return positions


# ---------------------------------------------------------------------------
# 路径C：微博精选汇总式操作记录提取与交叉校验
# ---------------------------------------------------------------------------

def extract_summary_records(content: str, source_file: str = "") -> list[dict[str, Any]]:
    """从博客内容中提取所有汇总式操作记录（路径C）。

    匹配博主在微博精选中使用的"年度操作汇总"格式，例如：
      - "150卖出19份，买入14份，没有在高位区域加仓..."
      - "S卖出9份，买入14份，但要注意S是永续买入模式..."
      - "150卖出5份" / "S买入10份"（单边独立操作）

    该函数是独立的，不依赖 extract_operations()，可直接对任意博客文本使用。

    Args:
        content: 博客原始内容（markdown 文本）
        source_file: 来源文件名，用于校验报告溯源

    Returns:
        list[dict]: 汇总式记录列表，每条格式：
            {
                "plan": "150" | "S",
                "sell_shares": float,
                "buy_shares": float,
                "net_change": float,
                "source_line": str,
                "source_file": str,
            }
    """
    records: list[dict[str, Any]] = []

    # --- 模式1: 同一句内包含买卖双方 ---

    # 150/S 卖出X份...买入Y份
    for m in _SUMMARY_SELL_BUY_PATTERN.finditer(content):
        records.append({
            "plan": m.group(1),
            "sell_shares": float(m.group(2)),
            "buy_shares": float(m.group(3)),
            "net_change": float(m.group(3)) - float(m.group(2)),
            "source_line": m.group(0).strip(),
            "source_file": source_file,
        })

    # 150/S 买入Y份...卖出X份
    for m in _SUMMARY_BUY_SELL_PATTERN.finditer(content):
        records.append({
            "plan": m.group(1),
            "sell_shares": float(m.group(3)),
            "buy_shares": float(m.group(2)),
            "net_change": float(m.group(2)) - float(m.group(3)),
            "source_line": m.group(0).strip(),
            "source_file": source_file,
        })

    # --- 模式2: 单边独立操作（排除已被模式1匹配的行） ---
    # 收集模式1已覆盖的行范围，避免重复
    covered_ranges: set[tuple[int, int]] = set()
    for m in _SUMMARY_SELL_BUY_PATTERN.finditer(content):
        covered_ranges.add((m.start(), m.end()))
    for m in _SUMMARY_BUY_SELL_PATTERN.finditer(content):
        covered_ranges.add((m.start(), m.end()))

    for m in _SUMMARY_SINGLE_OP_PATTERN.finditer(content):
        # 跳过已被模式1覆盖的匹配
        if any(s <= m.start() <= e or s <= m.end() <= e for s, e in covered_ranges):
            continue

        action = "买入" if "买入" in m.group(0) else "卖出"
        shares = float(m.group(2))
        record = {
            "plan": m.group(1),
            "sell_shares": shares if action == "卖出" else 0.0,
            "buy_shares": shares if action == "买入" else 0.0,
            "net_change": shares if action == "买入" else -shares,
            "source_line": m.group(0).strip(),
            "source_file": source_file,
        }
        records.append(record)

    return records


def extract_all_summary_records(blog_dir: str) -> list[dict[str, Any]]:
    """遍历博客目录，从所有文件中提取汇总式操作记录。

    Args:
        blog_dir: 博客目录绝对路径

    Returns:
        list[dict]: 所有文件中的汇总式记录列表
    """
    all_records: list[dict[str, Any]] = []
    all_files = list_blog_files(blog_dir)

    for filepath in all_files:
        try:
            content = read_blog(filepath)
            filename = os.path.basename(filepath)
            records = extract_summary_records(content, source_file=filename)
            if records:
                all_records.extend(records)
                logger.debug("  [%s] 提取 %d 条汇总记录", filename, len(records))
        except Exception:
            logger.debug(
                "读取汇总记录失败，跳过: %s",
                os.path.basename(filepath), exc_info=True,
            )

    logger.info("共提取 %d 条汇总式操作记录（路径C）", len(all_records))
    return all_records


def validate_with_summaries(
    positions: dict[str, Any],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """将汇总式记录（路径C）与发车解析累算结果（路径B）做交叉校验。

    校验逻辑：
      1. 从 positions 中计算每个 plan 的 total_buy 和 total_sell（跨所有品种）
      2. 与每条 summary 记录对比
      3. 如果 summary 的 sell/buy <= 路径B 的累计值 → 一致（summary 为子集）
      4. 如果 summary 的 sell/buy > 路径B 的累计值 → 可能有遗漏

    Args:
        positions: build_positions() 返回的持仓字典
        summaries: extract_summary_records() 返回的汇总记录列表

    Returns:
        dict: 校验报告，格式：
            {
                "total_summaries": int,
                "passed": int,
                "warnings": list[str],
                "plan_totals": {"150": {"buy": X, "sell": Y}, "S": {...}},
                "details": [...],  # 每条 summary 的逐项校验详情
            }
    """
    # 从 positions 计算每个计划的累计买卖总量
    plan_totals: dict[str, dict[str, float]] = {}
    for plan, plan_data in positions.items():
        total_buy = sum(v["total_buy"] for v in plan_data.values())
        total_sell = sum(v["total_sell"] for v in plan_data.values())
        plan_totals[plan] = {"buy": total_buy, "sell": total_sell}

    warnings: list[str] = []
    passed_count = 0
    details: list[dict[str, Any]] = []

    for idx, summary in enumerate(summaries):
        plan = summary["plan"]
        summary_sell = summary["sell_shares"]
        summary_buy = summary["buy_shares"]

        # 获取该计划在路径B中的累计值
        b_totals = plan_totals.get(plan, {"buy": 0.0, "sell": 0.0})
        b_buy = b_totals["buy"]
        b_sell = b_totals["sell"]

        # 逐项判断
        buy_ok = summary_buy <= b_buy
        sell_ok = summary_sell <= b_sell

        detail = {
            "index": idx + 1,
            "plan": plan,
            "summary_buy": summary_buy,
            "summary_sell": summary_sell,
            "summary_net": summary["net_change"],
            "b_buy": b_buy,
            "b_sell": b_sell,
            "b_net": b_buy - b_sell,
            "buy_ok": buy_ok,
            "sell_ok": sell_ok,
            "source_line": summary["source_line"],
            "source_file": summary["source_file"],
        }
        details.append(detail)

        if buy_ok and sell_ok:
            passed_count += 1
        else:
            parts: list[str] = []
            if not buy_ok:
                parts.append(
                    f"{plan}计划博主自述买入{summary_buy:.0f}份 > "
                    f"发车解析累计{b_buy:.1f}份"
                )
            if not sell_ok:
                parts.append(
                    f"{plan}计划博主自述卖出{summary_sell:.0f}份 > "
                    f"发车解析累计{b_sell:.1f}份"
                )
            src = summary["source_file"] or "未知来源"
            warning = (
                f"[汇总记录#{idx + 1}] {src}: "
                f"{'; '.join(parts)}，可能有遗漏"
            )
            warnings.append(warning)

    return {
        "total_summaries": len(summaries),
        "passed": passed_count,
        "warnings": warnings,
        "plan_totals": plan_totals,
        "details": details,
    }


def append_validation_report(md_path: str, validation: dict[str, Any]) -> None:
    """将交叉校验报告追加到 Markdown 持仓表文件末尾。

    Args:
        md_path: Markdown 文件绝对路径
        validation: validate_with_summaries() 返回的校验报告
    """
    v = validation
    total = v["total_summaries"]
    passed = v["passed"]
    warnings = v["warnings"]
    plan_totals = v["plan_totals"]
    details = v["details"]

    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n\n")
        f.write("---\n\n")
        f.write("## 交叉校验（路径C：汇总式记录 vs 路径B：发车解析）\n\n")

        # 校验总览表格
        f.write("| 校验项 | 结果 |\n")
        f.write("|--------|------|\n")
        f.write(f"| 汇总式记录数 | {total} 条 |\n")
        f.write(f"| 通过校验 | {passed} 条 |\n")
        warn_count = len(warnings)
        f.write(f"| 差异警告 | {warn_count} 条 |\n")
        f.write("\n")

        # 按计划分组输出详细校验
        # 按 plan 分组 details
        from collections import defaultdict
        plan_details: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for d in details:
            plan_details[d["plan"]].append(d)

        for plan in sorted(plan_details.keys()):
            p_details = plan_details[plan]
            b_totals = plan_totals.get(plan, {"buy": 0.0, "sell": 0.0})
            b_buy = b_totals["buy"]
            b_sell = b_totals["sell"]
            b_net = b_buy - b_sell

            f.write(f"### {plan} 计划校验\n\n")
            f.write("| 数据源 | 累计买入 | 累计卖出 | 净变动 |\n")
            f.write("|--------|---------|---------|--------|\n")
            f.write(
                f"| 发车解析（路径B） | {b_buy:.1f} | {b_sell:.1f} | "
                f"{b_net:+.1f} |\n"
            )

            # 输出该计划下每条汇总记录及判断
            has_issue = False
            for d in p_details:
                d_net = d["summary_net"]
                src_label = d["source_file"] or "未知来源"
                # 截断过长的源行
                src_line = d["source_line"][:60]
                if len(d["source_line"]) > 60:
                    src_line += "..."

                buy_judge = "一致" if d["buy_ok"] else "博主数据 > 发车解析"
                sell_judge = "一致" if d["sell_ok"] else "博主数据 > 发车解析"
                net_judge = "一致" if (d["buy_ok"] and d["sell_ok"]) else "可能有遗漏"

                buy_icon = "✅" if d["buy_ok"] else "⚠️"
                sell_icon = "✅" if d["sell_ok"] else "⚠️"
                net_icon = "✅" if (d["buy_ok"] and d["sell_ok"]) else "⚠️"

                f.write(
                    f"| 博主自述 ({src_label}) | {d['summary_buy']:.0f} | "
                    f"{d['summary_sell']:.0f} | {d_net:+.0f} |\n"
                )
                f.write(
                    f"| **判断** | {buy_icon} {buy_judge} | "
                    f"{sell_icon} {sell_judge} | {net_icon} {net_judge} |\n"
                )

                if not (d["buy_ok"] and d["sell_ok"]):
                    has_issue = True

            # 如果有差异，追加备注说明
            if has_issue:
                f.write("\n> **注**：该计划存在差异警告，说明路径B（发车帖逐笔解析）可能遗漏了部分早期操作。\n")

        # 如果没有任何汇总记录
        if total == 0:
            f.write("> 未找到任何汇总式操作记录，跳过交叉校验。\n")


# ---------------------------------------------------------------------------
# 导出持仓表
# ---------------------------------------------------------------------------

def _detect_same_code_notes(positions: dict[str, Any]) -> dict[str, str]:
    """检测同一计划下代码相同但名称不同的品种，生成备注信息。

    Args:
        positions: 持仓结构字典

    Returns:
        dict[str, str]: 以 "plan|fund_name" 为键的备注字典
    """
    notes: dict[str, str] = {}
    for plan, plan_data in positions.items():
        # 按代码分组，找出代码相同但名称不同的品种
        code_groups: dict[str, list[str]] = {}
        for fund_name, entry in plan_data.items():
            code = entry["fund_code"]
            if code not in code_groups:
                code_groups[code] = []
            code_groups[code].append(fund_name)

        for code, names in code_groups.items():
            if len(names) > 1:
                for name in names:
                    other_names = [n for n in names if n != name]
                    key = f"{plan}|{name}"
                    notes[key] = f"同代码品种: {', '.join(other_names)}"

    return notes


def export_position_table(
    positions: dict[str, Any],
    output_dir: str,
    md_filename: str = "持仓还原表.md",
    csv_filename: str = "持仓还原表.csv",
) -> tuple[str, str]:
    """将持仓导出为 Markdown 表格和 CSV 文件。

    表格包含备注列，标注同一代码下可能存在不同名称的品种。

    Args:
        positions: 持仓结构字典
        output_dir: 输出目录绝对路径
        md_filename: Markdown 文件名
        csv_filename: CSV 文件名

    Returns:
        tuple[str, str]: (Markdown 文件路径, CSV 文件路径)
    """
    os.makedirs(output_dir, exist_ok=True)

    md_path = os.path.join(output_dir, md_filename)
    csv_path = os.path.join(output_dir, csv_filename)

    # 检测同代码品种备注
    notes_map = _detect_same_code_notes(positions)

    # 收集所有行数据
    rows: list[dict[str, Any]] = []
    for plan in sorted(positions.keys()):
        for fund_name in sorted(positions[plan].keys()):
            entry = positions[plan][fund_name]
            note_key = f"{plan}|{fund_name}"
            rows.append({
                "plan": plan,
                "fund_name": fund_name,
                "fund_code": entry["fund_code"],
                "shares": entry["shares"],
                "total_buy": entry["total_buy"],
                "total_sell": entry["total_sell"],
                "notes": notes_map.get(note_key, ""),
            })

    # --- 写入 Markdown ---
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 理论持仓还原表\n\n")
        f.write("> 基于所有发车帖的买入/卖出操作累加计算，不包含分红、拆分等调整。\n\n")
        f.write(
            "| 计划 | 基金名称 | 基金代码 | 净持仓(份) "
            "| 累计买入(份) | 累计卖出(份) | 备注 |\n"
        )
        f.write(
            "|------|---------|---------|-----------"
            "|-------------|-------------|------|\n"
        )
        for row in rows:
            notes = row["notes"] if row["notes"] else "-"
            f.write(
                f"| {row['plan']} | {row['fund_name']} | {row['fund_code']} "
                f"| {row['shares']:.1f} | {row['total_buy']:.1f} "
                f"| {row['total_sell']:.1f} | {notes} |\n"
            )
        f.write("\n")
        # 汇总统计
        for plan in sorted(positions.keys()):
            total_net = sum(v["shares"] for v in positions[plan].values())
            total_buy = sum(v["total_buy"] for v in positions[plan].values())
            total_sell = sum(v["total_sell"] for v in positions[plan].values())
            f.write(
                f"- **{plan}计划**：品种数 {len(positions[plan])}，"
                f"净持仓 {total_net:.1f} 份，"
                f"累计买入 {total_buy:.1f} 份，"
                f"累计卖出 {total_sell:.1f} 份\n"
            )

    # --- 写入 CSV ---
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "计划", "基金名称", "基金代码", "净持仓(份)", "累计买入(份)", "累计卖出(份)", "备注",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "计划": row["plan"],
                "基金名称": row["fund_name"],
                "基金代码": row["fund_code"],
                "净持仓(份)": f"{row['shares']:.1f}",
                "累计买入(份)": f"{row['total_buy']:.1f}",
                "累计卖出(份)": f"{row['total_sell']:.1f}",
                "备注": row["notes"],
            })

    logger.info("持仓表已导出: %s, %s", md_path, csv_path)
    return md_path, csv_path


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main() -> None:
    """主函数：处理所有发车帖，还原理论持仓并导出。

    流程：
      1. 加载配置
      2. 遍历 blog 目录，筛选发车帖（文件名匹配 + 内容回退）
      3. 对每篇发车帖提取操作记录
      4. 汇总所有操作，构建持仓
      5. 导出 Markdown 和 CSV 文件
    """
    config = get_config()

    # 从配置读取参数
    pos_config: dict[str, Any] = config.get("position_extraction", {})
    fache_keywords: list[str] = pos_config.get("fache_keywords", [
        "ETF计划", "长赢指数投资计划", "长赢投资计划", "文字发车",
    ])
    fund_code_priority: str = pos_config.get("fund_code_priority", "场外")
    filter_mode: str = pos_config.get("filter_mode", "filename_and_content")
    output_md_rel: str = pos_config.get("output_markdown", "docs/distilled/持仓还原表.md")
    output_csv_rel: str = pos_config.get("output_csv", "docs/distilled/持仓还原表.csv")

    blog_dir: str = config["paths"]["blog_dir"]
    project_root = get_project_root()

    # 解析输出路径
    output_md = output_md_rel if os.path.isabs(output_md_rel) else os.path.join(project_root, output_md_rel)
    output_csv = output_csv_rel if os.path.isabs(output_csv_rel) else os.path.join(project_root, output_csv_rel)
    output_dir = os.path.dirname(output_md)

    logger.info("=" * 60)
    logger.info("开始持仓还原")
    logger.info("博客目录: %s", blog_dir)
    logger.info("发车帖关键词: %s", fache_keywords)
    logger.info("筛选模式: %s", filter_mode)
    logger.info("基金代码优先级: %s", fund_code_priority)
    logger.info("输出目录: %s", output_dir)
    logger.info("=" * 60)

    # 1. 获取所有博客文件，筛选发车帖
    all_files = list_blog_files(blog_dir)
    logger.info("共找到 %d 篇博客", len(all_files))

    # 根据筛选模式决定筛选策略
    fache_files: list[str] = []
    for filepath in all_files:
        if filter_mode == "filename_only":
            # 仅文件名匹配
            if _is_fache_post(filepath, fache_keywords):
                fache_files.append(filepath)
        else:
            # "filename_and_content": 文件名匹配优先，失败则检查内容
            if _is_fache_post(filepath, fache_keywords):
                fache_files.append(filepath)
            else:
                try:
                    content = read_blog(filepath)
                    if _is_fache_post(filepath, fache_keywords, content):
                        fache_files.append(filepath)
                except Exception:
                    logger.debug("读取文件失败，跳过内容回退: %s", os.path.basename(filepath))

    logger.info("筛选出 %d 篇发车帖", len(fache_files))

    if not fache_files:
        logger.warning("未找到任何发车帖，退出")
        return

    # 2. 逐篇提取操作记录
    all_operations: list[dict[str, Any]] = []
    success_count = 0
    fail_count = 0

    for filepath in fache_files:
        filename = os.path.basename(filepath)
        try:
            content = read_blog(filepath)
            ops = extract_operations(content, fund_code_priority, filepath)
            if ops:
                all_operations.extend(ops)
                success_count += 1
                logger.debug("  [%s] 提取 %d 条操作", filename, len(ops))
            else:
                logger.debug("  [%s] 未提取到操作记录", filename)
        except Exception:
            fail_count += 1
            logger.warning("处理文件失败: %s", filename, exc_info=True)

    logger.info(
        "操作提取完成: 成功 %d 篇, 失败 %d 篇, 共提取 %d 条操作记录",
        success_count, fail_count, len(all_operations),
    )

    if not all_operations:
        logger.warning("未提取到任何操作记录，退出")
        return

    # 3. 构建持仓
    positions = build_positions(all_operations)

    # 打印持仓摘要
    for plan in sorted(positions.keys()):
        plan_data = positions[plan]
        total_net = sum(v["shares"] for v in plan_data.values())
        logger.info(
            "%s计划: %d 个品种, 净持仓 %.1f 份",
            plan, len(plan_data), total_net,
        )

    # 4. 导出
    md_path, csv_path = export_position_table(
        positions,
        output_dir,
        md_filename=os.path.basename(output_md),
        csv_filename=os.path.basename(output_csv),
    )

    # 5. 【步骤C】提取汇总式记录并执行交叉校验
    logger.info("-" * 60)
    logger.info("步骤C：提取汇总式操作记录（路径C）并交叉校验")
    summaries = extract_all_summary_records(blog_dir)

    if summaries:
        validation = validate_with_summaries(positions, summaries)
        append_validation_report(md_path, validation)

        logger.info("=== 校验结果 ===")
        logger.info(
            "汇总式记录: %d 条, 通过: %d 条, 警告: %d 条",
            validation["total_summaries"],
            validation["passed"],
            len(validation["warnings"]),
        )
        for w in validation["warnings"]:
            logger.warning("  %s", w)
    else:
        logger.info("未找到任何汇总式操作记录，跳过交叉校验")

    logger.info("=" * 60)
    logger.info("持仓还原完成！")
    logger.info("Markdown: %s", md_path)
    logger.info("CSV:      %s", csv_path)
    logger.info("=" * 60)


if __name__ == "__main__":
    # 设置基础日志（在 main 中会进一步配置）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    main()
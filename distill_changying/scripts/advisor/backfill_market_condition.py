"""观点库 market_condition 标签回填脚本

对「投资理念 + principle」类观点补标 market_condition（五档市场温度标签），
让 opinion_matcher 能按「当前市场温度档」优先召回 E大 的金句。

五档枚举对齐 temperature.py：钻石坑 / 低估 / 正常 / 高估 / 泡沫，无法判断时标「通用」。

标注分两层：
1. 规则层（默认，零成本）：关键词强信号单档命中直接标注；多档冲突或无命中标「通用」。
   多档冲突通常是「牛市卖出、熊市买入」这类双向金句，强行归类会失真，故诚实标「通用」。
2. LLM 层（可选 --use-llm）：检测 DEEPSEEK_API_KEY，对规则标「通用」的记录调 LLM 补标。

用法：
    python backfill_market_condition.py [--use-llm] [--input 观点库.jsonl] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter

# 旧值 → 五档 映射（统一历史遗留的自由文本枚举）
LEGACY_MAP = {
    "低估区": "低估",
    "高估区": "高估",
    "正常区": "正常",
    "市场恐慌": "钻石坑",
    "市场狂热": "泡沫",
    "钻石坑": "钻石坑",
}

# 五档关键词（只保留高置信度强信号，避免宽泛词导致双向金句误标）
MARKET_KW = {
    "钻石坑": ["钻石坑", "钻石", "弯腰捡", "恐慌", "崩盘", "历史大底", "超级大底",
               "黄金坑", "至暗", "绝望", "无人问津", "别人恐惧", "恐慌性"],
    "泡沫":   ["泡沫", "狂热", "疯狂", "追涨", "追高", "非理性", "亢奋", "击鼓传花",
               "赶顶", "盛宴", "狂欢", "博傻"],
    "高估":   ["高估", "止盈", "越涨越卖", "见好就收", "落袋", "减仓", "兑现"],
    "低估":   ["低估", "熊市", "越跌越买", "抄底", "建仓", "加仓", "低吸", "布局",
               "错杀", "底部区域"],
    "正常":   ["震荡", "垃圾时间", "区间", "中枢", "不预测", "耐心持有"],
}

# 标注对象：source_type 或 topic 命中的记录
TARGET_SOURCE_TYPES = ("principle",)
TARGET_TOPICS = ("投资理念",)


def is_target(record: dict) -> bool:
    """判断该记录是否需要标注 market_condition。"""
    if record.get("source_type") in TARGET_SOURCE_TYPES:
        return True
    if record.get("topic") in TARGET_TOPICS:
        return True
    return False


def rule_label(text: str) -> str:
    """规则标注：单档命中返回该档，否则返回「通用」。"""
    hits = [k for k, ws in MARKET_KW.items() if any(w in text for w in ws)]
    if len(hits) == 1:
        return hits[0]
    return "通用"


def normalize_legacy(value: str) -> str:
    """把历史遗留的自由文本枚举映射到五档。"""
    return LEGACY_MAP.get(value, value if value else "通用")


def backfill_rule(records: list[dict]) -> Counter:
    """规则层回填：仅对「通用」的记录标规则，已有五档标签的保持不动（幂等）。"""
    stats = Counter()
    for r in records:
        if not is_target(r):
            continue
        if r.get("market_condition") != "通用":
            # 已有五档标签（含历史遗留已归一化 / LLM 已补标），跳过避免覆盖
            continue
        r["market_condition"] = rule_label(r.get("opinion", ""))
        stats[r["market_condition"]] += 1
    return stats


# LLM 批量补标的 prompt 模板（一次判一批金句的五档状态）
LLM_LABEL_PROMPT = """你是熟悉"ETF拯救世界"(E大)投资思想的助手。以下是 E大 的多条投资理念/金句，请判断每条金句最适配的市场状态。

五档定义：
- 钻石坑：市场极度恐慌、估值极低、历史大底、无人问津
- 低估：市场低估、熊市、估值便宜、值得逢低买入
- 正常：市场震荡、估值合理、垃圾时间
- 高估：市场高估、牛市、估值贵、该止盈
- 泡沫：市场狂热、估值泡沫、极度亢奋

金句（序号. 内容）：
{items}

请输出 JSON 数组，每条对应：{{"id": 序号数字, "market_condition": "五档之一"}}
只返回 JSON 数组，不要其他文字。"""

# 合法五档（校验 LLM 输出，防止越界标签）
VALID_LABELS = {"钻石坑", "低估", "正常", "高估", "泡沫"}


def backfill_llm(records: list[dict], config: dict) -> Counter:
    """LLM 层补标：对规则标「通用」的记录分批调 LLM 判断五档。

    每批 20 条，一次调用返回整批的五档标签，控制成本与耗时。
    单批失败不中断整体，失败的记录保持「通用」。
    """
    # 延迟导入，避免无 LLM 依赖时加载失败
    from scripts.shared.llm_utils import build_llm_client, call_deepseek_json

    stats = Counter()
    targets = [r for r in records if is_target(r) and r.get("market_condition") == "通用"]
    if not targets:
        return stats

    client = build_llm_client(config)
    model = config["api"].get("model", "deepseek-chat")
    batch_size = 20

    for start in range(0, len(targets), batch_size):
        batch = targets[start:start + batch_size]
        numbered = "\n".join(
            f"{i + 1}. {r.get('opinion', '')}" for i, r in enumerate(batch)
        )
        prompt = LLM_LABEL_PROMPT.format(items=numbered)
        batch_no = start // batch_size + 1
        try:
            result = call_deepseek_json(
                client=client,
                model=model,
                messages=[
                    {"role": "system", "content": "你是熟悉E大投资思想的助手，只输出 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2500,
                timeout=120,
            )
        except Exception as e:
            print(f"[LLM 补标] 第 {batch_no} 批调用失败: {e}", file=sys.stderr)
            continue

        # 归一化 LLM 返回为 items 列表（兼容 list 与 dict 两种形态）
        if isinstance(result, list):
            items = result
        elif isinstance(result, dict):
            # dict 可能是 {"items": [...]} 包裹，或 {"1": "低估", ...} 数字键映射
            if isinstance(result.get("items"), list):
                items = result["items"]
            elif isinstance(result.get("results"), list):
                items = result["results"]
            else:
                items = [
                    {"id": k, "market_condition": v}
                    for k, v in result.items() if str(k).isdigit()
                ]
        else:
            print(f"[LLM 补标] 第 {batch_no} 批解析失败（返回 {type(result).__name__}）", file=sys.stderr)
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            label = item.get("market_condition")
            if label not in VALID_LABELS:
                continue
            try:
                idx = int(item.get("id")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                batch[idx]["market_condition"] = label
                stats[label] += 1
        print(f"[LLM 补标] 第 {batch_no} 批完成（{len(batch)} 条）")

    return stats


def main() -> int:
    # 确保 scripts 包可导入（脚本可能从任意 cwd 直接运行）
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)

    parser = argparse.ArgumentParser(description="观点库 market_condition 标签回填")
    parser.add_argument("--input", default="", help="观点库路径，默认 distill 产物目录下 观点库.jsonl")
    parser.add_argument("--use-llm", action="store_true", help="对规则标「通用」的记录调 LLM 补标")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写回")
    args = parser.parse_args()

    if not args.input:
        args.input = os.path.join(root, "docs", "distilled", "观点库.jsonl")

    if not os.path.isfile(args.input):
        print(f"文件不存在: {args.input}", file=sys.stderr)
        return 1

    with open(args.input, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    # 先统一历史遗留枚举
    for r in records:
        r["market_condition"] = normalize_legacy(r.get("market_condition", "通用"))

    # 规则层回填
    backfill_rule(records)

    # LLM 层补标（可选）
    if args.use_llm:
        from scripts.advisor.config import get_config
        backfill_llm(records, get_config())

    # 统计（回填完成后从 records 重算真实分布，避免累加计数失真）
    target_records = [r for r in records if is_target(r)]
    total_target = len(target_records)
    stats = Counter(r.get("market_condition", "通用") for r in target_records)
    print(f"标注对象（投资理念 + principle）: {total_target} 条")
    print("market_condition 分布（标注对象内）:")
    for k, v in stats.most_common():
        print(f"  {k}: {v} ({v * 100 // max(total_target, 1)}%)")

    if args.dry_run:
        print("\n[dry-run] 未写回文件")
        return 0

    # 备份 + 写回
    backup = args.input + ".bak"
    if not os.path.exists(backup):
        shutil.copyfile(args.input, backup)
        print(f"\n已备份原文件: {backup}")

    with open(args.input, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"已写回 {args.input}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

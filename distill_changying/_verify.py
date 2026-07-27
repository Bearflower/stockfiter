import json, os, sys

print("=== 9.1 时间戳验证 ===")
ops = [json.loads(l) for l in open('docs/distilled/操作时间线.jsonl') if l.strip()]
years = set()
for op in ops:
    t = op.get('time', '')
    if t and len(t) >= 4 and t[:4].isdigit():
        years.add(t[:4])
print(f"操作时间线: {len(ops)} 条, 年份: {sorted(years)} count={len(years)}")
assert len(years) >= 5, f"FAIL: {years}"

obs = [json.loads(l) for l in open('docs/distilled/近期判断库.jsonl') if l.strip()]
obs_years = set()
for o in obs:
    t = o.get('time', '')
    if t and len(t) >= 4 and t[:4].isdigit():
        obs_years.add(t[:4])
print(f"近期判断库: {len(obs)} 条, 年份: {sorted(obs_years)} count={len(obs_years)}")
assert len(obs_years) >= 5, f"FAIL: {obs_years}"

opinions = [json.loads(l) for l in open('docs/distilled/观点库.jsonl') if l.strip()]
principle_str = [o for o in opinions if o.get('time') == 'principle']
print(f"观点库 time=principle: {len(principle_str)} 条")
assert len(principle_str) == 0, "FAIL"

obs_with_source = [o for o in obs if o.get('source_type') == 'observation']
print(f"近期判断库 source_type=observation: {len(obs_with_source)}/{len(obs)}")
print("9.1 PASSED")

print("\n=== 9.2 导入 + 索引构建验证 ===")
from scripts.distill.persona_index import load_data, build_structured_index, save_index, load_index
from scripts.distill.persona_stats import compute_all_stats, generate_stats_report
from scripts.distill.persona_builder import build_persona, write_persona

data = load_data("docs/distilled")
print(f"数据加载: ops={len(data.get('operations',[]))}, obs={len(data.get('observations',[]))}, principles={len(data.get('principles',[]))}")

index = build_structured_index(data)
print(f"索引: fund={len(index.get('by_fund',{}))}, action={len(index.get('by_action',{}))}, topic={len(index.get('by_topic',{}))}, year={len(index.get('by_year',{}))}")
assert len(index['by_fund']) > 0, "索引为空"
assert len(index['by_year']) > 0, "年份索引为空"

save_index(index, "docs/distilled")
loaded = load_index(os.path.join("docs/distilled", "persona_index"))
assert len(loaded['by_fund']) == len(index['by_fund']), "保存/加载不一致"
print("9.2 PASSED")

print("\n=== 9.3 统计摘要验证 ===")
stats = compute_all_stats(data['operations'], data['observations'], data['principles'])
report = generate_stats_report(stats)
print(f"统计报告: {len(report)} 字符")
assert "操作频率" in report, "缺少操作频率"
assert "品种买卖" in report, "缺少品种买卖"
assert "收益率" in report, "缺少收益率"
assert "主题分布" in report, "缺少主题分布"
print("9.3 PASSED")

print("\n=== 9.4 画像导出验证 ===")
content = build_persona({}, distill_dir="docs/distilled")
print(f"画像文档: {len(content)} 字符")
assert "E大人物画像" in content, "缺少标题"
assert "交易操作时间线" in content, "缺少操作时间线"
assert "统计摘要" in content, "缺少统计摘要"
assert "PERSONA_PROMPT_TEMPLATE" not in content, "可能包含LLM编造"
write_persona(content, "docs/distilled")
assert os.path.isfile("docs/distilled/E大人物画像.md"), "文件未生成"
print("9.4 PASSED")

print("\n=== 9.5 查询接口验证 ===")
from scripts.advisor.persona_query import PersonaQuery
from scripts.distill.config import get_config
config = get_config()
# 验证导入和方法存在
assert hasattr(PersonaQuery, '_extract_keywords'), "缺少_extract_keywords"
assert hasattr(PersonaQuery, '_search_opinions'), "缺少_search_opinions"
print("9.5 PASSED (import + method check)")

print("\n=== ALL TESTS PASSED ===")
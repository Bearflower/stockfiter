#!/usr/bin/env python3
"""验证蒸馏产物"""
import json, os

distill_dir = '/Users/yl/vscode/stockfilter_v3/distill_changying/docs/distilled'

# 1. Check 观点库.jsonl
opinions_path = os.path.join(distill_dir, '观点库.jsonl')
source_types = {}
with open(opinions_path) as f:
    for line in f:
        if line.strip():
            r = json.loads(line)
            st = r.get('source_type', 'unknown')
            source_types[st] = source_types.get(st, 0) + 1
print(f"观点库.jsonl 分布: {source_types}")
print(f"总计: {sum(source_types.values())}")

# 2. Check 操作时间线.jsonl
ops_path = os.path.join(distill_dir, '操作时间线.jsonl')
ops_count = 0
if os.path.isfile(ops_path):
    with open(ops_path) as f:
        ops_count = sum(1 for _ in f if _.strip())
print(f"操作时间线.jsonl: {ops_count} 条")

# 3. Check 近期判断库.jsonl
obs_path = os.path.join(distill_dir, '近期判断库.jsonl')
obs_count = 0
if os.path.isfile(obs_path):
    with open(obs_path) as f:
        obs_count = sum(1 for _ in f if _.strip())
print(f"近期判断库.jsonl: {obs_count} 条")

# 4. Sample
with open(opinions_path) as f:
    for line in f:
        r = json.loads(line)
        if r.get('source_type') == 'operation':
            print(f"\n样品 operation: {json.dumps(r, ensure_ascii=False, indent=2)[:300]}")
            break
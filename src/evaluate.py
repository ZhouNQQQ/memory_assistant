"""
评估脚本：Mock 提取 vs 真实 LLM 提取，对比 F1
"""

import json
import sys
sys.path.insert(0, 'src')

from mock_extractor import mock_extract, compute_metrics
from extractor import MemoryExtractor

# 读取对话和 ground truth
with open('data/real_dialogs.jsonl', 'r') as f:
    dialogs = [json.loads(line) for line in f]

with open('data/ground_truth.json', 'r') as f:
    gt_data = json.load(f)

# 选择有 ground truth 的对话（前 5 个有标注的）
labeled_indices = [i for i, gt in enumerate(gt_data['dialogs']) if gt['memories']]
print(f"有标注的对话: {len(labeled_indices)} 个，索引: {labeled_indices}")

# 第一步：Mock 提取
print("\n" + "=" * 60)
print("第一步：Mock 提取（关键词匹配）")
print("=" * 60)

mock_total = {'tp': 0, 'fp': 0, 'fn': 0}
mock_results = []

for idx in labeled_indices[:5]:  # 先跑前 5 个
    dialog = dialogs[idx]
    gt_dialog = gt_data['dialogs'][idx]
    user_text = dialog['user']
    gt_memories = gt_dialog['memories']
    
    mock_facts = mock_extract(user_text)
    metrics = compute_metrics(mock_facts, gt_memories)
    
    mock_total['tp'] += metrics['tp']
    mock_total['fp'] += metrics['fp']
    mock_total['fn'] += metrics['fn']
    
    mock_results.append({
        'id': idx,
        'user_text': user_text[:100],
        'gt': gt_memories,
        'pred': mock_facts,
        'metrics': metrics
    })
    
    print(f"\nDialog {idx}: P={metrics['precision']:.2f} R={metrics['recall']:.2f} F1={metrics['f1']:.2f}")
    print(f"  GT ({len(gt_memories)}): {gt_memories}")
    print(f"  Mock ({len(mock_facts)}): {mock_facts}")

p = mock_total['tp'] / (mock_total['tp'] + mock_total['fp']) if (mock_total['tp'] + mock_total['fp']) > 0 else 0
r = mock_total['tp'] / (mock_total['tp'] + mock_total['fn']) if (mock_total['tp'] + mock_total['fn']) > 0 else 0
f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0

print(f"\n{'='*60}")
print(f"Mock 总计: Precision={p:.2f} Recall={r:.2f} F1={f1:.2f}")
print(f"TP={mock_total['tp']} FP={mock_total['fp']} FN={mock_total['fn']}")
print(f"{'='*60}")

# 保存 Mock 结果供后续对比
with open('data/mock_results.json', 'w') as f:
    json.dump({'mock_total': {'precision': p, 'recall': r, 'f1': f1, **mock_total}, 'details': mock_results}, f, ensure_ascii=False, indent=2)

print("\nMock 结果已保存到 data/mock_results.json")
print("\n下一步：跑真实 LLM 提取（GLM-4-Flash），需要 API 调用...")

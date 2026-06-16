"""
评估脚本：真实 LLM 提取 vs Mock 提取，对比 F1
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

# 选择有 ground truth 的对话
labeled_indices = [i for i, gt in enumerate(gt_data['dialogs']) if gt['memories']]
print(f"有标注的对话: {len(labeled_indices)} 个")

# 跑真实 LLM 提取（只跑前 5 个，节省 token）
print("\n" + "=" * 60)
print("真实 LLM 提取（GLM-4-Flash）")
print("=" * 60)

extractor = MemoryExtractor()
llm_total = {'tp': 0, 'fp': 0, 'fn': 0}
llm_results = []

for idx in labeled_indices[:5]:
    dialog = dialogs[idx]
    gt_dialog = gt_data['dialogs'][idx]
    user_text = dialog['user']
    gt_memories = gt_dialog['memories']
    
    try:
        memories = extractor.extract(user_text)
        llm_facts = [m.content for m in memories]
    except Exception as e:
        print(f"Dialog {idx} 提取失败: {e}")
        llm_facts = []
    
    metrics = compute_metrics(llm_facts, gt_memories)
    
    llm_total['tp'] += metrics['tp']
    llm_total['fp'] += metrics['fp']
    llm_total['fn'] += metrics['fn']
    
    llm_results.append({
        'id': idx,
        'user_text': user_text[:100],
        'gt': gt_memories,
        'pred': llm_facts,
        'metrics': metrics
    })
    
    print(f"\nDialog {idx}: P={metrics['precision']:.2f} R={metrics['recall']:.2f} F1={metrics['f1']:.2f}")
    print(f"  GT ({len(gt_memories)}): {gt_memories}")
    print(f"  LLM ({len(llm_facts)}): {llm_facts}")

p = llm_total['tp'] / (llm_total['tp'] + llm_total['fp']) if (llm_total['tp'] + llm_total['fp']) > 0 else 0
r = llm_total['tp'] / (llm_total['tp'] + llm_total['fn']) if (llm_total['tp'] + llm_total['fn']) > 0 else 0
f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0

print(f"\n{'='*60}")
print(f"LLM 总计: Precision={p:.2f} Recall={r:.2f} F1={f1:.2f}")
print(f"TP={llm_total['tp']} FP={llm_total['fp']} FN={llm_total['fn']}")
print(f"{'='*60}")

# 读取 Mock 结果
with open('data/mock_results.json', 'r') as f:
    mock_data = json.load(f)

mock_f1 = mock_data['mock_total']['f1']
mock_p = mock_data['mock_total']['precision']
mock_r = mock_data['mock_total']['recall']

print(f"\n对比总结:")
print(f"  Mock 提取:  P={mock_p:.2f} R={mock_r:.2f} F1={mock_f1:.2f}")
print(f"  LLM 提取:   P={p:.2f} R={r:.2f} F1={f1:.2f}")
print(f"  提升:       F1 +{f1 - mock_f1:.2f} ({(f1/max(mock_f1, 0.01) - 1)*100:.0f}%)")

# 保存结果
with open('data/llm_results.json', 'w') as f:
    json.dump({'llm_total': {'precision': p, 'recall': r, 'f1': f1, **llm_total}, 'details': llm_results}, f, ensure_ascii=False, indent=2)

print("\nLLM 结果已保存到 data/llm_results.json")

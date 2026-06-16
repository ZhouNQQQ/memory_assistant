"""
Mock 提取器（关键词匹配）
用于与真实 LLM 提取做对比，计算 F1。

规则：
- 从用户消息中提取包含关键词的事实
- 关键词：名字、偏好（喜欢/爱/讨厌）、职业、计划（打算/想/准备）、关系（女朋友/ wife / husband）
- 简单正则匹配，不涉及语义理解
"""

import re
import json
from typing import List


def mock_extract(dialog_text: str) -> List[str]:
    """用关键词规则从对话中提取事实（Mock 版）。"""
    facts = []
    text = dialog_text.lower()
    
    # 1. 提取名字（我叫/我是/名字是）
    name_patterns = [
        r'我叫(\S+?)，|我叫(\S+?)$|我的名字是(\S+?)[，。]|我是(\S+?)[，。]',
    ]
    for p in name_patterns:
        match = re.search(p, text)
        if match:
            name = match.group(1) or match.group(2) or match.group(3) or match.group(4)
            if name:
                facts.append(f"名字是{name}")
    
    # 2. 提取偏好（喜欢/爱/讨厌/不喜欢）
    pref_patterns = [
        r'喜欢(.+?)[，。]|爱(.+?)[，。]|讨厌(.+?)[，。]|不喜欢(.+?)[，。]',
    ]
    for p in pref_patterns:
        matches = re.findall(p, text)
        for m in matches:
            pref = next((x for x in m if x), None)
            if pref and len(pref) < 20:
                facts.append(f"喜欢{pref}")
    
    # 3. 提取职业（是...工程师/开发/架构师）
    job_patterns = [
        r'是(\S+?工程师)|是(\S+?开发)|是(\S+?架构师)|是(\S+?程序员)',
        r'职业是(\S+?)[，。]|工作[是叫](\S+?)[，。]',
    ]
    for p in job_patterns:
        match = re.search(p, text)
        if match:
            job = match.group(1) or match.group(2)
            if job:
                facts.append(f"职业是{job}")
    
    # 4. 提取计划（打算/想/准备/要）
    plan_patterns = [
        r'打算(.+?)[，。]|想(.+?)[，。]|准备(.+?)[，。]|要(.+?)[，。]',
    ]
    for p in plan_patterns:
        matches = re.findall(p, text)
        for m in matches:
            plan = next((x for x in m if x), None)
            if plan and len(plan) < 20:
                facts.append(f"打算{plan}")
    
    # 5. 提取关系（有/是...的）
    rel_patterns = [
        r'(.+?)[是我]的(男朋友|女朋友|老婆|老公|妻子|丈夫|父亲|母亲|爸爸|妈妈)',
        r'我有(一个|一位)?(\S+?)[，。]',
    ]
    for p in rel_patterns:
        matches = re.findall(p, text)
        for m in matches:
            rel = m[-1] if isinstance(m, tuple) else m
            if rel:
                facts.append(f"有{rel}")
    
    # 去重
    seen = set()
    unique = []
    for f in facts:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def normalize_fact(fact: str) -> str:
    """标准化事实字符串，用于匹配对比。"""
    f = re.sub(r'[，。！？\s]', '', fact)
    # 去掉常见前缀/后缀
    f = f.replace('用户的', '').replace('用户', '')
    f = f.replace('目的之一是', '').replace('目的是', '')
    f = f.replace('发现', '').replace('认为', '')
    f = f.replace('决定', '').replace('考虑', '')
    f = f.replace('关心', '').replace('希望', '')
    f = f.replace('询问', '')
    f = f.replace('之一', '')
    f = f.replace('经常', '')
    f = f.replace('需要', '')
    f = f.replace('建议', '')
    f = f.replace('未按照', '').replace('没有', '')
    f = f.replace('不懂', '')
    f = f.replace('什么', '')
    f = f.replace('喜欢', '').replace('讨厌', '').replace('不喜欢', '')
    f = f.replace('名字是', '').replace('名字叫', '')
    f = f.replace('职业是', '').replace('工作叫', '').replace('工作是', '')
    f = f.replace('打算', '').replace('想', '').replace('准备', '')
    f = f.replace('有', '')
    f = f.replace('的', '')
    f = f.replace('了', '')
    f = f.replace('是', '')
    return f.strip()


def match_fact(pred: str, ground: str) -> bool:
    """判断预测事实是否与 ground truth 匹配。"""
    pred_norm = normalize_fact(pred)
    ground_norm = normalize_fact(ground)
    # 如果标准化后太短，用原始包含关系
    if len(pred_norm) < 3 or len(ground_norm) < 3:
        pred_raw = pred.replace('用户', '')
        ground_raw = ground.replace('用户', '')
        if pred_raw in ground_raw or ground_raw in pred_raw:
            return True
        return False
    # 互相包含
    if pred_norm in ground_norm or ground_norm in pred_norm:
        return True
    # 检查较长公共子串
    for i in range(len(pred_norm)):
        for j in range(i+4, len(pred_norm)+1):
            substr = pred_norm[i:j]
            if substr in ground_norm and len(substr) >= 4:
                return True
    return False


def compute_metrics(predictions: List[str], ground_truths: List[str]) -> dict:
    """计算 Precision / Recall / F1。"""
    if not predictions and not ground_truths:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}
    
    matched_preds = set()
    matched_gts = set()
    
    for i, pred in enumerate(predictions):
        for j, gt in enumerate(ground_truths):
            if j in matched_gts:
                continue
            if match_fact(pred, gt):
                matched_preds.add(i)
                matched_gts.add(j)
                break
    
    tp = len(matched_preds)
    fp = len(predictions) - tp
    fn = len(ground_truths) - len(matched_gts)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


if __name__ == "__main__":
    # 自测
    test = "我叫李明，是一名后端工程师，喜欢喝咖啡，不喜欢吃辣。我有一个女朋友叫小红。"
    print("Mock 提取:", mock_extract(test))

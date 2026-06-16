"""
真实 LLM 记忆提取器 (GLM-4-Flash 版)
参考 mem0 的 FACT_RETRIEVAL_PROMPT 设计，但用自己的话重写，不照抄。

核心设计：
- 从对话中提取结构化记忆（MemoryItem）
- 用 OpenAI 兼容 SDK 调用 GLM-4-Flash
- 容错解析 LLM 返回的非标准 JSON
- 支持 retry（最多 3 次）
"""

import json
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional

from openai import OpenAI


@dataclass
class MemoryItem:
    """结构化记忆项"""
    content: str          # 记忆内容（事实陈述）
    category: str         # 分类：preference / personal / plan / activity / health / professional / misc
    importance: float     # 重要性 0.0-1.0
    entity: Optional[str] # 关联实体（人名/地名/物名等）
    confidence: float     # 提取置信度 0.0-1.0


# ---------------------------------------------------------------------------
# Prompt 设计（参考 mem0 的 7 类信息，用自己的话重写）
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """你是一个对话记忆提取专家。你的任务是从用户和助手的对话中，提取出关于用户的结构化事实记忆。

## 需要提取的信息类型

1. **个人偏好** (preference)：喜欢/不喜欢的事物，如食物、产品、活动、娱乐
2. **个人详情** (personal)：姓名、关系、重要日期、身份特征
3. **计划意图** (plan)： upcoming 事件、旅行、目标、待办事项
4. **活动服务** (activity)：餐饮、旅行、爱好、服务偏好
5. **健康 wellness** (health)：饮食限制、健身习惯、健康相关信息
6. **职业信息** (professional)：职位、工作习惯、职业目标、技能
7. **其他杂项** (misc)：书籍、电影、品牌、任何有价值的事实

## 提取规则

- 只提取**用户**消息中的事实，不要提取助手消息中的内容（除非助手提供了用户会参考的具体建议/计划）
- 不要提取纯寒暄（"你好"、"谢谢"）
- 不要提取过于泛泛的陈述（"树有树枝"）
- 每个事实要自包含，不用代词，用具体名称
- 保持原语言提取（中文对话用中文，英文用英文）
- 如果对话中没有可提取的事实，返回空数组

## 输出格式

必须返回合法的 JSON，格式如下：

```json
{
  "memories": [
    {
      "content": "用户喜欢川菜，尤其爱吃火锅",
      "category": "preference",
      "importance": 0.8,
      "entity": "火锅",
      "confidence": 0.95
    }
  ]
}
```

字段说明：
- content: 事实陈述，15-80字，自包含
- category: 从 7 类中选一个
- importance: 0.0-1.0，越重要越高
- entity: 关联的核心实体名称，没有则留空字符串
- confidence: 0.0-1.0，你对这条提取的确定程度

## 示例

Input: 用户: 你好
Output: {"memories": []}

Input: 用户: 我叫张明，是一名 Java 架构师，最近在研究大模型记忆系统。
Output:
{"memories": [
  {"content": "用户名叫张明", "category": "personal", "importance": 0.9, "entity": "张明", "confidence": 0.99},
  {"content": "用户是 Java 架构师", "category": "professional", "importance": 0.85, "entity": "Java", "confidence": 0.95},
  {"content": "用户最近在研究大模型记忆系统", "category": "plan", "importance": 0.8, "entity": "记忆系统", "confidence": 0.9}
]}

Input: 用户: 我上周去了杭州西湖，风景很美，但人太多了。下次想工作日去。
Output:
{"memories": [
  {"content": "用户上周去了杭州西湖，觉得风景很美但人太多", "category": "activity", "importance": 0.7, "entity": "西湖", "confidence": 0.9},
  {"content": "用户下次想工作日去西湖，避开人流", "category": "plan", "importance": 0.6, "entity": "西湖", "confidence": 0.85}
]}
"""


# ---------------------------------------------------------------------------
# JSON 容错解析（参考 mem0 的 extract_json + normalize_facts）
# ---------------------------------------------------------------------------

def extract_json(text: str) -> str:
    """从 LLM 返回的文本中提取 JSON 字符串。
    
    处理策略（按优先级）：
    1. 匹配 ```json ... ``` 代码块
    2. 匹配 ``` ... ``` 代码块
    3. 找第一个 '{' 和最后一个 '}' 之间的内容
    4. 返回原文本（让 json.loads 自己报错）
    """
    text = text.strip()
    # 策略 1: ```json\n{...}\n```
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 策略 2: ```\n{...}\n```
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 策略 3: 找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    # 策略 4: 返回原文
    return text


def parse_memories(raw_json: str) -> List[MemoryItem]:
    """解析 LLM 返回的 JSON，容错处理各种非标准格式。"""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}\n原始文本: {raw_json[:200]}")
    
    memories = data.get("memories", data.get("facts", []))
    if not isinstance(memories, list):
        raise ValueError(f"期望 memories 是 list，实际是 {type(memories).__name__}")
    
    result = []
    for item in memories:
        if isinstance(item, str):
            # 兼容 mem0 的纯字符串 facts 格式
            result.append(MemoryItem(
                content=item,
                category="misc",
                importance=0.5,
                entity=None,
                confidence=0.8
            ))
        elif isinstance(item, dict):
            result.append(MemoryItem(
                content=item.get("content", item.get("text", item.get("fact", ""))),
                category=item.get("category", "misc"),
                importance=float(item.get("importance", 0.5)),
                entity=item.get("entity") or None,
                confidence=float(item.get("confidence", 0.8))
            ))
        else:
            # 未知格式，跳过
            continue
    return result


# ---------------------------------------------------------------------------
# 提取器核心类
# ---------------------------------------------------------------------------

class MemoryExtractor:
    """基于真实 LLM 的记忆提取器"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "glm-4-flash",
        max_retries: int = 3,
        temperature: float = 0.1,
    ):
        self.api_key = api_key or os.getenv("GLM_API_KEY") or os.getenv("KIMI_API_KEY")
        self.base_url = base_url or os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        
        if not self.api_key:
            raise ValueError("缺少 API key，请设置 GLM_API_KEY 或 KIMI_API_KEY 环境变量")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
    
    def extract(self, dialog_text: str) -> List[MemoryItem]:
        """从对话文本中提取结构化记忆。
        
        Args:
            dialog_text: 对话文本，格式为 "用户: ...\n助手: ..." 或纯用户消息
            
        Returns:
            List[MemoryItem]: 提取出的记忆列表
        """
        user_prompt = f"请从以下对话中提取用户的结构化记忆。\n\n对话内容：\n{dialog_text}\n\n请只返回 JSON，不要其他解释。"
        
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self.temperature,
                    max_tokens=2048,
                )
                raw_text = response.choices[0].message.content
                raw_json = extract_json(raw_text)
                memories = parse_memories(raw_json)
                
                # 记录调用日志
                self._log_call(dialog_text, raw_text, memories, response.usage)
                return memories
                
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2 ** attempt  # 指数退避
                    print(f"[extract] 第 {attempt} 次尝试失败: {e}，{wait}s 后重试...")
                    time.sleep(wait)
                else:
                    break
        
        raise RuntimeError(f"提取失败（已重试 {self.max_retries} 次）: {last_error}")
    
    def _log_call(self, input_text: str, raw_output: str, memories: List[MemoryItem], usage):
        """打印调用日志（真实 API 调用记录）"""
        print(f"\n{'='*60}")
        print(f"[MemoryExtractor] API 调用日志")
        print(f"{'='*60}")
        print(f"模型: {self.model}")
        if usage:
            print(f"Token 消耗: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
        print(f"输入长度: {len(input_text)} 字符")
        print(f"提取记忆数: {len(memories)}")
        for i, m in enumerate(memories, 1):
            print(f"  [{i}] [{m.category}] {m.content} (importance={m.importance}, confidence={m.confidence}, entity={m.entity})")
        print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def extract_memories(dialog_text: str) -> List[MemoryItem]:
    """便捷函数：用默认配置提取记忆"""
    extractor = MemoryExtractor()
    return extractor.extract(dialog_text)


if __name__ == "__main__":
    # 自测：用 3 条真实对话验证
    test_dialogs = [
        # 对话 1：自我介绍 + 职业
        "用户: 你好，我叫李华，是一名后端开发工程师，主要用 Go 语言。最近在研究分布式系统的共识算法，比如 Raft 和 Paxos。",
        
        # 对话 2：偏好 + 计划
        "用户: 我喜欢喝美式咖啡，不加糖。周末打算去爬山，北京周边有什么推荐的山吗？\n助手: 北京周边推荐香山、妙峰山、百花山。\n用户: 香山人太多了，妙峰山听起来不错，我查一下路线。",
        
        # 对话 3：健康 + 活动 + 复杂关系
        "用户: 我最近开始健身了，每周去三次健身房，主要练力量和有氧。教练建议我多吃蛋白质，少吃精制碳水。另外，我女朋友叫小雨，她也想一起练，但她更喜欢瑜伽。",
    ]
    
    print("=" * 70)
    print("MemoryExtractor 自测 — 3 条真实对话")
    print("=" * 70)
    
    extractor = MemoryExtractor()
    all_memories = []
    
    for i, dialog in enumerate(test_dialogs, 1):
        print(f"\n>>> 对话 {i}:")
        print(dialog[:100] + "..." if len(dialog) > 100 else dialog)
        try:
            memories = extractor.extract(dialog)
            all_memories.extend(memories)
            print(f"<<< 提取成功: {len(memories)} 条记忆")
        except Exception as e:
            print(f"<<< 提取失败: {e}")
    
    print("\n" + "=" * 70)
    print(f"总计提取: {len(all_memories)} 条记忆")
    print("=" * 70)

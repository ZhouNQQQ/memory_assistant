# 学习资料：Mem0 检索链路源码精读

> 来源：`mem0/memory/main.py`（`_search_vector_store` 方法 + `search` 方法）
> 定位：看懂 mem0 是怎么做"海选→精排"的，尤其是 `over-fetch` 和 `rerank` 的衔接点。

---

## 一、检索链路总览（9 步流程）

```
用户输入 query
    │
    ▼
┌─────────────────────────────────────┐
│ Step 1: 预处理 query                 │
│  - 词形还原（lemmatize_for_bm25）    │
│  - 实体提取（extract_entities）      │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 2: query 向量化                 │
│  - embedding_model.embed(query)    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 3: 语义检索（向量搜索）          │
│  - 关键代码: max(limit*4, 60)        │
│  - 这就是 over-fetch                 │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 4: 关键词检索（BM25）            │
│  - vector_store.keyword_search()     │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 5: BM25 分数归一化              │
│  - 用 sigmoid 把原始 BM25 分数压到 [0,1] │
│  - 根据 query 长度自适应参数          │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 6: 实体关联加分（Entity Boost）  │
│  - 提取 query 中的实体               │
│  - 搜实体库，找到关联的记忆 ID        │
│  - 给这些记忆额外加分                 │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 7: 构建候选集                   │
│  - 从语义检索结果中提取候选           │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 8: 混合评分与排序               │
│  - score_and_rank(): 语义分 + BM25分 + 实体分 │
│  - 按综合分降序，取 top_k            │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ Step 9: 格式化输出                   │
│  - 转成 MemoryItem 字典              │
└─────────────────────────────────────┘
    │
    ▼
  ┌──────────────┐
  │ 如果 rerank=True │
  │ 调用 reranker   │
  │ 对结果再精排    │
  └──────────────┘
```

---

## 二、核心代码逐段解读

### 2.1 Over-fetch：为什么检索 4 倍量？

**源码位置**：`mem0/memory/main.py:1376`

```python
# Step 3: Semantic search (over-fetch for scoring pool)
internal_limit = max(limit * 4, 60)
semantic_results = self.vector_store.search(
    query=query, vectors=embeddings, top_k=internal_limit, filters=filters
)
```

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `limit` | `top_k`（用户传入，默认 20） | 最终要返回给用户的结果数 |
| `internal_limit` | `max(limit * 4, 60)` | 向量库实际检索的候选数 |

**为什么是 4 倍？**
- 如果 `limit=5`（最终要 5 条），向量库检索 `max(5*4, 60)=60` 条
- 如果 `limit=20`（最终要 20 条），向量库检索 `max(20*4, 60)=80` 条
- 如果 `limit=100`，向量库检索 `400` 条

**目的**：给后续的混合评分（BM25 + entity boost）和可选 reranker 提供充足的候选池。如果只检索 limit 条，BM25 和 entity boost 可能想加分的记忆根本没被向量检索捞到。

**注意**：mem0 的 over-fetch 不是基于 recall@k 曲线的实验数据，而是工程经验值。在工业级调优时，应该用真实数据测试 recall@internal_limit 来确定这个倍数。

---

### 2.2 混合检索：向量 + 关键词

**源码位置**：`mem0/memory/main.py:1372-1384`

```python
# Step 2: Embed query
embeddings = self.embedding_model.embed(query, "search")

# Step 3: Semantic search
internal_limit = max(limit * 4, 60)
semantic_results = self.vector_store.search(
    query=query, vectors=embeddings, top_k=internal_limit, filters=filters
)

# Step 4: Keyword search
keyword_results = self.vector_store.keyword_search(
    query=query_lemmatized, top_k=internal_limit, filters=filters
)
```

| 检索方式 | 负责什么 | 特点 |
|----------|----------|------|
| **Semantic search**（向量） | 语义相似度 | 能找"likes"和"enjoys"的语义关联 |
| **Keyword search**（BM25） | 关键词匹配 | 对专有名词、ID、精确词很准 |

**两者不是取交集，而是取并集后混合打分。**

---

### 2.3 混合评分公式

**源码位置**：`mem0/utils/scoring.py:60-138`

```python
def score_and_rank(
    semantic_results,  # 向量检索结果（含 score）
    bm25_scores,       # 关键词检索的 BM25 分数（已归一化到 [0,1]）
    entity_boosts,     # 实体关联的额外加分（最大 0.5）
    threshold,         # 语义分最低门槛（默认 0.1）
    top_k,             # 最终返回条数
    explain=False      # 是否返回详细分数
):
    # 根据有哪些信号，计算满分基准
    max_possible = 1.0
    if has_bm25:   max_possible += 1.0       # 语义+BM25，满分=2.0
    if has_entity: max_possible += 0.5       # 再加实体分，满分=2.5
    
    for result in semantic_results:
        semantic_score = result.get("score", 0.0)
        if semantic_score < threshold:         # 语义分低于门槛的直接淘汰
            continue
        
        bm25_score = bm25_scores.get(mem_id, 0.0)
        entity_boost = entity_boosts.get(mem_id, 0.0)
        
        raw_combined = semantic_score + bm25_score + entity_boost
        combined = min(raw_combined / max_possible, 1.0)
        # 最终分数 = (语义 + 关键词 + 实体) / 满分基准
```

**评分逻辑拆解：**

```
假设某条记忆的分数：
  语义分 = 0.80
  BM25 分 = 0.30（这条记忆里有 query 里的关键词）
  实体分 = 0.20（这条记忆和 query 中的某个实体关联）
  
  满分基准 = 2.0（语义 1.0 + BM25 1.0 + 实体 0.5，但实体没满所以按 2.0 算）
  
  综合分 = (0.80 + 0.30 + 0.20) / 2.0 = 0.65
```

**threshold 的作用**：即使 BM25 分和实体分很高，如果语义分低于 threshold（默认 0.1），这条记忆直接淘汰。这是防止关键词匹配召回完全不相关的记忆。

---

### 2.4 Entity Boost：图记忆给检索加分

**源码位置**：`mem0/memory/main.py:1463-1543`（`_compute_entity_boosts`）

```python
def _compute_entity_boosts(self, query_entities, filters):
    # 1. 从 query 中提取最多 8 个实体（去重）
    # 2. 把每个实体向量化
    # 3. 在实体库里搜索相似实体（top_k=500，阈值 0.5）
    # 4. 找到实体关联的记忆 ID，给这些记忆加分
    
    # 加分公式：
    boost = 相似度 × 0.5(ENTITY_BOOST_WEIGHT) × 记忆数量权重
    
    # 记忆数量权重：某个实体关联的记忆越多，单个记忆加分越少
    # 防止"热门实体"把所有相关记忆都推上去
    memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
```

**举例：**
```
Query: "张三在 X 项目中的方案是什么"
提取实体: ["张三", "X 项目"]

实体库中："张三" 这个实体关联了 100 条记忆
         → 每条记忆加分 = 0.9 × 0.5 × (1.0 / (1 + 0.001 × 99²)) ≈ 0.045

         "X 项目" 这个实体关联了 5 条记忆
         → 每条记忆加分 = 0.9 × 0.5 × (1.0 / (1 + 0.001 × 4²)) ≈ 0.43

结果：提到"X 项目"的记忆被大幅推前，提到"张三"的 100 条记忆只轻微加分。
```

---

### 2.5 Reranker 接入点

**源码位置**：`mem0/memory/main.py:1247-1257`

```python
original_memories = self._search_vector_store(query, effective_filters, limit, threshold, explain=explain)

# Apply reranking if enabled and reranker is available
if rerank and self.reranker and original_memories:
    try:
        reranked_memories = self.reranker.rerank(query, original_memories, limit)
        original_memories = reranked_memories
    except Exception as e:
        logger.warning(f"Reranking failed, using original results: {e}")
```

**关键点：**
- Reranker 是在 `_search_vector_store` 完成后才调用的
- 输入给 Reranker 的是已经经过混合评分后的 `original_memories`（最多 `limit` 条，默认 20 条）
- Reranker 接收 `(query, documents, top_k)`，对 documents 逐对精排，返回 top_k 条

**注意**：mem0 的 Reranker 输入只有 `limit` 条（默认 20），而不是 `internal_limit` 条（60~80）。这意味着 Reranker 做的是**最后一公里的精排**，而不是从更大的候选池里重新筛选。这和论文里"从 100 个候选里重排"的思路略有不同。

---

## 三、三种 Reranker 实现对比

**源码位置**：`mem0/reranker/`

| 类型 | 实现文件 | 原理 | 速度 | 成本 | 适用场景 |
|------|----------|------|------|------|----------|
| **HuggingFace** | `huggingface_reranker.py` | 本地加载 Cross-Encoder 模型（如 `BAAI/bge-reranker-base`），逐 batch 打分 | 中等（GPU 加速） | 零 API 费用，需 GPU 内存 | 本地部署、有 GPU |
| **Cohere** | `cohere_reranker.py` | 调用 Cohere rerank API，云端 Cross-Encoder | 快（云端） | 按调用计费 | 无 GPU、愿付费 |
| **LLM** | `llm_reranker.py` | 用 LLM（如 GPT-4o-mini）做相关性打分，prompt 里给评分标准 | 慢（逐条过 LLM） | 高（每条都要 LLM API） | 精度要求极高、预算充足 |

### HuggingFace Reranker 核心代码
```python
# 输入: [[query, doc1], [query, doc2], ...]
# tokenizer 把每对拼接成 [CLS] query [SEP] doc [SEP]
inputs = self.tokenizer(batch_pairs, padding=True, truncation=True, max_length=512)

# model 输出 logits → 相关性分数
outputs = self.model(**inputs)
batch_scores = outputs.logits.squeeze(-1).cpu().numpy()
```

### LLM Reranker 核心 Prompt
```python
_SYSTEM_PROMPT = (
    "You are a relevance scoring assistant. "
    "Given a query and a document, score how relevant the document is to the query.\n\n"
    "Score the relevance on a scale from 0.0 to 1.0, where:\n"
    "- 1.0 = Perfectly relevant and directly answers the query\n"
    "- 0.8-0.9 = Highly relevant with good information\n"
    "- 0.6-0.7 = Moderately relevant with some useful information\n"
    "- 0.4-0.5 = Slightly relevant with limited useful information\n"
    "- 0.0-0.3 = Not relevant or no useful information\n\n"
    "Respond with only a single numerical score between 0.0 and 1.0."
)
```

---

## 四、链路设计的关键取舍

| 设计点 | mem0 的做法 | 为什么这样设计 |
|--------|-------------|---------------|
| Over-fetch 倍数 | `max(limit*4, 60)` | 经验值，给混合评分和 reranker 留候选池 |
| Reranker 输入量 | `limit` 条（默认 20） | 如果输入 60 条给 Reranker，Cross-Encoder 延迟更高 |
| Threshold 门槛 | 默认 0.1 | 语义分低于 0.1 的，即使 BM25 高也不入围 |
| Entity Boost 上限 | 0.5 | 防止实体关联完全压倒语义相似度 |
| 记忆数量权重衰减 | `1/(1+0.001*(n-1)²)` | 热门实体的关联记忆太多，不能每条都推 |

---

## 五、面试考点

| 问题 | 答案要点 |
|------|----------|
| "mem0 检索链路有几步？" | 9 步：预处理→嵌入→语义检索→关键词检索→BM25归一化→实体加分→候选集构建→混合评分→格式化 |
| "over-fetch 怎么算？" | `max(limit*4, 60)`，向量库实际检索量比最终返回多 |
| "为什么混合评分时语义分低于 threshold 直接淘汰？" | 防止关键词匹配召回完全不相关的记忆 |
| "Entity Boost 为什么有记忆数量权重衰减？" | 热门实体关联记忆太多，不能每条都推，要按关联数量降权 |
| "Reranker 在链路中的位置？" | 在 `_search_vector_store` 完成后，对已有结果精排 |
| "mem0 支持哪几种 Reranker？" | HuggingFace（本地模型）、Cohere（API）、LLM（通用模型打分） |
| "LLM Reranker 的 prompt 设计有什么讲究？" | 用 system prompt 锁定评分标准，截断输入防 prompt 注入（`_MAX_INPUT_LEN = 4000`），失败时 fallback 到 0.5 分 |

---

> 源码路径：`~/IdeaProjects/mem0-source/mem0/memory/main.py`（`_search_vector_store` 方法）

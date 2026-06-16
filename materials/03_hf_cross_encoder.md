# 学习资料：HuggingFace Cross-Encoder 实践指南

> 来源：`https://www.sbert.net/examples/applications/cross-encoder/README.html`
> 定位：用代码把 Bi-Encoder / Cross-Encoder 的差异跑明白，理解"为什么两段式是工业标准"。

---

## 一、Bi-Encoder vs Cross-Encoder 的代码差异

### Bi-Encoder（双塔）

```python
from sentence_transformers import SentenceTransformer

# 1. 加载模型
model = SentenceTransformer('all-MiniLM-L6-v2')

# 2. 分别编码 query 和 document
query_embedding = model.encode("用户喜欢川菜")
doc_embedding = model.encode("用户偏好四川菜")

# 3. 计算余弦相似度
from sklearn.metrics.pairwise import cosine_similarity
score = cosine_similarity([query_embedding], [doc_embedding])[0][0]
print(score)  # 输出: 0.85（示例）
```

**特点：**
- `query_embedding` 和 `doc_embedding` 是**独立计算的**
- Document 的 embedding 可以**预先计算并保存**（比如存到向量库里）
- 新 query 来了，只编码 query 一次，然后和预存的 doc 向量做点积
- **100 万个文档的检索**：只需 1 次 query 编码 + 100 万次点积（向量库用 HNSW 优化到毫秒级）

### Cross-Encoder（交叉编码器）

```python
from sentence_transformers import CrossEncoder

# 1. 加载模型（注意是 CrossEncoder，不是 SentenceTransformer）
model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L6-v2')

# 2. 输入必须是 [query, doc] 对
score = model.predict([["用户喜欢川菜", "用户偏好四川菜"]])
print(score)  # 输出: [0.92]（示例，比 Bi-Encoder 更准）
```

**特点：**
- `query` 和 `doc` 必须**同时输入**模型
- 模型内部会把它们拼接成 `[CLS] query [SEP] doc [SEP]`，让注意力在两者之间交互
- **Cross-Encoder 不输出 sentence embedding**，只输出一个相似度分数
- **100 万个文档的检索**：要构造 100 万个 [query, doc] 对，逐个过模型 → 算力爆炸

---

## 二、关键数字对比（来自 HF 文档原文）

> **文档原话**："Clustering 10,000 sentence with Cross-Encoders would require computing similarity scores for about 50 Million sentence combinations, which takes about 65 hours. With a Bi-Encoder, you compute the embedding for each sentence, which takes only 5 seconds."

| 任务 | Bi-Encoder | Cross-Encoder | 差距 |
|------|-----------|---------------|------|
| 10,000 句子聚类（两两算相似度） | **5 秒** | **65 小时** | 4.7 万倍 |
| 原理 | 各编码一次 → 向量点积 | 每对拼接后过 Transformer | — |
| 可预计算 | ✅ 是 | ❌ 否 | — |
| 精度 | 较粗（独立编码） | 更准（交互 attention） | Cross-Encoder 胜 |

**这个 65 小时 vs 5 秒的数字，就是两段式检索的根本原因。**

---

## 三、两段式检索的完整代码示例

### 场景：从 10,000 条记忆中找与 query 相关的 top-5

```python
from sentence_transformers import SentenceTransformer, CrossEncoder
import numpy as np

# ========== Stage 1: Bi-Encoder 海选 ==========

bi_encoder = SentenceTransformer('all-MiniLM-L6-v2')

# 10,000 条记忆（预先编码并存入向量库）
corpus = [
    "用户喜欢川菜",
    "用户偏好四川菜",
    "用户不喜欢辣",
    "用户讨厌花椒",
    "用户上周吃了火锅",
    # ... 共 10,000 条
]

# 预计算所有记忆的 embedding（只需做一次）
corpus_embeddings = bi_encoder.encode(corpus, show_progress_bar=True)

# 新 query 来了
def stage1_recall(query, corpus_embeddings, top_k=20):
    """Bi-Encoder 快速召回 top-20"""
    query_embedding = bi_encoder.encode(query)
    
    # 余弦相似度（可用 faiss/hnsw 加速到毫秒级）
    similarities = np.dot(corpus_embeddings, query_embedding)
    
    # 取 top-20
    top_indices = np.argsort(similarities)[::-1][:top_k]
    return [(corpus[i], similarities[i]) for i in top_indices]

# ========== Stage 2: Cross-Encoder 决赛 ==========

cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L6-v2')

def stage2_rerank(query, candidates, top_k=5):
    """Cross-Encoder 对候选精排，取 top-5"""
    # 构造 [query, doc] 对
    pairs = [[query, doc] for doc, _ in candidates]
    
    # 批量打分
    scores = cross_encoder.predict(pairs)
    
    # 按分数排序，取 top_k
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]

# ========== 完整调用 ==========

query = "用户喜欢吃什么口味的菜？"

# Stage 1: 从 10,000 条中召回 20 条
candidates = stage1_recall(query, corpus_embeddings, top_k=20)
# 耗时: ~10ms

# Stage 2: 对 20 条精排，取 5 条
final_results = stage2_rerank(query, candidates, top_k=5)
# 耗时: ~20 × 50ms = 1s

for (doc, bi_score), ce_score in final_results:
    print(f"Doc: {doc}")
    print(f"  Bi-Encoder score: {bi_score:.3f}")
    print(f"  Cross-Encoder score: {ce_score:.3f}")
    print()
```

### 可能的输出

```
Doc: 用户偏好四川菜
  Bi-Encoder score: 0.851
  Cross-Encoder score: 0.923    ← 推前了，因为 attention 捕获了"喜欢"和"偏好"的关联

Doc: 用户喜欢川菜
  Bi-Encoder score: 0.892
  Cross-Encoder score: 0.901

Doc: 用户上周吃了火锅
  Bi-Encoder score: 0.743
  Cross-Encoder score: 0.654    ← 下降了，Cross-Encoder 认为"火锅"和"喜欢吃什么口味"关系较弱

Doc: 用户不喜欢辣
  Bi-Encoder score: 0.712
  Cross-Encoder score: 0.421    ← 大幅下降，attention 捕获了"不"的否定语义

Doc: 用户讨厌花椒
  Bi-Encoder score: 0.698
  Cross-Encoder score: 0.315    ← 同样下降，Cross-Encoder 精准识别否定态度
```

**观察**：Cross-Encoder 能区分"喜欢"和"不喜欢"，Bi-Encoder 可能把两者都排前面（因为都含有"喜欢""川菜"等词，向量相似度高）。

---

## 四、batch 推理优化

Cross-Encoder 支持 batch 输入，减少 GPU 空闲时间：

```python
# 20 个候选，batch_size=8
pairs = [[query, doc] for doc in candidate_docs]

# 不是逐个 predict，而是批量
scores = cross_encoder.predict(pairs, batch_size=8)
# 耗时从 20 × 100ms = 2s 降到 3 × 100ms = 300ms
```

mem0 的 HuggingFace Reranker 也用了 batch：
```python
for i in range(0, len(doc_texts), self.config.batch_size):  # batch_size=32
    batch_docs = doc_texts[i:i + self.config.batch_size]
    batch_pairs = [[query, doc] for doc in batch_docs]
    inputs = self.tokenizer(batch_pairs, ...)
    outputs = self.model(**inputs)
```

---

## 五、模型选型参考

| 模型 | 大小 | 适用场景 | HuggingFace ID |
|------|------|----------|----------------|
| MiniLM-L6 | 小（~20MB） | 速度优先、资源有限 | `cross-encoder/ms-marco-MiniLM-L6-v2` |
| MiniLM-L12 | 中（~40MB） | 速度和精度的平衡 | `cross-encoder/ms-marco-MiniLM-L12-v2` |
| Electra-base | 较大 | 精度优先 | `cross-encoder/ms-marco-electra-base` |
| BGE Reranker | 中文优化 | 中文场景 | `BAAI/bge-reranker-base` |

mem0 默认用的是 `BAAI/bge-reranker-base`（中文优化）。

---

## 六、常见误区

| 误区 | 真相 |
|------|------|
| "Cross-Encoder 可以输出 sentence embedding 存到向量库" | ❌ 不能。Cross-Encoder 只输出分数，不输出向量。能存向量的是 Bi-Encoder。 |
| "Cross-Encoder 可以单独做检索" | ❌ 理论上可以，但 100 万候选要过 100 万次模型，65 小时的延迟不可接受。 |
| "Cross-Encoder 一定比 Bi-Encoder 好" | ✅ 精度上好，但速度和扩展性上差。不是替代关系，是互补关系。 |
| "batch_size 越大越好" | ❌ 受 GPU 显存限制。`max_length=512` 时，batch_size=32 约需 512×32×4 bytes ≈ 64MB 显存（实际更多，因为还有模型参数和中间激活）。 |

---

## 七、面试考点

| 问题 | 答案要点 |
|------|----------|
| "Bi-Encoder 和 Cross-Encoder 的输入有什么区别？" | Bi-Encoder 分别输入 query/doc，各自出向量；Cross-Encoder 拼接输入 `[query, doc]`，出分数 |
| "10,000 句子聚类，Cross-Encoder 要多久？Bi-Encoder 呢？" | Cross-Encoder 65 小时（5000 万对）；Bi-Encoder 5 秒（各编码一次） |
| "Cross-Encoder 能预计算 document 向量吗？" | 不能，必须 query+doc 同时输入 |
| "batch 推理为什么能加速？" | 减少 GPU 空闲时间，并行处理多个 pair 的前向传播 |
| "mem0 默认用哪个 Cross-Encoder 模型？" | `BAAI/bge-reranker-base`（中文优化） |
| "两段式中，如果 Bi-Encoder 召回的 top-20 里没有正确答案，Cross-Encoder 还有用吗？" | 没用。两段式的有效前提是 Bi-Encoder 的 recall@k 足够高。 |

---

> 原文：`https://www.sbert.net/examples/applications/cross-encoder/README.html`

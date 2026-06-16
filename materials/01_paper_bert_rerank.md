# 学习资料：Passage Re-ranking with BERT（论文精读）

> 来源：Nogueira & Cho, 2019, arXiv:1901.04085
> 定位：这篇论文不是"发明了 Cross-Encoder"，而是证明**预训练语言模型（BERT）可以直接做 passage re-ranking**，并给出了工业级落地的数据。

---

## 一、论文核心贡献（一句话概括）

用 BERT 做 query-passage 拼接后的交互式打分，在 MS MARCO  passage 检索任务上，比当时的 SOTA（BM25 基线）提升了 **27% MRR@10**。

---

## 二、为什么之前的方案不行？BM25 的盲区

### BM25 做了什么
BM25 看的是词频匹配：query 里的词在 passage 里出现多少次。

```
Query: "user likes spicy food"
Passage A: "user enjoys hot cuisine"
Passage B: "user likes spicy food"
```
BM25 眼里：B 得分高（exact match），A 得分低（"likes"→"enjoys" 不算匹配）。

但人眼里：A 和 B 语义等价，A 得分应该也很高。

**这就是词袋模型的语义盲区：词不同但语义相同时，打分会错。**

### 神经网络之前的方案
- 用 LSTM 学 query 和 passage 的向量表示 → 然后点积
- 但效果一直不如 BM25，因为监督数据太少，模型不够强

---

## 三、BERT 怎么做 Re-ranking（输入格式 + 注意力交互）

### 输入格式
```
[CLS] user likes spicy food [SEP] user enjoys hot cuisine [SEP]
        ↓
    BERT 12层 Transformer
        ↓
    [CLS] token 的输出向量 → 全连接层 → 相似度分数（0~1）
```

### 关键设计：为什么不是分别编码？

| 方案 | 输入方式 | 注意力交互 | 结果 |
|------|----------|------------|------|
| **Bi-Encoder（双塔）** | query 单独输入 BERT，passage 单独输入 BERT | query 和 passage 各自内部交互，**互相之间没有 attention** | 向量各算各的，然后点积 |
| **Cross-Encoder（交叉）** | query+passage 拼接后一起输入 BERT | 每个 token 都能看到 query 和 passage 的所有 token，**全局 attention** | 直接输出相似度分数 |

### 注意力交互图示
```
Query:  [Q1] [Q2] [Q3] [Q4]  [SEP]
Doc:    [D1] [D2] [D3] [D4] [D5] [D6]

Bi-Encoder:
  Q1 只能和 Q2/Q3/Q4 做 attention
  D1 只能和 D2/D3/D4/D5/D6 做 attention
  Q1 永远看不到 D1 的语义
  → 最后靠向量点积"猜"关系

Cross-Encoder:
  Q1 可以和 Q2/Q3/Q4/D1/D2/D3/D4/D5/D6 全部做 attention
  → "likes" 和 "enjoys" 的 attention weight 会高
  → 模型直接学到语义等价关系
```

---

## 四、实验数据：为什么这个提升有说服力

| 数据集 | 任务 | 基线（BM25） | BERT Re-ranker | 提升 |
|--------|------|------------|---------------|------|
| **MS MARCO** | Passage ranking | MRR@10 = 0.195 | MRR@10 = 0.247 | **+27%** |
| **TREC-CAR** | Paragraph ranking | 原 SOTA | **新 SOTA** | 登顶 |

> MRR@10（Mean Reciprocal Rank）含义：看前 10 个结果里第一个正确答案的位置。如果排第 1，贡献 1.0；排第 2，贡献 0.5；排第 3，贡献 0.33... 最后取平均。MRR@10 提升 27% 意味着第一个正确答案的平均排名明显前移。

---

## 五、训练细节：怎么教 BERT 打分？

### 数据集：MS MARCO
- 每个 query 对应一组 passage
- 标注：一个 relevant passage（正例），其他 irrelevant（负例）

### 训练任务：二分类
```
输入: [CLS] query [SEP] passage [SEP]
标签: 1（相关）或 0（不相关）
损失: Cross-Entropy
```

### 预测时怎么做排序？
- 对每个 query，把所有候选 passage 和 query 拼接
- 每个拼接过一次 BERT，得到相关概率
- 按概率从大到小排序

---

## 六、论文的局限（为什么现在用两段式）

论文用的是纯 Cross-Encoder：每个 query 和所有候选 passage 逐对拼接后过 BERT。

**问题：**
- MS MARCO 每次 query 只有几百个候选 passage（已经从 BM25 筛过一轮）
- 如果候选是 100 万个，每个 query 要过 100 万次 BERT → **计算爆炸**

**所以工业界走了两段式：**
```
Stage 1: BM25 / Bi-Encoder 快速召回 top-100（从 100 万中）
Stage 2: Cross-Encoder 对 top-100 精排
```

这篇论文证明了"Stage 2 用 BERT 非常有效"，但没有解决 Stage 1 的速度问题。

---

## 七、与 mem0 的关联

mem0 的检索链路（见 `02_mem0_search_pipeline.md`）本质上就是这篇论文思想的应用：
- **第一阶段**：向量检索（Bi-Encoder 思想的延伸）+ BM25 召回 `max(limit*4, 60)` 个
- **第二阶段**：可选 Cross-Encoder reranker（HuggingFace/Cohere/LLM）对召回结果精排
- mem0 的 `score_and_rank` 还加了 **entity boost**（图记忆关联加分），这是论文没有的

---

## 八、面试考点

| 问题 | 答案要点 |
|------|----------|
| "Cross-Encoder 为什么比 BM25 准？" | BERT 做注意力交互，捕获语义等价（"likes"→"enjoys"），BM25 只看词频 |
| "这篇论文的数据提升是多少？" | MS MARCO +27% MRR@10 |
| "为什么不用纯 Cross-Encoder 做全流程检索？" | 100 万候选要过 100 万次 BERT，计算不可行 |
| "输入格式是什么？" | `[CLS] query [SEP] passage [SEP]`，取 `[CLS]` 输出做分类 |
| "这和 Bi-Encoder 的核心区别是什么？" | Cross-Encoder 是交互式（attention 在 query+doc 之间），Bi-Encoder 是独立编码后点积 |

---

> 原文：`https://arxiv.org/abs/1901.04085`

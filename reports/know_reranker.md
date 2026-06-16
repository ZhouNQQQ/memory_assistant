# K.5 Reranker：Cross-Encoder vs Bi-Encoder

> 口述时间：2026-06-12
> 状态：✅ 通过（核心概念正确，类比用词可优化）

---

## 一、Q14 口述批改

### 口述原文

> "这样设计的主要目的是为了类似限流或者削峰的思路，如果样本量不大，那甚至没必要海选，直接 cross encoder 就行了。Bi-Encoder 负责从海量数据中找到相关性高的数据，可以接受一定的噪音。Cross-Encoder 负责将最相关的数据放到前面几位。"

### 🟢 正确的点

1. **Bi-Encoder 负责海选，接受噪音** —— 正确。核心指标是 recall@k。
2. **Cross-Encoder 负责精排** —— 正确。核心指标是 MRR/NDCG。
3. **样本量小可直接 Cross-Encoder** —— 正确。候选池 < 500 条时，两段式不必要。

### 🟡 可优化的点

**类比用词不够准确。** "限流/削峰"是流量控制概念，两段式是**精度-速度 trade-off**。

更好的类比：
- **招聘**：HR 初筛（看关键词，快但粗）→ 技术面（深入问，准但慢）
- **搜索**：倒排索引召回（快）→ 人工判断相关性（准）

### 🔴 缺了的内容（面试追问点）

1. **为什么必须两段式？** 缺了关键数字：10,000 句子聚类，Cross-Encoder 65 小时 vs Bi-Encoder 5 秒。
2. **两段式有效的硬前提：** Bi-Encoder 的 recall@k 必须足够高，否则 Cross-Encoder 没机会排。
3. **指标分工：** 没有提到 recall@k（Bi-Encoder）和 MRR/NDCG（Cross-Encoder）的对应关系。

### 标准答案（30 秒版）

> "两段式的设计是**精度-速度 trade-off** 的工业标准做法。Bi-Encoder 分别编码 query 和 doc，可预计算，10,000 条检索只需 5 秒，但精度较粗；Cross-Encoder 把 query+doc 拼接后一起过 Transformer，attention 能捕获语义等价（如 '喜欢' 和 '偏好'），但 10,000 条两两比较要 65 小时。所以 Stage 1 用 Bi-Encoder 快速召回 top-100，Stage 2 用 Cross-Encoder 对 100 条精排。两段式有效的前提是 Bi-Encoder 的 recall@k 足够高——如果真实相关项不在候选池里，Cross-Encoder 再准也没用。"

---

## 二、错题本（K.1-K.5 口述错误汇总）

### ❌ 错误1：Over-fetch 4 倍的主观描述

**错误表述**："4 倍是经验值，平衡召回率和精排成本"
**正确理解**：Over-fetch 的客观依据是 **recall@k 评估曲线的边际收益拐点**。2 倍时 recall≈65%（漏太多），4 倍时 recall≈90%（拐点），8 倍时 recall≈95%（只多 5% 但成本翻倍）。
**来源**：K.5 口述时用户追问

### ❌ 错误2：两段式变成"大量不准+少量准"

**错误认知**：担心 Bi-Encoder 海选后"大量不准+少量准"导致两段式失效
**正确理解**：**这正是设计意图**。20 个候选里可能只有 3-5 个真正相关，但关键是这 3-5 个必须在 20 个里面。Cross-Encoder 的职责不是"滤掉噪声"，而是"把准的排到前面"。
**来源**：K.5 口述时用户追问

### ❌ 错误3：缩小 over-fetch 能加大 recall

**错误表述**："缩小 over-fetch 也有可能加大 recall"
**正确理解**：**缩小 over-fetch 只会降低 recall**，不会升高。over-fetch 越大，候选池越大，recall 越高（边际递减）。
**来源**：K.5 口述时用户追问

### ❌ 错误4：BM25 在 Cross-Encoder 之后

**错误表述**："先做两段式，然后使用 BM25"
**正确理解**：BM25 是 **第一阶段（召回阶段）的补充信号**，在 Cross-Encoder 之前。mem0 的链路：向量检索 + BM25 检索 → 混合评分 → 可选 Cross-Encoder 精排。
**来源**：K.5 口述时

### ❌ 错误5：两段式无效的应对

**错误表述**："recall@20 只有 30% 说明两段式不适合这个场景，放弃两段式"
**正确理解**：先优化召回阶段：①换更强的 embedding 模型；②微调；③加 BM25 混合检索；④加大 over-fetch。**放弃两段式只在候选池很小（<500 条）时才考虑。**
**来源**：K.5 口述时用户追问

### ❌ 错误6："限流/削峰"类比

**错误表述**：两段式目的是"类似限流或者削峰"
**正确理解**：两段式是**精度-速度 trade-off**，不是流量控制。更好的类比是招聘初筛→技术面。
**来源**：K.5 Q14 口述

---

## 三、知识要点速查表

| 概念 | 一句话定义 | 核心指标 | 易错点 |
|------|-----------|---------|--------|
| **Bi-Encoder** | query/doc 分别编码，点积求相似 | 速度、recall@k | 可预计算，但捕获不了细粒度语义 |
| **Cross-Encoder** | query+doc 拼接后一起编码，attention 交互 | 精度、MRR/NDCG | 不可预计算，每对都要过 Transformer |
| **两段式** | Bi-Encoder 海选 → Cross-Encoder 决赛 | recall@k（阶段1）+ MRR（阶段2） | 前提是阶段1 recall 足够高 |
| **Over-fetch** | 向量检索量 = max(limit×4, 60) | 召回率 | 是经验值，非 universal truth |
| **Entity Boost** | query 中的实体 → 搜实体库 → 关联记忆加分 | 最大 0.5 | 有记忆数量衰减（热门实体降权） |
| **混合评分** | (语义 + BM25 + 实体) / max_possible | 综合分 | 语义分低于 threshold 直接淘汰 |
| **recall@k** | 前 k 个覆盖多少真实相关项 | 0~1 | 完全依赖数据集，不是固定值 |
| **MRR** | 第一个相关项的平均排名倒数 | 0~1 | 只看第一个相关项的位置 |
| **F1** | Precision 和 Recall 的调和平均 | 0~1 | 不是越高越好，要看基线对比 |

---

## 四、面试高压题预演

### 追问："F1 只有 70%，不够生产级"

**标准回应（30 秒）：**

> "70% 确实不是终点。但这个数字有三个背景：①对比基线从 10% 提升到 67%，是从'完全不可用'到'初步可用'的跨越；②评估函数用的是硬匹配（公共子串），'喜欢川菜'和'偏好四川菜'算不匹配，用 embedding 语义相似度重新评估会更高；③这是提取阶段的评估，后面还有去重、embedding、检索、reranker 各阶段独立优化。下一步：①改用语义相似度评估；②加 few-shot prompt；③扩测试集到 50 轮。"

---

## 五、学习资料索引

| 资料 | 路径 | 内容 |
|------|------|------|
| 论文精读 | `materials/01_paper_bert_rerank.md` | Passage Re-ranking with BERT 论文拆解 |
| 源码精读 | `materials/02_mem0_search_pipeline.md` | mem0 检索链路 9 步流程 + 混合评分公式 |
| 实践指南 | `materials/03_hf_cross_encoder.md` | HF Cross-Encoder 代码示例 + 两段式完整代码 |

---

> 产出物：`reports/know_reranker.md`（本文件）  
> 学习资料：`materials/01_paper_bert_rerank.md`、`materials/02_mem0_search_pipeline.md`、`materials/03_hf_cross_encoder.md`  
> 关联面试题：Q14、Q15（后续高压追问）

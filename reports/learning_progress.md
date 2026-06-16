# 学习进度记录：2026-06-11 ~ 2026-06-12

## 已完成任务

| 日期 | 任务 | 标题 | 状态 | 口述/面试情况 |
|------|------|------|------|--------------|
| 06-11 | K.1 | CQRS + 双水位一致性 | ✅ | 口述通过，Q15 答出频率/成本差异 + 双水位 + 幂等 |
| 06-11 | K.2 | Rolling Summary + Background Review | ✅ | 口述通过，覆盖热冷路径、分层合并、防实体丢失 |
| 06-11 | K.4 | Graph Memory（关系图记忆 vs 向量记忆） | ✅ | 口述通过，Q18 答出关系推理/结构化查询/规模三个标准 |
| 06-12 | K.5 | Reranker：Cross-Encoder vs Bi-Encoder | ✅ | 口述通过，Q14 核心概念正确，6个错题已记录 |

## 产出物清单

### 报告文件

| 文件 | 路径 | 内容 |
|------|------|------|
| CQRS 报告 | `reports/know_cqrs.md` | CQRS + 双水位一致性口述稿 |
| Rolling Summary 报告 | `reports/know_rolling_summary.md` | 热冷路径 + Background Review |
| Graph Memory 报告 | `reports/know_graph_memory.md` | 向量 vs 图 + 混合方案 |
| Reranker 报告 | `reports/know_reranker.md` | Q14 批改 + 错题本 + 知识速查 + 高压题预演 |

### 学习资料（额外参考资源压缩整理）

| 文件 | 路径 | 来源 | 内容 |
|------|------|------|------|
| 论文精读 | `materials/01_paper_bert_rerank.md` | Nogueira & Cho, 2019 | BERT 做 passage re-ranking 的原理 + 注意力交互图示 + MS MARCO +27% 数据 |
| 源码精读 | `materials/02_mem0_search_pipeline.md` | mem0/memory/main.py | 9 步检索链路 + over-fetch 源码 + 混合评分公式 + Entity Boost + 三种 Reranker 实现 |
| 实践指南 | `materials/03_hf_cross_encoder.md` | HuggingFace sbert.net | Bi-Encoder vs Cross-Encoder 代码对比 + 65 小时 vs 5 秒数字 + 两段式完整 Python 代码 + batch 优化 |

### 错题本（K.1-K.5 累计 6 条）

| 编号 | 错误内容 | 正确理解 | 来源 |
|------|---------|---------|------|
| 1 | Over-fetch 是"经验值" | 是 recall@k 曲线的边际收益拐点 | K.5 追问 |
| 2 | 担心两段式变成"大量不准+少量准" | 这正是设计意图，Cross-Encoder 负责把准的排前面 | K.5 追问 |
| 3 | 缩小 over-fetch 能加大 recall | 缩小只会降低 recall | K.5 追问 |
| 4 | BM25 在 Cross-Encoder 之后 | BM25 是召回阶段的补充信号，在 Cross-Encoder 之前 | K.5 口述 |
| 5 | recall@20=30% 就放弃两段式 | 先优化召回阶段，放弃只在候选池<500条时考虑 | K.5 追问 |
| 6 | 两段式是"限流/削峰" | 是精度-速度 trade-off，不是流量控制 | K.5 Q14 |

## 面试题状态更新

| 题号 | 题目 | 关联 Task | 状态 | 备注 |
|------|------|-----------|------|------|
| Q14 | Bi-Encoder 海选→Cross-Encoder 决赛，为什么这样设计？ | K.5 | 🟢 熟练 | 口述通过，核心概念正确 |
| Q15 | 记忆系统为什么天然适合 CQRS？ | K.1 | 🟢 熟练 | 口述通过 |
| Q16 | Rolling Summary 的热路径和冷路径怎么分界？ | K.2 | 🟢 熟练 | 口述通过 |
| Q18 | 什么时候该上 Graph Memory，什么时候纯向量就够？ | K.4 | 🟢 熟练 | 口述通过 |
| Q17 | Compaction 什么时候触发？失败了怎么恢复？ | K.3 | 🔴 待答 | K.3 未开始 |
| Q19 | Background Review 和对话结束触发写入有什么区别？ | K.2 | 🟡 已答 | 口述时覆盖，但未单独出题 |

## 下一步计划

1. **K.3 Compaction + 时间衰减策略** —— 最后一个 Phase 2.5 知识类任务
2. **回到代码主线：Task 1.4**（真实 Embedding + 阈值重标定）
3. **Task 1.5** 口述演练（暂存，已超时）

## 累计产出统计

| 类别 | 数量 |
|------|------|
| 口述报告 | 4 份（K.1/K.2/K.4/K.5） |
| 学习资料 | 3 份（论文/源码/实践） |
| 代码产出 | 3 份（extractor.py / mock_extractor.py / evaluate_llm.py） |
| 数据集 | 1 份（17 轮对话 + ground truth） |
| 错题本 | 6 条 |


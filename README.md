# kimiclaw-memory

给本地 QClaw / KimiClaw 补一个**自动记忆增强层**的实践项目。

## 它解决什么问题（Task 0.3 实测结论）

QClaw 本地（`~/.qclaw/workspace/`）已有记忆载体——`USER.md`、`SOUL.md`、`IDENTITY.md` 等 markdown 文件，agent 启动时会加载它们。但 QClaw **没有自动记忆引擎**：这些文件靠 agent 自己"手动想起来就写"，没有从对话里自动提取、去重、更新记忆的流水线。

本项目就是补这个短板：
- 从对话自动提取结构化记忆（LLM）
- 去重 / 冲突检测 / ADD-UPDATE-DELETE 决策（真实 embedding）
- 写回 QClaw 的 `USER.md`（markdown 契约）+ 结构化存储
- 跨会话注入（QClaw 启动自动加载 = 注入"免费"）

## 与 mem0-source 的关系

参考 + 改造，**不复制**。源码在 `~/IdeaProjects/mem0-source`（Python 包：embeddings/llms/memory/reranker/vector_stores，含 openclaw 集成）。Phase 2 会真实改它的源码并跑它的测试。

## 接入契约（Phase 3 目标）

| 路径 | 角色 | 本项目动作 |
|------|------|-----------|
| `~/.qclaw/workspace/USER.md` | 用户长期记忆（markdown） | 自动提取后写入/更新 |
| `~/.qclaw/workspace/SOUL.md` | 行为/偏好/边界 | 偏好类记忆可更新 |
| `~/.qclaw/qmemory/*.json` | 会话/任务运行态（非长期记忆） | 只读参考 |

## 环境

- mem0 要求 Python ≥3.10，系统是 3.9.7 → 用 `uv` 建 3.11 venv
- LLM：Kimi（Moonshot，OpenAI 兼容），key 放 `.env`

## 目录

```
src/      记忆模块代码（extractor/updater/embedding/writer/injector）
reports/  对比报告 / 口述稿 / 调研（Task 0.3 等）
tests/    测试
data/     真实对话数据集 + ground truth
```

## 进度

以 `AIS/mem0-course-optimization-v3.2.md` 第〇部分状态表为唯一真相。

# kimiclaw-memory

给本地 QClaw / KimiClaw 补一个**自动记忆增强层**的实践项目。

---

## 可嵌入记忆框架（embeddable-memory-framework）

本项目现已封装为一个**可嵌入的 mem0 记忆框架**，对外提供两个集成面：

- **Python 包**：`from kimiclaw_memory import Memory`
- **MCP server**（主集成方式）：控制台入口 `kimiclaw-memory-mcp`，供 Kimi/Daimon 及任何 MCP 客户端调用

> 主集成目标是 **Kimi.app（Daimon 运行时）**，与其原生记忆**共存、互不侵入**：本框架是一个可被 agent 主动调用的额外长期记忆源，**绝不读写 Daimon 的 vault/transcripts**，数据独立存放于 `~/.kimiclaw_memory/`。

### 安装

```bash
# 系统 Python 3.9 不满足 mem0ai，需 3.11 venv
uv venv --python 3.11
uv pip install -e .
```

### 配置（环境变量 / .env）

| 变量 | 说明 |
|------|------|
| `KIMI_API_KEY` / `KIMI_BASE_URL` / `KIMI_MODEL` | 主 LLM（Moonshot，OpenAI 兼容） |
| `ZHIPU_API_KEY`（兜底） | 智谱 GLM-4-Flash |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` | 嵌入模型（默认沿用 LLM 凭证，base_url 须支持 `/embeddings`） |
| `GITHUB_REPO` / `GITHUB_TOKEN` | 可选：开启 GitHub 远程同步（两者齐备才启用） |
| `KIMICLAW_DATA_DIR` | 可选：本框架数据目录（默认 `~/.kimiclaw_memory`） |

### Python 用法

```python
from kimiclaw_memory import Memory

with Memory.from_env() as mem:
    mem.add("我叫 Alice，喜欢吃火锅", user_id="u1")
    hits = mem.search("用户喜欢什么", user_id="u1", limit=5)
    mem.compact(user_id="u1")           # 时间衰减 + 去重 + 滚动摘要
```

### 作为 MCP server 接入 Kimi（`~/.kimi/mcp.json`）

```json
{
  "mcpServers": {
    "kimiclaw-memory": {
      "command": "kimiclaw-memory-mcp",
      "env": {
        "KIMI_API_KEY": "sk-xxxx",
        "KIMI_BASE_URL": "https://api.moonshot.cn/v1",
        "KIMI_MODEL": "moonshot-v1-8k"
      }
    }
  }
}
```

> 也可用 venv 绝对路径：`"command": "/abs/path/.venv/bin/kimiclaw-memory-mcp"`。

暴露的 MCP 工具：`memory_add`、`memory_search`、`memory_get_all`、`memory_delete`、`memory_compact`。

### 接入 openclaw / QClaw（可移植性）

- **实时检索/记忆**：openclaw 支持 MCP，按其 MCP 配置格式登记同一个 server 即可，**免二次开发**。
- **开机自动加载文件记忆**：openclaw 启动会读 `USER.md`/`SOUL.md`/`MEMORY.md`/每日日志。本框架带可选的遗留文件注入器（`enable_openclaw_inject`，默认关），目前覆盖 `USER.md`/`SOUL.md`；若需写 `MEMORY.md`/每日日志需小幅扩展。

### 规格文档

设计 / 需求 / 任务见 `.kiro/specs/embeddable-memory-framework/`。

---

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

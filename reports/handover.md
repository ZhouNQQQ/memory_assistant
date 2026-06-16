# KimiClaw-Memory 项目交接文档

> 生成时间：2026-06-16
> 交接原因：当前 agent 已完成核心架构（GitHub 同步 + Compaction 引擎 + QClaw 注入），后续工作由新 agent 接手。
> 目标：让新 agent 在 5 分钟内理解项目全貌，并直接开始下一步工作。

---

## 1. 项目背景

KimiClaw（QClaw 的本地变体）需要**自动记忆增强层**——从对话中自动提取记忆、去重、持久化，并在跨会话时自动注入上下文。

**核心约束**：
- 基于 `mem0`（`mem0ai` pip 包）核心架构，不修改其源码，通过继承包装实现定制
- 向量存储用 **Chroma**（本地持久化，无额外服务依赖）
- 记忆存储位置扩展到 **GitHub**（远程持久化 + 跨设备同步）
- 必须加入 **Compaction 组件**（时间衰减 + 去重合并 + 滚动摘要），因为 mem0 原生无自动压缩
- 用户拒绝在记忆 metadata 中存储 `importance`/`entity`/`confidence` 等 LLM 自评字段（可信度存疑）

**已完成的阶段**：
- Phase 0：mem0 源码深度调研（`~/IdeaProjects/mem0-source`）
- Phase A：GitHubSyncManager（队列 + 批量 flush + 冲突重试）
- Phase B：CompactionEngine（时间衰减 + 去重合并 + 滚动摘要）
- Phase C：QClawInjector（USER.md / SOUL.md 增量映射）

---

## 2. 代码结构

```
kimiclaw-memory/
├── src/
│   ├── __init__.py
│   ├── extractor.py              # 现有：LLM 记忆提取器（GLM-4-Flash）
│   ├── evaluate.py               # 现有：评估脚本（Mock vs LLM F1）
│   ├── mock_extractor.py         # 现有：Mock 提取器（关键词匹配）
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── kimi_claw_memory.py   # 核心入口：继承 mem0.Memory，注入所有定制层
│   │   ├── storage/
│   │   │   ├── __init__.py
│   │   │   ├── github_manager.py # GitHubSyncManager + GitHubClient + SyncEvent
│   │   │   └── sqlite_wrapper.py # SQLiteManager 包装器：history 钩子
│   │   ├── compaction/
│   │   │   ├── __init__.py
│   │   │   └── engine.py         # CompactionEngine + 三策略实现
│   │   ├── vector_store/
│   │   │   └── __init__.py       # 占位（暂未实现自定义向量存储）
│   │   └── injector.py           # QClawInjector：USER.md / SOUL.md 注入
├── tests/
│   ├── test_github_sync.py       # 12 个测试，全通过
│   ├── test_compaction.py        # 17 个测试，全通过
│   └── test_injector.py          # 12 个测试，全通过
├── data/                         # 现有：对话数据集 + ground truth
├── reports/                      # 现有：调研报告 + 方案文档
└── README.md
```

---

## 3. 核心文件速查

### 3.1 `kimi_claw_memory.py`（主入口）

```python
from memory.kimi_claw_memory import KimiClawMemory

memory = KimiClawMemory({
    # mem0 原生配置
    "vector_store": {"provider": "chroma", "config": {"collection_name": "kimiclaw", "path": "/tmp/chroma"}},
    "llm": {"provider": "openai", "config": {"model": "glm-4-flash"}},
    "embedder": {"provider": "openai", "config": {"model": "embedding-3"}},
    # 自定义配置
    "github_sync": {"enabled": True, "repo": "user/repo", "token": "ghp_xxx", "sync_interval": 300},
    "compaction": {"enabled": True, "half_life_days": 90, "schedule": "0 2 * * 0"},
})

# 对话前检索
memories = memory.search("用户问题", user_id="user_001")

# 对话后保存
memory.add("用户: ...", user_id="user_001")

# 定时压缩
report = memory.compact(user_id="user_001")
```

**关键设计**：
- 继承 `mem0.Memory`，重写 `__init__` 注入定制层
- 用 `SQLiteManagerWrapper` 包装 `self.db`，在 `add_history`/`batch_add_history` 后触发 `GitHubSyncManager.queue_event()`
- `compaction_engine` 是**懒加载**（避免循环导入），首次调用 `.compact()` 时初始化

### 3.2 `github_manager.py`（GitHub 同步层）

| 类 | 职责 |
|----|------|
| `SyncEvent` | 数据模型：event_type + user_id + payload + timestamp |
| `GitHubClient` | 轻量 GitHub Contents API 封装：get_file / create_file / update_file / delete_file |
| `GitHubSyncManager` | 队列 + 后台线程 + 批量 flush + 冲突重试 |

**GitHub 文件结构**：
```
users/{user_id_hash}/
├── memories/active.json       # 去重后的活跃记忆（id -> payload）
├── memories/archive.json      # Compaction 归档
├── history/{yyyy-mm}.jsonl    # 按月分片的事件日志
├── entities/graph.json        # 实体关联图
└── profile.json               # 滚动摘要
```

**同步策略**：
- 本地优先：所有写操作先完成 SQLite/Chroma，再异步同步到 GitHub
- 批量缓冲：达 `batch_size`（默认 20）或 `sync_interval`（默认 300s）时 flush
- 冲突处理：`update_file` 需 `sha` 防冲突，409 时重试一次（重新读取 + 合并）
- 失败事件放回队列，最多重试 `max_retries`（默认 3）后丢弃

### 3.3 `engine.py`（Compaction 引擎）

| 策略 | 类 | 关键参数 |
|------|------|---------|
| 时间衰减 | `TimeDecayStrategy` | `half_life_days=90`, `archive_threshold=0.3` |
| 去重合并 | `DeduplicationStrategy` | `similarity_threshold=0.92` |
| 滚动摘要 | `SummarizationStrategy` | `max_memories=100`, `max_summary_length=500` |

**Compaction 主流程**（`CompactionEngine.compact()`）：
1. 获取所有记忆（`vector_store.list()`）
2. 创建快照（SQLite 备份 + GitHub tag）
3. 时间衰减 → 分离 active / archive
4. 去重合并（active 中 cosine 相似度 > 0.92 的合并）
5. 更新衰减分数到向量存储 metadata
6. LLM 生成滚动摘要 → 推送到 GitHub profile.json
7. 归档记忆推送到 GitHub archive.json
8. 清理 SQLite 历史表（保留最近 `keep_history` 条）

### 3.4 `injector.py`（QClaw 注入层）

| 方法 | 行为 |
|------|------|
| `inject_memories(memories, user_id)` | 将记忆注入 `USER.md`：结构化字段填充 + Context 追加 |
| `update_soul(preference_memories)` | 将偏好更新到 `SOUL.md` 的 `## Preferences` 段落 |

**分类映射**（`memory.category` → `USER.md` 字段）：
- `personal` → `name`, `what_to_call_them`, `pronouns`, `timezone`
- `preference` → `notes`, `context`
- `professional` / `plan` / `activity` / `health` / `misc` → `context`

**安全边界**：`name` / `what_to_call_them` / `pronouns` 已有值时**不自动覆盖**，需要用户确认。

---

## 4. 技术决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 向量存储 | Chroma | 本地持久化，无额外服务，适合单机 KimiClaw |
| 存储后端 | GitHub（分层混合） | 向量保留本地（GitHub 无实时检索能力），文本/历史/摘要远程同步 |
| mem0 改造方式 | 继承 + 包装（不修改源码） | 便于跟进 mem0 版本更新，可降级为纯本地模式 |
| 同步策略 | 异步批量 + 队列缓冲 | 不阻塞用户响应，减少 GitHub API 调用频率 |
| Compaction 触发 | 手动 + 定时（可接入 cron） | 避免高频 compaction 影响性能 |
| metadata 策略 | 不存储 LLM 自评字段 | 用户明确要求：不加入 `importance`/`entity`/`confidence` |
| 冲突解决 | last-write-wins + 时间戳 | 简单可靠，适合单用户场景 |

---

## 5. 测试状态

| 测试文件 | 测试数 | 状态 | 覆盖内容 |
|----------|--------|------|---------|
| `test_github_sync.py` | 12 | ✅ 全通过 | GitHubClient API、SyncManager 队列/分组/flush/冲突/重试/丢弃 |
| `test_compaction.py` | 17 | ✅ 全通过 | TimeDecayStrategy、DeduplicationStrategy、SummarizationStrategy、CompactionEngine 主流程 |
| `test_injector.py` | 12 | ✅ 全通过 | UserMdParser 解析/序列化、字段注入、Context 追加、SOUL.md 更新 |

运行命令：
```bash
cd /Users/zhounanqiao12867/Documents/技术文档/AICoding/kimiclaw-memory
python3 tests/test_github_sync.py
python3 tests/test_compaction.py
python3 tests/test_injector.py
```

---

## 6. 剩余任务（新 agent 的 TODO）

### 高优先级

- [ ] **端到端集成测试**：构造一个完整对话流水线（search → LLM → add → inject → compact），验证全链路工作
- [ ] **真实 GitHub 联调**：用真实 GitHub token 创建仓库，做一次完整的 add + sync + pull 验证
- [ ] **接入 KimiClaw 调度器**：在 `extractor.py` 的 `extract()` 之后，调用 `memory.add()` + `injector.inject_memories()`

### 中优先级

- [ ] **定时触发器**：在 `kimi_claw_memory.py` 中加入 `BackgroundScheduler`（APScheduler）或 cron，自动执行 compaction
- [ ] **增量同步优化**：GitHub 目前是全量重写文件（Contents API 不支持追加），当文件变大时（>1000条）需要改为分片策略
- [ ] **加密敏感字段**：GitHub 仓库可能是私有，但再加一层字段级加密（AES + 环境变量密钥）
- [ ] **启动恢复**：从 GitHub pull 记忆后，如何写入本地 Chroma（需要重新生成 embeddings）

### 低优先级

- [ ] **监控与告警**：同步失败、compaction 异常的日志 + 告警机制
- [ ] **多用户隔离**：当前 user_id 已作为过滤维度，但多用户场景下需验证隔离性
- [ ] **前端/CLI**：提供一个简单的 CLI 命令查看记忆统计、手动触发 compaction

---

## 7. 环境依赖

```bash
# 系统 Python 3.9+（当前环境是 3.9.7）
# 已安装的核心包
pip install numpy        # Compaction 引擎的相似度计算
pip install requests     # GitHubSyncManager 的 HTTP 调用

# 需要安装（但未在当前环境安装）
pip install mem0ai       # mem0 核心包（Python ≥3.10，当前系统 3.9.7 → 用 uv 建 3.11 venv）
pip install chromadb     # 向量存储后端
pip install openai       # LLM + Embedding 调用（兼容 Kimi/GLM）
```

**注意**：当前 `mem0ai` 未安装在当前 Python 3.9 环境中。项目 README 提到用 `uv` 建 3.11 venv。开发时可能需要：
```bash
cd /Users/zhounanqiao12867/Documents/技术文档/AICoding/kimiclaw-memory
uv venv --python 3.11
source .venv/bin/activate
uv pip install mem0ai chromadb openai numpy requests
```

---

## 8. 关键文件路径（工作区）

| 路径 | 说明 |
|------|------|
| `/Users/zhounanqiao12867/Documents/技术文档/AICoding/kimiclaw-memory` | 项目根目录 |
| `/Users/zhounanqiao12867/IdeaProjects/mem0-source` | mem0 源码（调研用） |
| `~/.qclaw/workspace/USER.md` | QClaw 用户记忆文件（注入目标） |
| `~/.qclaw/workspace/SOUL.md` | QClaw Agent 性格文件（注入目标） |
| `~/.mem0/config.json` | mem0 原生配置（自动创建） |
| `~/.mem0/history.db` | mem0 原生 SQLite（被包装） |

---

## 9. 新 Agent 的 first step

建议新 agent 先运行以下步骤验证环境：

```bash
# 1. 运行所有测试（确认当前 agent 的代码完好）
cd /Users/zhounanqiao12867/Documents/技术文档/AICoding/kimiclaw-memory
python3 tests/test_github_sync.py
python3 tests/test_compaction.py
python3 tests/test_injector.py

# 2. 检查 mem0ai 是否可安装（如果不能，说明 Python 版本问题）
python3 -c "import mem0; print(mem0.__file__)"

# 3. 阅读核心文件（按顺序）
#   → src/memory/kimi_claw_memory.py（理解主入口）
#   → src/memory/storage/github_manager.py（理解同步层）
#   → src/memory/compaction/engine.py（理解 Compaction）
#   → src/memory/injector.py（理解注入层）

# 4. 选择剩余任务中的高优先级项开始实施
```

---

## 10. 交接确认

当前 agent 已确认：
- ✅ 所有 4 个 Phase 的核心代码已编写并保存到工作区
- ✅ 41 个测试全部通过（12 + 17 + 12）
- ✅ 没有未保存的修改（所有文件已写入磁盘）
- ✅ 没有正在运行的后台进程（`GitHubSyncManager` 线程只在 `start()` 后运行）
- ✅ 没有遗留的 TODO 或临时文件

**如果新 agent 在接手时遇到任何问题（测试失败、文件缺失、理解障碍），请直接阅读对应的源文件，代码中有详细的 docstring 和注释。**

---

*交接文档由前 agent 生成，供新 agent 快速接手 KimiClaw-Memory 项目。*

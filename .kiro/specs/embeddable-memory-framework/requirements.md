# Requirements Document

## Introduction

本功能把现有基于 mem0 的记忆系统（`KimiClawMemory` + GitHub 同步 + Compaction + 旧 QClaw 注入器）封装成一个**可复用、易嵌入的记忆框架**，对外提供两个集成面：可 `pip install` 的 Python 包（门面 API），以及作为主集成方式的 MCP server。主目标接入对象是 Kimi.app（Daimon 运行时），并与其原生记忆**共存、互不侵入**；同时通过 MCP 保持对 openclaw/QClaw 等运行时的可移植性。本需求文档由已批准的设计文档反推得出，需求编号供设计中的正确性属性引用（`Validates: Requirements X.Y`）。

## Requirements

### Requirement 1: Python 包门面 API

**User Story:** 作为接入方开发者，我希望通过一个干净稳定的 Python 门面类使用记忆能力，以便在任意 Python agent 中一两行代码完成记忆的增删查与压缩。

#### Acceptance Criteria

1. THE 框架 SHALL 提供一个可导入的门面类 `kimiclaw_memory.Memory`，对外暴露 `add` / `search` / `get_all` / `delete` / `compact` / `close` 方法。
2. WHEN 调用 `Memory.from_env()` THEN 框架 SHALL 从环境变量/`.env`/可选 yaml 合并出配置并返回一个就绪的门面实例。
3. WHEN 调用 `add` / `search` / `get_all` / `delete` 而未提供非空 `user_id` THEN 框架 SHALL 抛出 `ValueError` 且不写入任何记忆。
4. THE 门面 SHALL 支持上下文管理器协议（`__enter__` / `__exit__`），并在退出时调用 `close()`。
5. WHEN 门面方法返回记忆记录 THEN 返回的 `metadata` SHALL NOT 包含 `importance`、`entity` 或 `confidence` 字段。

### Requirement 2: MCP Server 集成面

**User Story:** 作为使用 Kimi/Daimon 或其它 MCP 客户端的用户，我希望以 MCP 工具的形式调用记忆能力，以便 agent 在对话中实时检索和写入长期记忆而无需改动框架代码。

#### Acceptance Criteria

1. THE 框架 SHALL 提供一个 MCP server，暴露工具 `memory_add`、`memory_search`、`memory_get_all`、`memory_delete`、`memory_compact`。
2. THE MCP server SHALL 以 stdio 传输运行，并提供一个 console-script 入口 `kimiclaw-memory-mcp`。
3. WHEN MCP server 进程启动 THEN 它 SHALL 通过 `Memory.from_env()` 构造一个进程级单例门面，并将每个工具调用转发给门面对应方法。
4. WHEN 任一 MCP 工具内部抛出异常 THEN server SHALL 返回 `{"ok": false, "error": <message>, "code": <CONFIG|NOT_FOUND|BACKEND>}` 且 SHALL NOT 向客户端泄露异常堆栈。
5. WHEN MCP 工具成功执行 THEN server SHALL 返回 `{"ok": true, "data": <结果>}`。
6. WHEN MCP server 进程退出 THEN 它 SHALL 调用 `memory.close()` 以释放资源。

### Requirement 3: 配置加载与密钥管理

**User Story:** 作为部署者，我希望用环境变量/`.env`/可选 yaml 配置框架，以便无需改代码即可切换 LLM、向量库路径与 GitHub 同步，并确保密钥不被持久化。

#### Acceptance Criteria

1. THE 配置加载 SHALL 按"默认值 < yaml 文件（可选）< 环境变量/`.env`"的优先级三级合并。
2. THE 配置加载 SHALL 从 `KIMI_API_KEY`（兜底 `ZHIPU_API_KEY`）、`KIMI_BASE_URL`/`ZHIPU_BASE_URL`、`KIMI_MODEL`、`GITHUB_TOKEN` 读取相应配置。
3. IF 既配置了 GitHub `repo` 又提供了 `GITHUB_TOKEN` THEN 框架 SHALL 启用 GitHub 同步，ELSE 框架 SHALL 以纯本地模式运行且不报错。
4. IF LLM 密钥（`KIMI_API_KEY` 与 `ZHIPU_API_KEY`）均缺失 THEN 配置加载 SHALL 抛出 `ConfigError`。
5. THE 框架 SHALL NOT 将任何密钥写入向量库、SQLite 或同步到 GitHub 的文件。

### Requirement 4: 打包与分发

**User Story:** 作为接入方，我希望框架是一个标准可安装的 Python 包，以便用 `pip`/`uv` 安装并直接 import，而不依赖 `sys.path` 手工拼接。

#### Acceptance Criteria

1. THE 项目 SHALL 提供 `pyproject.toml`，声明包名 `kimiclaw-memory`、import 命名空间 `kimiclaw_memory`、`requires-python >= 3.10` 及运行时依赖。
2. THE `pyproject.toml` SHALL 注册 console-script `kimiclaw-memory-mcp` 指向 MCP server 入口。
3. WHEN 在 Python ≥ 3.10 环境执行 `pip install`（或 `uv pip install -e .`）后 THEN `from kimiclaw_memory import Memory` SHALL 可成功导入且不依赖 `sys.path.insert`。

### Requirement 5: 复用既有核心、不重构

**User Story:** 作为维护者，我希望新功能只叠加外壳层而不改动既有核心，以便降低回归风险并保持现有测试有效。

#### Acceptance Criteria

1. THE 框架 SHALL 复用既有 `KimiClawMemory`、`GitHubSyncManager`、`CompactionEngine`，且 SHALL NOT 修改其对外行为契约。
2. THE 门面层与 MCP 层 SHALL 作为既有核心之上的新增封装实现，MCP 层 SHALL 通过门面层间接调用核心（不直接绕过门面调用核心）。
3. WHEN 既有单元测试（`test_github_sync.py`、`test_compaction.py`、`test_injector.py`）在本功能完成后运行 THEN 它们 SHALL 全部通过。

### Requirement 6: 与 Daimon 原生记忆共存隔离

**User Story:** 作为 Kimi 用户，我希望本框架作为额外长期记忆源与 Daimon 原生记忆共存，以便不破坏 Kimi 自带的记忆体验。

#### Acceptance Criteria

1. THE 框架 SHALL NOT 读取或写入 Daimon 的数据目录（`kimi-desktop/.../daimon/` 下的 `vault/`、`transcripts/`、`sections/`、`entities/` 等）。
2. THE 框架 SHALL 将自身数据（Chroma、SQLite）默认存放于独立目录 `~/.kimiclaw_memory/`。
3. WHEN 以 `user_id=A` 调用 `search` THEN 结果 SHALL NOT 包含任何 `user_id=B` 的记忆。
4. THE 框架 SHALL 仅在被 agent 主动调用 MCP 工具时才执行记忆操作，SHALL NOT 主动 hook 或拦截 Daimon 内部流程。

### Requirement 7: 记忆压缩能力暴露

**User Story:** 作为用户，我希望能触发记忆压缩，以便控制记忆库规模并保持检索质量。

#### Acceptance Criteria

1. WHEN 调用门面 `compact` 或工具 `memory_compact` THEN 框架 SHALL 委派既有 `CompactionEngine` 执行时间衰减归档、去重合并与滚动摘要。
2. WHEN 调用 `compact(dry_run=true)` THEN 框架 SHALL 仅返回压缩报告而 SHALL NOT 实际修改记忆库。
3. WHEN compaction 完成且启用了 GitHub 同步 THEN 框架 SHALL 通过既有同步队列将 profile/archive 事件入队。

### Requirement 8: 优雅关闭与生命周期

**User Story:** 作为运行环境，我希望框架能干净地启动与关闭，以便不残留后台线程或丢失待同步数据。

#### Acceptance Criteria

1. WHEN 调用 `close()` THEN 框架 SHALL 停止 GitHub 同步后台线程并 flush 待处理队列。
2. WHEN `close()` 返回后 THEN GitHub 同步后台线程 SHALL NOT 仍处于存活状态。

### Requirement 9: openclaw / QClaw 可移植性

**User Story:** 作为后续可能接入 openclaw/QClaw 的用户，我希望同一框架能以最小成本接入这些运行时，以便复用而非二次开发。

#### Acceptance Criteria

1. THE MCP server SHALL 作为跨运行时的统一集成契约，使支持 MCP 的 openclaw/QClaw 仅通过登记 MCP 配置即可调用记忆工具，无需改动框架代码。
2. THE 旧 `QClawInjector`（写 USER.md/SOUL.md 的文件注入）SHALL 作为可选/遗留适配器保留，默认关闭（门面开关 `enable_openclaw_inject` 默认为 false）。
3. WHEN `enable_openclaw_inject` 为 false THEN `add` SHALL NOT 写入任何 openclaw 工作区文件。

## Glossary

- **门面 / Facade（A）**：对外稳定入口类 `kimiclaw_memory.Memory`，封装核心生命周期与组合操作。
- **MCP server（C3）**：以 stdio 暴露记忆工具的独立进程，供 MCP 客户端（Kimi/Daimon、openclaw 等）调用。
- **核心四层 / 既有核心**：`KimiClawMemory`、`GitHubSyncManager`、`CompactionEngine`、`QClawInjector`（遗留），本功能直接复用。
- **Daimon**：Kimi.app 的 agent 运行时（`kimi-code` 内核），自带原生记忆系统（vault/transcripts/dream）。
- **Compaction**：记忆压缩，含时间衰减归档、去重合并、滚动摘要三策略。
- **user_id**：记忆归属与隔离维度，所有公开操作必填。
- **被禁止 metadata**：`importance` / `entity` / `confidence`，用户明确拒绝存储的字段。

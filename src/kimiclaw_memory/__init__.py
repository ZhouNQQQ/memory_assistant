"""kimiclaw_memory —— 可嵌入的 mem0 记忆框架。

对外提供两个集成面：
- 门面 API：`from kimiclaw_memory import Memory`
- MCP server：控制台入口 `kimiclaw-memory-mcp`（见 `kimiclaw_memory.mcp_server`）

实现策略：在既有核心四层（`memory.*` 包，本次不改动）之上叠加门面层与
MCP 层。本模块采用惰性导入（PEP 562 `__getattr__`），使得仅 `import
kimiclaw_memory` 不会立即拉起 mem0/chromadb 等重依赖，便于轻量使用与测试。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "Memory",
    "MemoryConfig",
    "ConfigError",
    "load_config",
    "to_core_dict",
    # 既有核心（重导出，便于高级用法）
    "KimiClawMemory",
    "GitHubSyncManager",
    "CompactionEngine",
    "QClawInjector",
]


def __getattr__(name: str):  # noqa: D401  (PEP 562 惰性导入)
    # 门面层（A）
    if name == "Memory":
        from .facade import Memory

        return Memory
    # 配置层
    if name in {"MemoryConfig", "ConfigError", "load_config", "to_core_dict"}:
        from . import config

        return getattr(config, name)
    # 既有核心层（来自不改动的 memory 包，重导出）
    if name == "KimiClawMemory":
        from memory.kimi_claw_memory import KimiClawMemory

        return KimiClawMemory
    if name == "GitHubSyncManager":
        from memory.storage.github_manager import GitHubSyncManager

        return GitHubSyncManager
    if name == "CompactionEngine":
        from memory.compaction.engine import CompactionEngine

        return CompactionEngine
    if name == "QClawInjector":
        from memory.injector import QClawInjector

        return QClawInjector
    raise AttributeError(f"module 'kimiclaw_memory' has no attribute {name!r}")


def __dir__():
    return sorted(__all__)

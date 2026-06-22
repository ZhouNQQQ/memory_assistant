# src/memory/kimi_claw_memory.py
"""
KimiClawMemory — 基于 mem0.Memory 的包装层。

设计原则：
- 不修改 mem0 源码，全部通过继承 + 方法包装实现
- 向量存储使用 Chroma（本地持久化）
- 历史记录保留 SQLite（本地缓存），同时通过钩子同步到 GitHub
- 支持自定义 Compaction（时间衰减、去重合并、滚动摘要）
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mem0.configs.base import MemoryConfig
from mem0.memory.main import Memory

from .storage.sqlite_wrapper import SQLiteManagerWrapper
from .storage.github_manager import GitHubSyncManager, make_history_event, make_memory_event

logger = logging.getLogger(__name__)


class KimiClawMemory(Memory):
    """
    KimiClaw 自定义记忆系统。

    继承 mem0.Memory，增加：
    1. SQLiteManager 包装 → 写入本地后触发 GitHub 同步
    2. Compaction 引擎 → 自动/手动压缩记忆（懒加载，按需导入）
    3. QClaw USER.md 注入钩子（Phase 4 接入）
    """

    def __init__(self, config: dict):
        """
        Args:
            config: 完整配置 dict，包含 mem0 原生配置 + 自定义配置。
                    示例见 config/memory.yaml
        """
        # 1. 提取自定义配置（在传给 mem0 之前 pop 掉）
        self._github_config = config.pop("github_sync", None)
        self._compaction_config = config.pop("compaction", None)
        self._qclaw_config = config.pop("qclaw", None)

        # 2. 构建 mem0 原生 MemoryConfig
        mem0_config = MemoryConfig(**config) if config else MemoryConfig()

        # 3. 调用父类 __init__（这会创建 self.db = SQLiteManager(...)）
        super().__init__(mem0_config)

        # 4. 用包装器替换 self.db，注入同步钩子
        self._wrap_sqlite_manager()

        # 5. 初始化 GitHub 同步层
        self.sync_manager: Optional[GitHubSyncManager] = None
        if self._github_config and self._github_config.get("enabled"):
            self.sync_manager = GitHubSyncManager(
                repo=self._github_config["repo"],
                token=self._github_config["token"],
                branch=self._github_config.get("branch", "main"),
                sync_interval=self._github_config.get("sync_interval", 300),
                batch_size=self._github_config.get("batch_size", 20),
            )
            self.sync_manager.start()
            logger.info(f"GitHub sync enabled: {self._github_config['repo']}")

        # 6. Compaction 引擎懒加载（避免循环导入）
        self._compaction_engine = None

    # ------------------------------------------------------------------
    # 内部：包装 SQLiteManager，注入同步钩子
    # ------------------------------------------------------------------
    def _wrap_sqlite_manager(self):
        """将 self.db 替换为 SQLiteManagerWrapper，在写入后触发同步钩子。"""
        original = self.db
        wrapper = SQLiteManagerWrapper(original)

        def _on_history_event(record: dict):
            """每次 SQLite 写入 history 后，将事件加入 GitHub 同步队列。"""
            if self.sync_manager:
                # 从 record 中解析 user_id（如果 record 中带了）
                user_id = record.get("user_id", "default")
                event = make_history_event(
                    user_id=user_id,
                    memory_id=record.get("memory_id", ""),
                    event=record.get("event", "ADD"),
                    old_memory=record.get("old_memory"),
                    new_memory=record.get("new_memory"),
                    created_at=record.get("created_at"),
                    updated_at=record.get("updated_at"),
                    is_deleted=record.get("is_deleted", 0),
                    actor_id=record.get("actor_id"),
                    role=record.get("role"),
                )
                self.sync_manager.queue_event(event)

        wrapper.install_history_hook(_on_history_event)
        self.db = wrapper

    # ------------------------------------------------------------------
    # Compaction 引擎（懒加载）
    # ------------------------------------------------------------------
    @property
    def compaction_engine(self):
        """懒加载 CompactionEngine，避免循环导入。"""
        if self._compaction_engine is None and self._compaction_config:
            from .compaction.engine import CompactionEngine
            self._compaction_engine = CompactionEngine(
                memory_instance=self,
                config=self._compaction_config,
                github_sync=self.sync_manager,
            )
        return self._compaction_engine

    # ------------------------------------------------------------------
    # 公共 API：保持与 mem0.Memory 一致，增加自定义功能
    # ------------------------------------------------------------------
    def add(self, messages, *, user_id: Optional[str] = None,
            agent_id: Optional[str] = None, run_id: Optional[str] = None,
            **kwargs) -> dict:
        """
        添加记忆。调用 mem0 原生 add，history 事件通过钩子自动同步到 GitHub。

        Args:
            messages: str / list[dict] — 对话内容
            user_id: 用户标识（必须提供，用于 GitHub 分片）
        """
        if not user_id:
            raise ValueError("KimiClawMemory.add() 要求必须提供 user_id")

        result = super().add(messages, user_id=user_id, agent_id=agent_id,
                             run_id=run_id, **kwargs)
        logger.info(f"Added {len(result.get('results', []))} memories for user={user_id}")
        return result

    def search(self, query: str, user_id: str, **kwargs) -> List[dict]:
        """
        检索记忆。在调用 LLM 前使用，将返回的记忆注入 prompt 上下文。

        Args:
            query: 查询文本（通常是用户当前问题）
            user_id: 用户标识

        说明：当前 mem0 版本要求会话标识经 ``filters`` 传入，
        不接受顶层 ``user_id`` kwarg，故在此转换为 ``filters``。
        """
        filters = kwargs.pop("filters", None) or {}
        filters.setdefault("user_id", user_id)
        results = super().search(query, filters=filters, **kwargs)
        try:
            _n = len(results.get("results", results)) if isinstance(results, dict) else len(results)
        except Exception:
            _n = -1
        logger.debug(f"Search returned {_n} memories for user={user_id}")
        return results

    def compact(self, user_id: Optional[str] = None, dry_run: bool = False) -> dict:
        """
        手动触发 Compaction。一般在后台定时任务中自动调用。

        Args:
            user_id: 指定用户（None 则对所有用户）
            dry_run: 只生成报告，不实际执行
        """
        if not self.compaction_engine:
            raise RuntimeError("Compaction 未启用，请在配置中开启 compaction.enabled")

        logger.info(f"Starting compaction for user={user_id or 'ALL'}")
        report = self.compaction_engine.compact(user_id=user_id, dry_run=dry_run)
        logger.info(f"Compaction done: {report}")
        return report

    def close(self):
        """优雅关闭：停止同步线程、关闭数据库连接。"""
        if self.sync_manager:
            self.sync_manager.stop()
        if hasattr(self.db, 'close'):
            self.db.close()

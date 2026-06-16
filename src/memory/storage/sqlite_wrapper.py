"""
SQLiteManager 包装器：在原始 SQLiteManager 的方法前后插入钩子。

关键设计：
- 保持原始 SQLiteManager 的所有接口不变
- 只在 add_history / batch_add_history 后触发回调
- 不拦截 save_messages（消息缓存无需同步到 GitHub）
"""

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


class SQLiteManagerWrapper:
    """
    透明包装 mem0.memory.storage.SQLiteManager。

    将原始实例的所有方法委托给它，只在写入 history 时插入钩子。
    """

    def __init__(self, original_instance):
        self._original = original_instance
        self._history_hook: Callable[[dict], None] = None

    def install_history_hook(self, hook: Callable[[dict], None]):
        """安装 history 写入钩子。hook(record_dict) 会在每次写入后调用。"""
        self._history_hook = hook

    # ------------------------------------------------------------------
    # 委托所有属性访问到原始实例
    # ------------------------------------------------------------------
    def __getattr__(self, name):
        """将未在此类定义的方法/属性委托给原始 SQLiteManager。"""
        return getattr(self._original, name)

    # ------------------------------------------------------------------
    # 包装方法：add_history
    # ------------------------------------------------------------------
    def add_history(self, memory_id: str, old_memory, new_memory, event: str,
                    *, created_at=None, updated_at=None, is_deleted=0,
                    actor_id=None, role=None):
        # 1. 先调用原始方法
        result = self._original.add_history(
            memory_id, old_memory, new_memory, event,
            created_at=created_at, updated_at=updated_at,
            is_deleted=is_deleted, actor_id=actor_id, role=role,
        )

        # 2. 触发钩子
        if self._history_hook:
            try:
                self._history_hook({
                    "memory_id": memory_id,
                    "old_memory": old_memory,
                    "new_memory": new_memory,
                    "event": event,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "is_deleted": is_deleted,
                    "actor_id": actor_id,
                    "role": role,
                })
            except Exception as e:
                logger.warning(f"History hook failed: {e}")

        return result

    # ------------------------------------------------------------------
    # 包装方法：batch_add_history
    # ------------------------------------------------------------------
    def batch_add_history(self, records: List[Dict[str, Any]]):
        # 1. 先调用原始方法
        result = self._original.batch_add_history(records)

        # 2. 为每条记录触发钩子
        if self._history_hook:
            for record in records:
                try:
                    self._history_hook(record)
                except Exception as e:
                    logger.warning(f"History hook failed for record: {e}")

        return result

    # ------------------------------------------------------------------
    # 以下方法只代理，不触发钩子
    # ------------------------------------------------------------------
    def save_messages(self, messages, session_scope: str):
        return self._original.save_messages(messages, session_scope)

    def get_last_messages(self, session_scope: str, limit: int = 10):
        return self._original.get_last_messages(session_scope, limit)

    def reset(self):
        return self._original.reset()

    def close(self):
        return self._original.close()

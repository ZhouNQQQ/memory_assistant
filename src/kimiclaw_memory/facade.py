"""门面 API 层（A）。

对外唯一稳定入口 `kimiclaw_memory.Memory`，封装既有核心 `KimiClawMemory`
的生命周期与组合操作，屏蔽底层细节。两个集成面（Python 包 / MCP server）
都通过本门面访问核心，保证行为一致、只维护一套业务逻辑。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from .config import MemoryConfig, load_config, to_core_dict

logger = logging.getLogger(__name__)

# 用户明确拒绝存储/外泄的元数据字段
_FORBIDDEN_METADATA_KEYS = ("importance", "entity", "confidence")


def _as_list(result: Any) -> List[dict]:
    """把 mem0 的返回（可能是 {"results": [...]} 或直接 list）归一化为 list。"""
    if isinstance(result, dict) and "results" in result:
        return result.get("results") or []
    if isinstance(result, list):
        return result
    return []


def _strip_forbidden(records: List[dict]) -> List[dict]:
    """归一化字段并剥离被禁止的 metadata（Design Property 2）。

    - 字段归一化：当前 mem0 版本用 ``memory`` 作为记忆文本字段，统一补一份
      ``data``，兼容设计中的 MemoryRecord 与遗留注入器（读 ``data``）。
    - 剥离 ``importance`` / ``entity`` / ``confidence``。
    """
    cleaned: List[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            cleaned.append(rec)
            continue
        rec = dict(rec)
        if "data" not in rec and "memory" in rec:
            rec["data"] = rec["memory"]
        if isinstance(rec.get("metadata"), dict):
            rec["metadata"] = {
                k: v for k, v in rec["metadata"].items() if k not in _FORBIDDEN_METADATA_KEYS
            }
        cleaned.append(rec)
    return cleaned


def _require_user_id(user_id: Optional[str]) -> None:
    if not user_id or not str(user_id).strip():
        raise ValueError("user_id 必填且不能为空")


class Memory:
    """可嵌入记忆框架的统一门面。

    用法::

        from kimiclaw_memory import Memory
        with Memory.from_env() as mem:
            mem.add("我叫 Alice，喜欢吃火锅", user_id="u1")
            hits = mem.search("用户喜欢什么", user_id="u1")
    """

    def __init__(self, config: MemoryConfig) -> None:
        from memory.kimi_claw_memory import KimiClawMemory  # 延迟导入重依赖

        self._config = config
        self._core = KimiClawMemory(to_core_dict(config))

        # 可选：openclaw 遗留文件注入器（默认关闭）
        self._injector = None
        if config.enable_openclaw_inject:
            from memory.injector import QClawInjector

            self._injector = QClawInjector(config.qclaw_workspace_dir or None)

    # ---- 构造 ----
    @classmethod
    def from_env(cls, yaml_path: Optional[str] = None) -> "Memory":
        """从环境变量 / .env / 可选 yaml 合并出配置并构造门面。"""
        return cls(load_config(yaml_path))

    # ---- 核心操作 ----
    def add(
        self,
        messages: Union[str, List[dict]],
        *,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        auto_inject: bool = False,
    ) -> Dict[str, Any]:
        """新增记忆。

        Args:
            auto_inject: 仅当启用了 openclaw 适配器时生效——额外把结果注入
                USER.md/SOUL.md（遗留路径，默认关闭）。
        """
        _require_user_id(user_id)
        result = self._core.add(messages, user_id=user_id, agent_id=agent_id, run_id=run_id)
        records = _strip_forbidden(_as_list(result))

        if auto_inject and self._injector is not None:
            try:
                self._injector.inject_memories(records, user_id=user_id)
            except Exception:  # 注入失败不应影响主流程
                logger.exception("openclaw 注入失败（忽略，不影响记忆写入）")

        return {"added": len(records), "results": records}

    def search(self, query: str, *, user_id: str, limit: int = 5) -> List[dict]:
        """按语义检索某用户的记忆。"""
        _require_user_id(user_id)
        result = self._core.search(query, user_id=user_id, top_k=limit)
        return _strip_forbidden(_as_list(result))

    def get_all(self, *, user_id: str, limit: int = 100) -> List[dict]:
        """列出某用户的全部记忆。"""
        _require_user_id(user_id)
        # 当前 mem0 版本要求经 filters 传入会话标识
        result = self._core.get_all(filters={"user_id": user_id}, top_k=limit)
        return _strip_forbidden(_as_list(result))

    def delete(self, memory_id: str, *, user_id: str) -> Dict[str, Any]:
        """按 id 删除一条记忆（user_id 用于校验与一致性）。"""
        _require_user_id(user_id)
        self._core.delete(memory_id)
        return {"deleted": memory_id}

    def compact(self, *, user_id: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
        """触发压缩（时间衰减归档 + 去重合并 + 滚动摘要）。"""
        return self._core.compact(user_id=user_id, dry_run=dry_run)

    # ---- 生命周期 ----
    def close(self) -> None:
        """优雅关闭：停止 GitHub 同步线程并关闭底层资源。"""
        try:
            self._core.close()
        except Exception:
            logger.exception("关闭核心时出错")

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

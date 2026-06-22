"""MCP server 层（C3）—— 主集成方式。

把门面 API 暴露成 MCP 工具，供支持 MCP 的 agent（Kimi/Daimon 及任何 MCP
客户端）通过 stdio 调用。设计要点：
- 进程级单例门面（首个工具调用或 main() 时构造）。
- 所有工具经 `_dispatch` 统一包装：成功 `{ok:true,data}`，失败
  `{ok:false,error,code}`，不向客户端泄露异常堆栈（Design Property 6）。
- 仅 stdio 传输，随宿主子进程生命周期；退出时关闭门面。
"""

from __future__ import annotations

import atexit
import logging
from typing import Any, Callable, Dict, Optional

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

app = FastMCP("kimiclaw-memory")

# 进程级单例门面
_memory: Optional[Any] = None


def get_memory():
    """惰性构造并返回进程级门面单例。"""
    global _memory
    if _memory is None:
        from .facade import Memory

        _memory = Memory.from_env()
        atexit.register(_close_memory)
    return _memory


def set_memory(mem: Any) -> None:
    """测试钩子：注入一个门面替身，绕过 from_env。"""
    global _memory
    _memory = mem


def _close_memory() -> None:
    global _memory
    if _memory is not None:
        try:
            _memory.close()
        finally:
            _memory = None


def _dispatch(method: str, **kwargs: Any) -> Dict[str, Any]:
    """统一执行 + 错误包装。绝不向客户端泄露堆栈。

    门面获取（`get_memory()`，可能因缺少密钥抛 ConfigError）也在 try 内进行，
    确保构造期错误同样被包装为 `{ok:false, code}` 而非裸异常。
    """
    try:
        mem = get_memory()
        fn = getattr(mem, method)
        return {"ok": True, "data": fn(**kwargs)}
    except Exception as exc:  # noqa: BLE001  统一兜底
        code = _classify(exc)
        if code == "BACKEND":
            logger.exception("MCP 工具执行失败")
        return {"ok": False, "error": str(exc), "code": code}


def _classify(exc: Exception) -> str:
    # 配置类错误
    try:
        from .config import ConfigError

        if isinstance(exc, ConfigError):
            return "CONFIG"
    except Exception:
        pass
    if isinstance(exc, KeyError):
        return "NOT_FOUND"
    return "BACKEND"


# ──────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────
@app.tool()
def memory_add(messages: str, user_id: str) -> Dict[str, Any]:
    """把一段对话/事实写入长期记忆。messages 为纯文本或 JSON 消息数组字符串。"""
    return _dispatch("add", messages=messages, user_id=user_id)


@app.tool()
def memory_search(query: str, user_id: str, limit: int = 5) -> Dict[str, Any]:
    """按语义检索某用户的长期记忆，返回最相关的若干条。"""
    return _dispatch("search", query=query, user_id=user_id, limit=limit)


@app.tool()
def memory_get_all(user_id: str, limit: int = 100) -> Dict[str, Any]:
    """列出某用户的全部记忆（分页上限 limit）。"""
    return _dispatch("get_all", user_id=user_id, limit=limit)


@app.tool()
def memory_delete(memory_id: str, user_id: str) -> Dict[str, Any]:
    """按记忆 id 删除一条记忆。"""
    return _dispatch("delete", memory_id=memory_id, user_id=user_id)


@app.tool()
def memory_compact(user_id: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """触发压缩（时间衰减归档 + 去重合并 + 滚动摘要）。"""
    return _dispatch("compact", user_id=user_id, dry_run=dry_run)


def main() -> None:
    """控制台入口：以 stdio 运行 MCP server。

    门面采用惰性构造（首个工具调用时才 `Memory.from_env()`），因此即使尚未
    配置密钥，server 也能正常启动并通告工具列表；缺失配置会在具体工具调用时
    以 `{ok:false, code:"CONFIG"}` 形式返回，而不是让进程启动即崩溃。
    """
    logging.basicConfig(level=logging.INFO)
    app.run(transport="stdio")


if __name__ == "__main__":
    main()

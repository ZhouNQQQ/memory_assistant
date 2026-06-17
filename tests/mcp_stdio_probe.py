"""真实 MCP stdio 客户端探针（手动运行，非单测）。

拉起 kimiclaw-memory MCP server 子进程，走真实 MCP 协议：
initialize → tools/list → 调用一个工具。

用法：
    # 不带 key：验证协议链路 + 配置错误被正确包装
    .venv/bin/python tests/mcp_stdio_probe.py

    # 带 GLM key：验证真实 add/search 经 MCP 跑通
    ZHIPU_API_KEY=... EMBEDDING_MODEL=embedding-3 \\
        .venv/bin/python tests/mcp_stdio_probe.py --live
"""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main(live: bool) -> int:
    # 用控制台脚本拉起 server（与 ~/.kimi/mcp.json 的方式一致）
    server_cmd = os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "kimiclaw-memory-mcp")
    server_cmd = os.path.abspath(server_cmd)

    env = dict(os.environ)
    # 强制走 GLM：显式置空 KIMI_*（dotenv 默认不覆盖已存在变量，
    # 故置空可阻止项目 .env 里的 KIMI key 生效，避免用到无 embeddings 端点）
    env["KIMI_API_KEY"] = ""
    env["KIMI_BASE_URL"] = ""
    env["KIMI_MODEL"] = ""
    env.setdefault("EMBEDDING_MODEL", "embedding-3")

    params = StdioServerParameters(command=server_cmd, args=[], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("[initialize] server:", init.serverInfo.name, init.serverInfo.version)

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("[tools/list]", names)
            assert {"memory_add", "memory_search", "memory_get_all",
                    "memory_delete", "memory_compact"}.issubset(set(names)), "工具缺失"

            if live:
                print("[live] memory_add ...")
                r = await session.call_tool("memory_add",
                                            {"messages": "我叫周楠，后端工程师，爱吃火锅",
                                             "user_id": "kimi_live_u1"})
                print("  ->", _text(r))
                print("[live] memory_search ...")
                r = await session.call_tool("memory_search",
                                            {"query": "这个人的职业", "user_id": "kimi_live_u1"})
                print("  ->", _text(r))
            else:
                print("[probe] 无 key 调 memory_search，预期 CONFIG 错误被包装：")
                r = await session.call_tool("memory_search",
                                            {"query": "x", "user_id": "u1"})
                print("  ->", _text(r))

    print("[OK] MCP stdio 链路验证通过")
    return 0


def _text(result) -> str:
    parts = []
    for c in result.content:
        parts.append(getattr(c, "text", str(c)))
    return " ".join(parts)


if __name__ == "__main__":
    live = "--live" in sys.argv
    raise SystemExit(asyncio.run(main(live)))

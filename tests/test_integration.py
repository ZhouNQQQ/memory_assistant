"""端到端集成测试（离线、确定性）。

采用 e2e 设计的「策略 B：组装式伪造」——用内存假核心替换 mem0/Chroma/网络，
但运行**真实的门面层与真实的 MCP 分发逻辑**，验证本功能新增外壳的接线正确：

- Design Property 1：两个集成面（门面 / MCP）行为一致
- Design Property 2：被禁止 metadata 永不外泄
- Design Property 4：user_id 隔离
- Design Property 7：优雅关闭
- Requirements 5.2, 5.3, 7.x, 8.2, 2.3
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kimiclaw_memory.config import MemoryConfig  # noqa: E402
from kimiclaw_memory.facade import Memory  # noqa: E402
from kimiclaw_memory import mcp_server as srv  # noqa: E402


class InMemoryCore:
    """假核心：按 user_id 隔离存储；模拟 add/search/get_all/delete/compact/close。

    每条记忆默认带被禁止 metadata，用于验证门面剥离逻辑贯穿两个集成面。
    """

    def __init__(self, config):
        self.config = config
        self.closed = False
        self._store = {}      # user_id -> list[record]
        self._seq = 0
        self.synced = []      # 模拟"历史事件扇出"，记录每次 add 的 id（无重复）

    def add(self, messages, *, user_id, agent_id=None, run_id=None):
        self._seq += 1
        mid = f"m{self._seq}"
        rec = {
            "id": mid,
            "data": messages if isinstance(messages, str) else str(messages),
            "category": "preference",
            "metadata": {"importance": 0.9, "entity": "x", "confidence": 0.7, "ok": 1},
        }
        self._store.setdefault(user_id, []).append(rec)
        self.synced.append(mid)   # 每条记忆恰好一个历史事件
        return {"results": [rec]}

    def search(self, query, *, user_id, top_k=5, **kw):
        return {"results": list(self._store.get(user_id, []))[:top_k]}

    def get_all(self, *, filters=None, top_k=100, **kw):
        user_id = (filters or {}).get("user_id")
        return {"results": list(self._store.get(user_id, []))[:top_k]}

    def delete(self, memory_id):
        for recs in self._store.values():
            recs[:] = [r for r in recs if r["id"] != memory_id]

    def compact(self, *, user_id=None, dry_run=False):
        total = sum(len(v) for v in self._store.values())
        return {"archived": 0, "merged": 0, "before_count": total,
                "after_count": total, "dry_run": dry_run}

    def close(self):
        self.closed = True


def _cfg(**over):
    base = dict(llm_api_key="sk-test", data_dir=tempfile.mkdtemp())
    base.update(over)
    return MemoryConfig(**base)


class IntegrationBase(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch("memory.kimi_claw_memory.KimiClawMemory", InMemoryCore)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        srv.set_memory(None)


class TestPipelineViaFacade(IntegrationBase):
    def test_multi_turn_add_search_compact(self):
        with Memory(_cfg()) as mem:
            mem.add("我叫 Alice，喜欢火锅", user_id="u1")
            mem.add("我是后端工程师", user_id="u1")
            hits = mem.search("用户喜欢什么", user_id="u1", limit=10)
            self.assertEqual(len(hits), 2)
            # 禁止 metadata 全部被剥离
            for h in hits:
                for k in ("importance", "entity", "confidence"):
                    self.assertNotIn(k, h["metadata"])
            report = mem.compact(user_id="u1")
            self.assertEqual(report["before_count"], 2)
            core = mem._core
            # 历史扇出：2 条记忆 → 2 个事件，无重复
            self.assertEqual(len(core.synced), 2)
            self.assertEqual(len(set(core.synced)), 2)
        self.assertTrue(core.closed)  # 优雅关闭

    def test_user_id_isolation(self):
        with Memory(_cfg()) as mem:
            mem.add("u1 的秘密", user_id="u1")
            mem.add("u2 的秘密", user_id="u2")
            self.assertEqual(len(mem.search("x", user_id="u1", limit=10)), 1)
            self.assertEqual(mem.search("x", user_id="u1", limit=10)[0]["data"], "u1 的秘密")
            self.assertEqual(len(mem.search("x", user_id="u2", limit=10)), 1)


class TestTwoFacesConsistent(IntegrationBase):
    """Design Property 1：经门面 与 经 MCP 入口产生一致结果与状态。"""

    def test_mcp_and_facade_equivalent(self):
        # 门面面
        mem_a = Memory(_cfg())
        mem_a.add("我叫 Alice", user_id="u1")
        face_search = mem_a.search("q", user_id="u1", limit=10)

        # MCP 面（共享同一个门面单例）
        srv.set_memory(mem_a)
        mcp_add = srv.memory_add(messages="我是工程师", user_id="u1")
        mcp_search = srv.memory_search(query="q", user_id="u1", limit=10)

        self.assertTrue(mcp_add["ok"])
        self.assertTrue(mcp_search["ok"])
        # MCP 返回的数据结构与门面一致（同为剥离后的记录列表）
        self.assertEqual(
            {r["id"] for r in mcp_search["data"]},
            {r["id"] for r in mem_a.search("q", user_id="u1", limit=10)},
        )
        # 两面都不外泄禁止字段
        for r in mcp_search["data"]:
            for k in ("importance", "entity", "confidence"):
                self.assertNotIn(k, r["metadata"])
        mem_a.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

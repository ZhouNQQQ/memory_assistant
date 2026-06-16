"""门面层单元测试（Requirements 1.1,1.3,1.5,8.1,9.3）。

通过 mock 既有核心 `KimiClawMemory` 与注入器，保持离线、确定性，
不触发真实 mem0/Chroma/LLM/网络。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kimiclaw_memory.config import MemoryConfig  # noqa: E402
from kimiclaw_memory import facade as facade_mod  # noqa: E402
from kimiclaw_memory.facade import Memory  # noqa: E402


def _cfg(**over):
    base = dict(llm_api_key="sk-test", data_dir=tempfile.mkdtemp())
    base.update(over)
    return MemoryConfig(**base)


class FakeCore:
    """假的 KimiClawMemory：记录调用并返回可控结果。"""

    def __init__(self, config):
        self.config = config
        self.closed = False
        self.add_calls = []
        self.search_calls = []
        self.getall_calls = []
        self.deleted = []
        self.compact_calls = []
        # 默认 add 返回含被禁止 metadata 的记录，用于验证剥离
        self.add_result = {
            "results": [
                {
                    "id": "m1",
                    "data": "用户喜欢火锅",
                    "category": "preference",
                    "metadata": {"importance": 0.9, "entity": "food", "confidence": 0.8, "ok": 1},
                }
            ]
        }
        self.search_result = {"results": [{"id": "m1", "data": "x", "metadata": {"importance": 1}}]}
        self.getall_result = {"results": [{"id": "m1", "data": "x", "metadata": {}}]}

    def add(self, messages, *, user_id, agent_id=None, run_id=None):
        self.add_calls.append((messages, user_id, agent_id, run_id))
        return self.add_result

    def search(self, query, *, user_id, top_k=5, **kw):
        self.search_calls.append((query, user_id, top_k))
        return self.search_result

    def get_all(self, *, filters=None, top_k=100, **kw):
        user_id = (filters or {}).get("user_id")
        self.getall_calls.append((user_id, top_k))
        return self.getall_result

    def delete(self, memory_id):
        self.deleted.append(memory_id)

    def compact(self, *, user_id=None, dry_run=False):
        self.compact_calls.append((user_id, dry_run))
        return {"archived": 0, "merged": 0, "dry_run": dry_run}

    def close(self):
        self.closed = True


class FacadeTestBase(unittest.TestCase):
    def setUp(self):
        # 把 facade 内 `from memory.kimi_claw_memory import KimiClawMemory` 指向 FakeCore
        self._p_core = mock.patch("memory.kimi_claw_memory.KimiClawMemory", FakeCore)
        self._p_core.start()

    def tearDown(self):
        self._p_core.stop()


class TestProxy(FacadeTestBase):
    def test_add_proxies_and_strips_forbidden_metadata(self):
        mem = Memory(_cfg())
        out = mem.add("hi", user_id="u1")
        self.assertEqual(out["added"], 1)
        md = out["results"][0]["metadata"]
        for k in ("importance", "entity", "confidence"):
            self.assertNotIn(k, md)
        self.assertIn("ok", md)  # 非禁止字段保留
        self.assertEqual(mem._core.add_calls[0][1], "u1")

    def test_search_maps_limit_to_top_k_and_strips(self):
        mem = Memory(_cfg())
        res = mem.search("q", user_id="u1", limit=3)
        self.assertEqual(mem._core.search_calls[0], ("q", "u1", 3))
        self.assertNotIn("importance", res[0]["metadata"])

    def test_get_all_proxies(self):
        mem = Memory(_cfg())
        mem.get_all(user_id="u1", limit=50)
        self.assertEqual(mem._core.getall_calls[0], ("u1", 50))

    def test_delete_proxies(self):
        mem = Memory(_cfg())
        out = mem.delete("m1", user_id="u1")
        self.assertEqual(out, {"deleted": "m1"})
        self.assertEqual(mem._core.deleted, ["m1"])

    def test_compact_dry_run(self):
        mem = Memory(_cfg())
        out = mem.compact(user_id="u1", dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(mem._core.compact_calls[0], ("u1", True))


class TestUserIdValidation(FacadeTestBase):
    def test_missing_user_id_raises(self):
        mem = Memory(_cfg())
        for call in (
            lambda: mem.add("x", user_id=""),
            lambda: mem.search("q", user_id="  "),
            lambda: mem.get_all(user_id=""),
            lambda: mem.delete("m1", user_id=""),
        ):
            with self.assertRaises(ValueError):
                call()


class TestLifecycle(FacadeTestBase):
    def test_context_manager_closes(self):
        with Memory(_cfg()) as mem:
            core = mem._core
        self.assertTrue(core.closed)


class TestOpenclawInject(FacadeTestBase):
    def test_inject_disabled_by_default(self):
        mem = Memory(_cfg())  # enable_openclaw_inject 默认 False
        self.assertIsNone(mem._injector)
        mem.add("x", user_id="u1", auto_inject=True)  # 即便传 True，无注入器也不写

    def test_inject_enabled_calls_injector(self):
        fake_inj = mock.MagicMock()
        with mock.patch("memory.injector.QClawInjector", return_value=fake_inj):
            mem = Memory(_cfg(enable_openclaw_inject=True, qclaw_workspace_dir=tempfile.mkdtemp()))
            mem.add("x", user_id="u1", auto_inject=True)
            self.assertTrue(fake_inj.inject_memories.called)

    def test_inject_not_called_when_auto_inject_false(self):
        fake_inj = mock.MagicMock()
        with mock.patch("memory.injector.QClawInjector", return_value=fake_inj):
            mem = Memory(_cfg(enable_openclaw_inject=True, qclaw_workspace_dir=tempfile.mkdtemp()))
            mem.add("x", user_id="u1", auto_inject=False)
            self.assertFalse(fake_inj.inject_memories.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)

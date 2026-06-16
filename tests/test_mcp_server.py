"""MCP server 层测试（Requirements 2.1,2.4,2.5）。

通过 set_memory 注入门面替身，直接调用工具函数，验证统一成功/失败包装，
不启动真实子进程、不触发网络。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kimiclaw_memory import mcp_server as srv  # noqa: E402
from kimiclaw_memory.config import ConfigError  # noqa: E402


class FakeMem:
    def __init__(self):
        self.added = []

    def add(self, messages, user_id):
        self.added.append((messages, user_id))
        return {"added": 1, "results": [{"id": "m1", "data": messages}]}

    def search(self, query, user_id, limit=5):
        return [{"id": "m1", "data": "hit", "score": 0.9}]

    def get_all(self, user_id, limit=100):
        return [{"id": "m1", "data": "x"}]

    def delete(self, memory_id, user_id):
        return {"deleted": memory_id}

    def compact(self, user_id=None, dry_run=False):
        return {"archived": 0, "dry_run": dry_run}


class RaisingMem:
    def __init__(self, exc):
        self._exc = exc

    def __getattr__(self, _):
        def _raise(*a, **k):
            raise self._exc
        return _raise


class MCPTestBase(unittest.TestCase):
    def tearDown(self):
        srv.set_memory(None)


class TestSuccessWrapping(MCPTestBase):
    def setUp(self):
        self.fake = FakeMem()
        srv.set_memory(self.fake)

    def test_add_ok_and_persists(self):
        out = srv.memory_add(messages="hello", user_id="u1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["added"], 1)
        self.assertEqual(self.fake.added[0], ("hello", "u1"))

    def test_search_ok(self):
        out = srv.memory_search(query="q", user_id="u1", limit=3)
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"][0]["id"], "m1")

    def test_get_all_ok(self):
        out = srv.memory_get_all(user_id="u1")
        self.assertTrue(out["ok"])

    def test_delete_ok(self):
        out = srv.memory_delete(memory_id="m1", user_id="u1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"], {"deleted": "m1"})

    def test_compact_ok(self):
        out = srv.memory_compact(user_id="u1", dry_run=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["data"]["dry_run"])


class TestErrorWrapping(MCPTestBase):
    def test_backend_error_wrapped(self):
        srv.set_memory(RaisingMem(RuntimeError("boom")))
        out = srv.memory_search(query="q", user_id="u1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "BACKEND")
        self.assertIn("boom", out["error"])

    def test_config_error_code(self):
        srv.set_memory(RaisingMem(ConfigError("no key")))
        out = srv.memory_add(messages="x", user_id="u1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "CONFIG")

    def test_not_found_code(self):
        srv.set_memory(RaisingMem(KeyError("missing")))
        out = srv.memory_delete(memory_id="zzz", user_id="u1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "NOT_FOUND")

    def test_value_error_is_backend(self):
        srv.set_memory(RaisingMem(ValueError("user_id 必填")))
        out = srv.memory_add(messages="x", user_id="")
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "BACKEND")
        # 不泄露堆栈：error 仅为消息文本
        self.assertNotIn("Traceback", out["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

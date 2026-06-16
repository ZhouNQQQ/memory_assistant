"""属性测试（Design Properties 2/4/6/3）。

对应任务 3.4 / 4.4 / 5.2。用 hypothesis 生成随机输入，断言不变量恒成立。
全部离线、确定性（假核心 + 守卫函数，不触发网络）。
"""

import os
import string
import sys
import tempfile
import unittest
from unittest import mock

from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kimiclaw_memory.config import (  # noqa: E402
    DataDirBoundaryError,
    MemoryConfig,
    assert_safe_data_dir,
    _PROTECTED_DIR_FRAGMENTS,
)
from kimiclaw_memory.facade import Memory, _FORBIDDEN_METADATA_KEYS  # noqa: E402
from kimiclaw_memory import mcp_server as srv  # noqa: E402
from kimiclaw_memory.config import ConfigError  # noqa: E402

_IDS = st.text(alphabet=string.ascii_letters + string.digits + "_", min_size=1, max_size=10)
_TEXT = st.text(alphabet=string.ascii_letters + string.digits + " 中文", min_size=1, max_size=40)


# ──────────────────────────────────────────────────────────────────
# 假核心：按 user_id 隔离，每条记忆都带随机被禁止 metadata
# ──────────────────────────────────────────────────────────────────
class IsoCore:
    def __init__(self, config):
        self._store = {}
        self._seq = 0
        self.closed = False

    def add(self, messages, *, user_id, agent_id=None, run_id=None):
        self._seq += 1
        rec = {
            "id": f"m{self._seq}",
            "memory": messages if isinstance(messages, str) else str(messages),
            # 故意混入全部被禁止字段 + 一个良性字段
            "metadata": {"importance": 0.5, "entity": "e", "confidence": 0.3, "ok": 1},
        }
        self._store.setdefault(user_id, []).append(rec)
        return {"results": [rec]}

    def search(self, query, *, user_id=None, filters=None, top_k=5, **kw):
        uid = user_id if user_id is not None else (filters or {}).get("user_id")
        return {"results": list(self._store.get(uid, []))[:top_k]}

    def get_all(self, *, filters=None, top_k=100, **kw):
        uid = (filters or {}).get("user_id")
        return {"results": list(self._store.get(uid, []))[:top_k]}

    def delete(self, memory_id):
        for recs in self._store.values():
            recs[:] = [r for r in recs if r["id"] != memory_id]

    def compact(self, *, user_id=None, dry_run=False):
        return {"dry_run": dry_run}

    def close(self):
        self.closed = True


def _cfg():
    return MemoryConfig(llm_api_key="sk", data_dir=tempfile.mkdtemp())


class PropFacadeBase(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch("memory.kimi_claw_memory.KimiClawMemory", IsoCore)
        self._p.start()

    def tearDown(self):
        self._p.stop()


class TestForbiddenMetadataAbsence(PropFacadeBase):
    """Design Property 2：被禁止 metadata 永不外泄。"""

    @settings(max_examples=60, deadline=None)
    @given(entries=st.lists(st.tuples(_IDS, _TEXT), min_size=1, max_size=8))
    def test_no_forbidden_keys_in_any_output(self, entries):
        mem = Memory(_cfg())
        try:
            for uid, text in entries:
                out = mem.add(text, user_id=uid)
                for rec in out["results"]:
                    self._assert_clean(rec)
            for uid, _ in entries:
                for rec in mem.search("q", user_id=uid, limit=50):
                    self._assert_clean(rec)
                for rec in mem.get_all(user_id=uid, limit=50):
                    self._assert_clean(rec)
        finally:
            mem.close()

    def _assert_clean(self, rec):
        md = rec.get("metadata", {})
        for k in _FORBIDDEN_METADATA_KEYS:
            self.assertNotIn(k, md)


class TestUserIdIsolation(PropFacadeBase):
    """Design Property 4：user_id 隔离。"""

    @settings(max_examples=60, deadline=None)
    @given(
        data=st.dictionaries(_IDS, st.lists(_TEXT, min_size=1, max_size=4), min_size=2, max_size=5)
    )
    def test_search_never_leaks_other_users(self, data):
        mem = Memory(_cfg())
        try:
            for uid, texts in data.items():
                for t in texts:
                    mem.add(t, user_id=uid)
            for uid, texts in data.items():
                got = {r["data"] for r in mem.get_all(user_id=uid, limit=100)}
                # 仅包含该 user 自己写入的文本，绝不含他人
                self.assertTrue(got.issubset(set(texts)))
                for other, otexts in data.items():
                    if other == uid:
                        continue
                    exclusive = set(otexts) - set(texts)
                    self.assertTrue(got.isdisjoint(exclusive))
        finally:
            mem.close()


class TestMcpErrorWrapping(unittest.TestCase):
    """Design Property 6：MCP 错误统一包装，不泄露堆栈。"""

    def tearDown(self):
        srv.set_memory(None)

    @settings(max_examples=50, deadline=None)
    @given(
        exc_kind=st.sampled_from(["config", "keyerror", "runtime", "value", "type"]),
        msg=st.text(alphabet=string.ascii_letters + string.digits + " ", min_size=0, max_size=30),
    )
    def test_any_exception_is_wrapped(self, exc_kind, msg):
        exc = {
            "config": ConfigError(msg),
            "keyerror": KeyError(msg),
            "runtime": RuntimeError(msg),
            "value": ValueError(msg),
            "type": TypeError(msg),
        }[exc_kind]

        class Raising:
            def __getattr__(self, _):
                def f(*a, **k):
                    raise exc
                return f

        srv.set_memory(Raising())
        out = srv.memory_search(query="q", user_id="u1", limit=3)
        self.assertFalse(out["ok"])
        self.assertIn(out["code"], {"CONFIG", "NOT_FOUND", "BACKEND"})
        self.assertNotIn("Traceback", out.get("error", ""))
        # 分类正确性
        if exc_kind == "config":
            self.assertEqual(out["code"], "CONFIG")
        elif exc_kind == "keyerror":
            self.assertEqual(out["code"], "NOT_FOUND")
        else:
            self.assertEqual(out["code"], "BACKEND")


class TestDaimonZeroTouch(unittest.TestCase):
    """Design Property 3：受保护目录（Daimon/QClaw）永不被选为数据目录。"""

    @settings(max_examples=80, deadline=None)
    @given(frag=st.sampled_from(list(_PROTECTED_DIR_FRAGMENTS)),
           suffix=st.text(alphabet=string.ascii_letters + "/_", min_size=0, max_size=20))
    def test_protected_paths_rejected(self, frag, suffix):
        bad = f"~/{frag}/{suffix}"
        with self.assertRaises(DataDirBoundaryError):
            assert_safe_data_dir(bad)

    @settings(max_examples=80, deadline=None)
    @given(name=st.text(alphabet=string.ascii_letters + string.digits + "_-", min_size=1, max_size=20))
    def test_independent_paths_allowed(self, name):
        base = tempfile.mkdtemp()
        # 普通独立目录应通过（排除极小概率命中受保护片段）
        path = os.path.join(base, name)
        if any(fr in os.path.abspath(os.path.expanduser(path)) for fr in _PROTECTED_DIR_FRAGMENTS):
            self.skipTest("随机路径意外命中受保护片段")
        assert_safe_data_dir(path)  # 不抛异常


if __name__ == "__main__":
    unittest.main(verbosity=2)

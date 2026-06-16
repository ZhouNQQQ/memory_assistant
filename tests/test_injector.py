"""
测试 QClawInjector 的 USER.md / SOUL.md 注入逻辑。

验证点：
1. USER.md 解析：正确提取字段和 Context
2. 增量更新：不覆盖已有字段（除非为空），新字段正确填充
3. 分类映射：不同 category 的记忆映射到正确字段
4. Context 追加：去重，不重复追加
5. SOUL.md 更新：创建或追加 Preferences 段落
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from memory.injector import UserMdParser, QClawInjector, inject_to_qclaw


class TestUserMdParser(unittest.TestCase):
    """测试 USER.md 解析器。"""

    def test_parse_fields(self):
        """解析顶层字段。"""
        content = """# USER.md

- **Name:** Alice
- **What to call them:** Ali
- **Pronouns:** _(optional)_ she/her
- **Timezone:** Asia/Shanghai
- **Notes:** Loves coffee

## Context

User is a developer working on AI memory systems.

---
"""
        parser = UserMdParser(content)
        self.assertEqual(parser.fields["name"], "Alice")
        self.assertEqual(parser.fields["what_to_call_them"], "Ali")
        self.assertEqual(parser.fields["pronouns"], "she/her")
        self.assertEqual(parser.fields["timezone"], "Asia/Shanghai")
        self.assertEqual(parser.fields["notes"], "Loves coffee")

    def test_parse_context(self):
        """解析 Context 段落。"""
        content = """# USER.md
- **Name:** Bob

## Context

User enjoys hiking.

---
"""
        parser = UserMdParser(content)
        self.assertEqual(parser.context, "User enjoys hiking.")

    def test_set_field(self):
        """设置字段后正确序列化。"""
        content = """# USER.md
- **Name:**
- **Notes:**

## Context

---
"""
        parser = UserMdParser(content)
        parser.set_field("name", "Charlie")
        parser.set_field("notes", "Pizza lover")
        md = parser.to_markdown()
        self.assertIn("- **Name:** Charlie", md)
        self.assertIn("- **Notes:** Pizza lover", md)

    def test_append_context(self):
        """追加 Context 内容。"""
        content = """# USER.md
- **Name:** Dana

## Context

First line.

---
"""
        parser = UserMdParser(content)
        parser.append_context("Second line.")
        md = parser.to_markdown()
        self.assertIn("First line.", md)
        self.assertIn("Second line.", md)

    def test_context_deduplication(self):
        """相同内容不重复追加。"""
        content = """# USER.md
## Context

Existing.

---
"""
        parser = UserMdParser(content)
        parser.append_context("Existing.")
        self.assertEqual(parser.context, "Existing.")


class TestQClawInjector(unittest.TestCase):
    """测试 QClawInjector 注入逻辑。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name)
        # 创建初始 USER.md
        (self.workspace / "USER.md").write_text(
            "# USER.md\n\n- **Name:**\n- **What to call them:**\n- **Notes:**\n\n## Context\n\n\n---\n",
            encoding="utf-8",
        )
        # 创建初始 SOUL.md
        (self.workspace / "SOUL.md").write_text(
            "# SOUL.md\n\n## Core Truths\n\nBe helpful.\n\n## Continuity\n\nEach session...\n",
            encoding="utf-8",
        )
        self.injector = QClawInjector(str(self.workspace))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_inject_name(self):
        """personal 类记忆填充 Name 字段。"""
        memories = [
            {"data": "User's name is Alice Smith", "category": "personal"},
        ]
        stats = self.injector.inject_memories(memories)
        self.assertEqual(stats["fields_updated"], 1)

        parser = UserMdParser((self.workspace / "USER.md").read_text())
        self.assertEqual(parser.fields["name"], "Alice Smith")

    def test_inject_timezone(self):
        """personal 类记忆填充 Timezone 字段。"""
        memories = [
            {"data": "User timezone is Asia/Shanghai", "category": "personal"},
        ]
        self.injector.inject_memories(memories)
        parser = UserMdParser((self.workspace / "USER.md").read_text())
        self.assertEqual(parser.fields["timezone"], "Asia/Shanghai")

    def test_context_append(self):
        """未匹配到字段的记忆追加到 Context。"""
        memories = [
            {"data": "User is working on a distributed system project", "category": "professional"},
        ]
        stats = self.injector.inject_memories(memories)
        self.assertEqual(stats["context_appended"], 1)

        parser = UserMdParser((self.workspace / "USER.md").read_text())
        self.assertIn("distributed system project", parser.context)

    def test_no_overwrite_existing_name(self):
        """已有 Name 不自动覆盖（需确认）。"""
        (self.workspace / "USER.md").write_text(
            "# USER.md\n\n- **Name:** Alice\n\n## Context\n\n\n---\n",
            encoding="utf-8",
        )
        memories = [
            {"data": "User's name is Bob", "category": "personal"},
        ]
        stats = self.injector.inject_memories(memories)
        self.assertEqual(stats["fields_updated"], 0)
        self.assertEqual(stats["skipped"], 0)

        parser = UserMdParser((self.workspace / "USER.md").read_text())
        self.assertEqual(parser.fields["name"], "Alice")

    def test_update_soul_preferences(self):
        """preference 类记忆更新 SOUL.md。"""
        memories = [
            {"data": "User prefers concise answers", "category": "preference"},
            {"data": "User wants to be asked before any external action", "category": "preference"},
        ]
        stats = self.injector.update_soul(memories)
        self.assertEqual(stats["soul_updated"], 2)

        soul = (self.workspace / "SOUL.md").read_text()
        self.assertIn("## Preferences", soul)
        self.assertIn("concise answers", soul)
        self.assertIn("external action", soul)

    def test_update_soul_existing_preferences(self):
        """在已有 Preferences 段落追加。"""
        (self.workspace / "SOUL.md").write_text(
            "# SOUL.md\n\n## Preferences\n\n- User likes dark mode\n\n## Continuity\n\n...\n",
            encoding="utf-8",
        )
        memories = [
            {"data": "User prefers concise answers", "category": "preference"},
        ]
        self.injector.update_soul(memories)
        soul = (self.workspace / "SOUL.md").read_text()
        self.assertIn("dark mode", soul)
        self.assertIn("concise answers", soul)

    def test_inject_to_qclaw_convenience(self):
        """便捷函数 inject_to_qclaw 正常工作。"""
        memories = [
            {"data": "User's name is Test User", "category": "personal"},
        ]
        stats = inject_to_qclaw(memories, str(self.workspace))
        self.assertEqual(stats["fields_updated"], 1)

        parser = UserMdParser((self.workspace / "USER.md").read_text())
        self.assertEqual(parser.fields["name"], "Test User")


if __name__ == "__main__":
    unittest.main(verbosity=2)

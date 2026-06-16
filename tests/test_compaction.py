"""
测试 CompactionEngine 的三个核心策略。

验证点：
1. TimeDecayStrategy：正确计算衰减系数，低于阈值时归档
2. DeduplicationStrategy：相似记忆正确分组合并
3. SummarizationStrategy：prompt 构建正确，调用 LLM 生成摘要
4. CompactionEngine：主流程快照 → 衰减 → 去重 → 摘要 → 清理
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from memory.compaction.engine import (
    TimeDecayStrategy,
    DeduplicationStrategy,
    SummarizationStrategy,
    CompactionEngine,
)


class TestTimeDecayStrategy(unittest.TestCase):
    """测试时间衰减策略。"""

    def setUp(self):
        self.strategy = TimeDecayStrategy(half_life_days=90, archive_threshold=0.3)

    def test_decay_today(self):
        """今天的记忆衰减系数接近 1.0。"""
        now = datetime.now(timezone.utc)
        decay = self.strategy.compute_decay(now)
        self.assertAlmostEqual(decay, 1.0, places=2)

    def test_decay_half_life(self):
        """90天前的记忆衰减系数为 0.5。"""
        half_life = datetime.now(timezone.utc) - timedelta(days=90)
        decay = self.strategy.compute_decay(half_life)
        self.assertAlmostEqual(decay, 0.5, places=2)

    def test_archive_old_memory(self):
        """200天前的记忆应该被归档。"""
        old_time = datetime.now(timezone.utc) - timedelta(days=200)
        memory = {"updated_at": old_time.isoformat(), "data": "old fact"}
        self.assertTrue(self.strategy.should_archive(memory))

    def test_keep_recent_memory(self):
        """7天前的记忆不应该被归档。"""
        recent = datetime.now(timezone.utc) - timedelta(days=7)
        memory = {"updated_at": recent.isoformat(), "data": "recent fact"}
        self.assertFalse(self.strategy.should_archive(memory))

    def test_apply_separates_active_and_archive(self):
        """apply 方法正确分离活跃和归档记忆。"""
        memories = [
            {"data": "recent", "updated_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()},
            {"data": "old", "updated_at": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()},
        ]
        active, archive = self.strategy.apply(memories)
        self.assertEqual(len(active), 1)
        self.assertEqual(len(archive), 1)
        self.assertEqual(active[0]["data"], "recent")
        self.assertEqual(archive[0]["data"], "old")
        # 验证活跃记忆被标记了 decay_score
        self.assertIn("decay_score", active[0].get("metadata", {}))


class TestDeduplicationStrategy(unittest.TestCase):
    """测试去重合并策略。"""

    def setUp(self):
        self.strategy = DeduplicationStrategy(similarity_threshold=0.95)

    def test_find_similar_groups(self):
        """相似向量被正确分组。"""
        # 构造 3 条记忆，其中 2 条几乎相同
        memories = [
            {"id": "m1", "data": "User likes pizza"},
            {"id": "m2", "data": "User loves pizza very much"},
            {"id": "m3", "data": "User has a dog named Max"},
        ]
        # 构造 embeddings：m1 和 m2 相似度极高，m3 不同
        embeddings = [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],  # 与 m1 非常相似
            [0.0, 1.0, 0.0],   # 与 m1/m2 完全不同
        ]
        groups = self.strategy.find_duplicate_groups(memories, embeddings)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0], [0, 1])

    def test_no_duplicates(self):
        """不相似的记忆不分组。"""
        memories = [
            {"id": "m1", "data": "User likes pizza"},
            {"id": "m2", "data": "User has a dog"},
        ]
        embeddings = [
            [1.0, 0.0],
            [0.0, 1.0],
        ]
        groups = self.strategy.find_duplicate_groups(memories, embeddings)
        self.assertEqual(len(groups), 0)

    def test_merge_group(self):
        """合并组保留最长内容。"""
        group = [
            {"id": "m1", "data": "User likes pizza"},
            {"id": "m2", "data": "User likes pizza with extra cheese and pepperoni"},
        ]
        merged = self.strategy.merge_group(group)
        self.assertEqual(merged["id"], "m2")  # 更长的那条
        self.assertEqual(merged["metadata"]["merged_count"], 2)
        self.assertIn("m1", merged["metadata"]["merged_from"])

    def test_apply_removes_duplicates(self):
        """apply 方法正确删除重复记忆。"""
        memories = [
            {"id": "m1", "data": "User likes pizza"},
            {"id": "m2", "data": "User loves pizza very much"},
            {"id": "m3", "data": "User has a dog"},
        ]
        embeddings = [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.0, 1.0, 0.0],
        ]
        result, merged_count = self.strategy.apply(memories, embeddings)
        self.assertEqual(len(result), 2)
        self.assertEqual(merged_count, 1)
        # m3 保留，m2 保留（合并后的主记忆），m1 被删除
        ids = {m["id"] for m in result}
        self.assertIn("m2", ids)
        self.assertIn("m3", ids)
        self.assertNotIn("m1", ids)


class TestSummarizationStrategy(unittest.TestCase):
    """测试滚动摘要策略。"""

    def setUp(self):
        self.strategy = SummarizationStrategy(max_memories=50, max_summary_length=500)

    def test_format_prompt_structure(self):
        """prompt 包含分类结构。"""
        memories = [
            {"data": "User likes pizza", "metadata": {"category": "preference"}},
            {"data": "User is a developer", "metadata": {"category": "professional"}},
        ]
        prompt = self.strategy._format_prompt(memories)
        self.assertIn("preference", prompt)
        self.assertIn("professional", prompt)
        self.assertIn("User likes pizza", prompt)

    def test_generate_summary_calls_llm(self):
        """generate_summary 正确调用 LLM 函数。"""
        mock_llm = MagicMock(return_value="User is a developer who likes pizza.")
        memories = [
            {"data": "User likes pizza"},
            {"data": "User is a developer"},
        ]
        summary = self.strategy.generate_summary(memories, mock_llm)
        self.assertEqual(summary, "User is a developer who likes pizza.")
        mock_llm.assert_called_once()

    def test_empty_memories(self):
        """空记忆列表返回空摘要。"""
        mock_llm = MagicMock(return_value="")
        summary = self.strategy.generate_summary([], mock_llm)
        self.assertEqual(summary, "")
        mock_llm.assert_not_called()


class TestCompactionEngine(unittest.TestCase):
    """测试 CompactionEngine 主流程。"""

    def setUp(self):
        """创建 mock 的 memory 实例。"""
        self.mock_memory = MagicMock()
        self.mock_memory.config.history_db_path = ":memory:"
        self.mock_memory.vector_store.list.return_value = []
        self.mock_memory.embedding_model.embed.side_effect = lambda text, op: [0.1, 0.2, 0.3]
        self.mock_memory.llm.generate_response.return_value = "User summary."

        self.mock_sync = MagicMock()

        self.config = {
            "half_life_days": 90,
            "archive_threshold": 0.3,
            "similarity_threshold": 0.95,
            "keep_history": 100,
        }
        self.engine = CompactionEngine(self.mock_memory, self.config, self.mock_sync)

    def test_compact_empty_memories(self):
        """空记忆时返回空报告。"""
        self.mock_memory.vector_store.list.return_value = []
        report = self.engine.compact(user_id="user_001")
        self.assertEqual(report["before_count"], 0)
        self.assertEqual(report["after_count"], 0)

    def test_compact_with_archiving(self):
        """旧记忆被正确归档。"""
        old_time = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        recent_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.mock_memory.vector_store.list.return_value = [
            {"id": "m1", "data": "old fact", "payload": {"updated_at": old_time, "data": "old fact"}},
            {"id": "m2", "data": "recent fact", "payload": {"updated_at": recent_time, "data": "recent fact"}},
        ]
        report = self.engine.compact(user_id="user_001")
        self.assertEqual(report["archived"], 1)
        self.assertEqual(report["before_count"], 2)
        self.assertEqual(report["after_count"], 1)

    def test_compact_generates_summary(self):
        """compaction 生成摘要并推送到 GitHub。"""
        self.mock_memory.vector_store.list.return_value = [
            {"id": "m1", "data": "fact 1", "payload": {"updated_at": datetime.now(timezone.utc).isoformat(), "data": "fact 1"}},
        ]
        report = self.engine.compact(user_id="user_001")
        self.assertTrue(report["summary_generated"])
        self.assertGreater(report["summary_length"], 0)
        # 验证同步队列收到 profile 事件
        self.mock_sync.queue_event.assert_called()

    def test_dry_run_no_modifications(self):
        """dry_run 不修改任何数据。"""
        self.mock_memory.vector_store.list.return_value = [
            {"id": "m1", "data": "fact", "payload": {"updated_at": datetime.now(timezone.utc).isoformat(), "data": "fact"}},
        ]
        report = self.engine.compact(user_id="user_001", dry_run=True)
        # 验证没有删除、更新操作
        self.mock_memory.vector_store.delete.assert_not_called()
        self.mock_memory.vector_store.update.assert_not_called()

    def test_concurrent_compaction_rejected(self):
        """并发 compaction 被拒绝。"""
        self.engine._is_running = True
        with self.assertRaises(RuntimeError):
            self.engine.compact()


if __name__ == "__main__":
    unittest.main(verbosity=2)

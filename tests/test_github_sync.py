"""
测试 GitHubSyncManager 的核心同步通路。

验证点：
1. 事件进入队列后，flush 时正确按 user_id + event_type 分组
2. GitHub Contents API 调用逻辑正确（create / update / 冲突重试）
3. 后台线程定时 flush 正常
4. 失败事件重试后丢弃
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from memory.storage.github_manager import (
    GitHubSyncManager,
    GitHubClient,
    SyncEvent,
    make_history_event,
    make_memory_event,
)


class TestGitHubClient(unittest.TestCase):
    """测试 GitHub Contents API 封装。"""

    @patch("memory.storage.github_manager.requests.get")
    def test_get_file_exists(self, mock_get):
        """读取已存在的文件。"""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "content": "SGVsbG8gV29ybGQ=",  # base64("Hello World")
            "sha": "abc123",
            "size": 11,
        }
        client = GitHubClient("user/repo", "token123")
        result = client.get_file("test.txt")

        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "Hello World")
        self.assertEqual(result["sha"], "abc123")

    @patch("memory.storage.github_manager.requests.get")
    def test_get_file_not_found(self, mock_get):
        """读取不存在的文件返回 None。"""
        mock_get.return_value.status_code = 404
        client = GitHubClient("user/repo", "token123")
        result = client.get_file("nonexistent.txt")
        self.assertIsNone(result)

    @patch("memory.storage.github_manager.requests.put")
    def test_create_file(self, mock_put):
        """创建新文件。"""
        mock_put.return_value.status_code = 201
        mock_put.return_value.json.return_value = {"commit": {"sha": "xyz"}}
        client = GitHubClient("user/repo", "token123")
        success = client.create_file("new.txt", "content", "create message")
        self.assertTrue(success)

        # 验证调用参数
        call_args = mock_put.call_args
        self.assertEqual(call_args[1]["json"]["message"], "create message")
        self.assertEqual(call_args[1]["json"]["branch"], "main")
        # 验证 content 是 base64 编码
        import base64
        decoded = base64.b64decode(call_args[1]["json"]["content"]).decode("utf-8")
        self.assertEqual(decoded, "content")

    @patch("memory.storage.github_manager.requests.put")
    def test_update_file(self, mock_put):
        """更新现有文件。"""
        mock_put.return_value.status_code = 200
        client = GitHubClient("user/repo", "token123")
        success = client.update_file("existing.txt", "new content", "sha456", "update message")
        self.assertTrue(success)

        call_args = mock_put.call_args
        self.assertEqual(call_args[1]["json"]["sha"], "sha456")

    @patch("memory.storage.github_manager.requests.put")
    def test_update_conflict(self, mock_put):
        """更新冲突时返回 False。"""
        mock_put.return_value.status_code = 409
        client = GitHubClient("user/repo", "token123")
        success = client.update_file("existing.txt", "new content", "sha456", "update message")
        self.assertFalse(success)


class TestGitHubSyncManager(unittest.TestCase):
    """测试 GitHubSyncManager 的同步逻辑。"""

    @patch("memory.storage.github_manager.GitHubClient")
    def setUp(self, mock_client_class):
        """每个测试前创建 SyncManager 实例。"""
        self.mock_client = MagicMock()
        mock_client_class.return_value = self.mock_client

        self.sync = GitHubSyncManager(
            repo="user/repo",
            token="token123",
            branch="main",
            sync_interval=300,
            batch_size=5,
        )

    def tearDown(self):
        """清理。"""
        self.sync.stop()

    def test_queue_and_batch_flush(self):
        """队列满 batch_size 后触发 flush。"""
        # mock get_file 返回 None（文件不存在，会创建）
        self.mock_client.get_file.return_value = None
        self.mock_client.create_file.return_value = True

        # 加入 5 条事件（达到 batch_size=5）
        for i in range(5):
            ev = make_history_event(
                user_id="user_001",
                memory_id=f"mem_{i}",
                event="ADD",
                new_memory=f"memory content {i}",
            )
            self.sync.queue_event(ev)

        # 等待后台线程处理
        time.sleep(0.5)

        # 验证 create_file 被调用（因为文件不存在）
        self.assertTrue(self.mock_client.create_file.called)

    def test_event_grouping(self):
        """相同 user_id 的 event 被分组到同一文件。"""
        self.mock_client.get_file.return_value = None
        self.mock_client.create_file.return_value = True

        # 3 条 user_001 的 history + 2 条 user_002 的 history
        for i in range(3):
            self.sync.queue_event(make_history_event("user_001", f"m{i}", "ADD"))
        for i in range(2):
            self.sync.queue_event(make_history_event("user_002", f"m{i}", "ADD"))

        self.sync._flush()

        # 验证 create_file 被调用 2 次（两个 user 各一个文件）
        self.assertEqual(self.mock_client.create_file.call_count, 2)

    def test_conflict_retry(self):
        """冲突时重新读取并合并。"""
        # 第一次 update 返回 409（冲突），第二次成功
        self.mock_client.get_file.side_effect = [
            {"content": "{}", "sha": "old_sha"},   # 第一次读取
            {"content": "{}", "sha": "new_sha"},   # 重试时读取（假设有人同时修改）
        ]
        self.mock_client.update_file.side_effect = [False, True]  # 第一次失败，第二次成功

        self.sync.queue_event(make_history_event("user_001", "m1", "ADD"))
        self.sync._flush()

        # 验证 get_file 被调用 2 次（初始 + 重试）
        self.assertEqual(self.mock_client.get_file.call_count, 2)

    def test_retry_then_drop(self):
        """重试 max_retries 次后丢弃事件。"""
        self.sync.max_retries = 2
        self.mock_client.get_file.return_value = None
        self.mock_client.create_file.return_value = False  # 始终失败

        ev = make_history_event("user_001", "m1", "ADD")
        self.sync.queue_event(ev)
        self.sync._flush()

        # 第一次 flush 失败，事件放回队列（retry=1）
        self.sync._flush()
        # 第二次 flush 失败，事件放回队列（retry=2）
        self.sync._flush()
        # 第三次 flush，事件被丢弃（retry=2 >= max_retries=2）
        self.sync._flush()

        # 验证 create_file 被调用 3 次（flush 3 次都调用，第三次失败后丢弃）
        self.assertEqual(self.mock_client.create_file.call_count, 3)
        # 队列最终为空
        self.assertEqual(len(self.sync._queue), 0)

    def test_memory_event_merge(self):
        """memory 类型事件合并到 active.json。"""
        self.mock_client.get_file.return_value = None
        self.sync.queue_event(make_memory_event("user_001", "mem1", "data1"))
        self.sync.queue_event(make_memory_event("user_001", "mem2", "data2"))
        self.sync._flush()

        self.assertTrue(self.mock_client.create_file.called)
        call_args = self.mock_client.create_file.call_args
        # 验证写入的内容是 JSON 格式，包含两条 memory
        # create_file 是位置参数调用: (path, content, message)
        content = call_args[0][1]
        data = json.loads(content)
        self.assertIn("mem1", data)
        self.assertIn("mem2", data)

    def test_start_stop_thread(self):
        """启动和停止后台线程。"""
        self.sync.start()
        self.assertTrue(self.sync._thread.is_alive())

        self.sync.stop()
        self.assertFalse(self.sync._thread.is_alive())


class TestSyncEvent(unittest.TestCase):
    """测试 SyncEvent 数据模型。"""

    def test_to_dict(self):
        ev = SyncEvent("history", "user_001", {"memory_id": "m1"})
        d = ev.to_dict()
        self.assertEqual(d["event_type"], "history")
        self.assertEqual(d["user_id"], "user_001")
        self.assertEqual(d["payload"]["memory_id"], "m1")
        self.assertIn("timestamp", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)

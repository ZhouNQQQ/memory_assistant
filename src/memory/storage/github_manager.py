"""
GitHubSyncManager — 将本地记忆事件同步到 GitHub 仓库。

设计原则：
- 本地优先：所有写操作先完成本地 SQLite/Chroma，再异步同步到 GitHub
- 批量缓冲：事件进入队列，达 batch_size 或定时触发时批量提交
- 冲突处理：基于 GitHub Contents API 的 sha 版本控制，last-write-wins
- 容错：同步失败不影响本地操作，支持指数退避重试
"""

import json
import logging
import os
import threading
import time
import base64
from datetime import datetime, timezone
from io import StringIO
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────────────────────────
DEFAULT_SYNC_INTERVAL = 300       # 5 分钟
DEFAULT_BATCH_SIZE = 20
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 2.0       # 指数退避基数（秒）
GITHUB_API_BASE = "https://api.github.com"


# ──────────────────────────────────────────────────────────────────
# 数据模型：同步事件
# ──────────────────────────────────────────────────────────────────
class SyncEvent:
    """一条待同步的记忆事件。"""

    def __init__(
        self,
        event_type: str,           # "history" | "memory" | "entity" | "profile" | "compact"
        user_id: str,
        payload: Dict,
        timestamp: Optional[str] = None,
    ):
        self.event_type = event_type
        self.user_id = user_id
        self.payload = payload
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "user_id": self.user_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    def __repr__(self):
        return f"SyncEvent({self.event_type}, user={self.user_id})"


# ──────────────────────────────────────────────────────────────────
# GitHub Contents API 封装
# ──────────────────────────────────────────────────────────────────
class GitHubClient:
    """轻量级 GitHub Contents API 客户端。"""

    def __init__(self, repo: str, token: str, branch: str = "main"):
        self.repo = repo
        self.token = token
        self.branch = branch
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "KimiClaw-Memory/1.0",
        }

    def _url(self, path: str) -> str:
        return f"{GITHUB_API_BASE}/repos/{self.repo}/contents/{path}"

    def get_file(self, path: str) -> Optional[Dict]:
        """
        获取文件内容和 metadata。
        Returns: {"content": str, "sha": str, "size": int} or None
        """
        url = self._url(path)
        params = {"ref": self.branch}
        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            # GitHub 返回 base64 编码的内容
            content_b64 = data.get("content", "")
            content = base64.b64decode(content_b64.replace("\n", "")).decode("utf-8")
            return {
                "content": content,
                "sha": data["sha"],
                "size": data.get("size", 0),
            }
        except Exception as e:
            logger.warning(f"GitHub get_file failed for {path}: {e}")
            return None

    def create_file(self, path: str, content: str, message: str) -> bool:
        """创建新文件。"""
        url = self._url(path)
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": self.branch,
        }
        try:
            resp = requests.put(url, headers=self.headers, json=payload, timeout=30)
            resp.raise_for_status()
            logger.info(f"GitHub created: {path}")
            return True
        except Exception as e:
            logger.error(f"GitHub create_file failed for {path}: {e}")
            return False

    def update_file(self, path: str, content: str, sha: str, message: str) -> bool:
        """更新现有文件（需 sha 防冲突）。"""
        url = self._url(path)
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "sha": sha,
            "branch": self.branch,
        }
        try:
            resp = requests.put(url, headers=self.headers, json=payload, timeout=30)
            if resp.status_code == 409:
                logger.warning(f"GitHub conflict (409) for {path} — sha stale")
                return False
            resp.raise_for_status()
            logger.info(f"GitHub updated: {path}")
            return True
        except Exception as e:
            logger.error(f"GitHub update_file failed for {path}: {e}")
            return False

    def delete_file(self, path: str, sha: str, message: str) -> bool:
        """删除文件。"""
        url = self._url(path)
        payload = {
            "message": message,
            "sha": sha,
            "branch": self.branch,
        }
        try:
            resp = requests.delete(url, headers=self.headers, json=payload, timeout=30)
            resp.raise_for_status()
            logger.info(f"GitHub deleted: {path}")
            return True
        except Exception as e:
            logger.error(f"GitHub delete_file failed for {path}: {e}")
            return False


# ──────────────────────────────────────────────────────────────────
# GitHub 同步管理器
# ──────────────────────────────────────────────────────────────────
class GitHubSyncManager:
    """
    将本地记忆事件批量同步到 GitHub 仓库。

    文件结构（与方案文档一致）：
        users/{user_id_hash}/
            memories/active.json
            memories/archive.json
            history/{yyyy-mm}.jsonl
            entities/graph.json
            profile.json
    """

    def __init__(
        self,
        repo: str,
        token: str,
        branch: str = "main",
        sync_interval: int = DEFAULT_SYNC_INTERVAL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.client = GitHubClient(repo, token, branch)
        self.sync_interval = sync_interval
        self.batch_size = batch_size
        self.max_retries = max_retries

        # 线程安全队列
        self._queue: List[SyncEvent] = []
        self._lock = threading.Lock()

        # 后台线程控制
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 重试退避计数器
        self._retry_count = 0

    # ──────────────────────────────
    # 生命周期
    # ──────────────────────────────
    def start(self):
        """启动后台同步线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("GitHubSyncManager started")

    def stop(self):
        """停止后台同步线程（触发最后一次 flush）。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        # 停止前强制 flush 剩余队列
        self._flush()
        logger.info("GitHubSyncManager stopped")

    def _run_loop(self):
        """后台循环：定时 flush。"""
        while not self._stop_event.is_set():
            # 等待 sync_interval 或直到 stop 被调用
            self._stop_event.wait(timeout=self.sync_interval)
            if not self._queue:
                continue
            self._flush()

    # ──────────────────────────────
    # 队列操作
    # ──────────────────────────────
    def queue_event(self, event: SyncEvent):
        """将事件加入同步队列。"""
        with self._lock:
            self._queue.append(event)
            # 如果队列达到 batch_size，尝试立即 flush（在后台线程中）
            should_flush = len(self._queue) >= self.batch_size

        if should_flush:
            # 触发后台线程立即唤醒（通过新建线程执行 flush，避免阻塞调用方）
            threading.Thread(target=self._flush, daemon=True).start()

    # ──────────────────────────────
    # 批量 flush（核心）
    # ──────────────────────────────
    def _flush(self):
        """
        将队列中的事件批量写入 GitHub。
        按 user_id 分组，每组合并写入对应文件。
        """
        with self._lock:
            if not self._queue:
                return
            batch = self._queue[: self.batch_size]
            self._queue = self._queue[self.batch_size :]

        # 按 user_id + event_type 分组
        groups: Dict[str, Dict[str, List[SyncEvent]]] = {}
        for ev in batch:
            uid = ev.user_id
            groups.setdefault(uid, {}).setdefault(ev.event_type, []).append(ev)

        # 逐组处理
        for user_id, type_events in groups.items():
            for event_type, events in type_events.items():
                try:
                    self._write_events(user_id, event_type, events)
                except Exception as e:
                    logger.error(f"Flush failed for {user_id}/{event_type}: {e}")
                    # 失败的 events 放回队列尾部（避免无限重试，记录重试次数）
                    with self._lock:
                        for ev in events:
                            if getattr(ev, "_retry", 0) < self.max_retries:
                                ev._retry = getattr(ev, "_retry", 0) + 1
                                self._queue.append(ev)
                            else:
                                logger.warning(f"Event dropped after {self.max_retries} retries: {ev}")

    # ──────────────────────────────
    # 文件写入策略
    # ──────────────────────────────
    def _write_events(self, user_id: str, event_type: str, events: List[SyncEvent]):
        """将一组事件写入 GitHub 对应文件。"""
        if event_type == "history":
            self._append_history(user_id, events)
        elif event_type in ("memory", "entity", "profile"):
            self._merge_json_file(user_id, event_type, events)
        elif event_type == "compact":
            self._handle_compact(user_id, events)
        else:
            logger.warning(f"Unknown event_type: {event_type}")

    def _user_path(self, user_id: str, *paths: str) -> str:
        """生成 GitHub 文件路径：users/{user_id_hash}/..."""
        import hashlib
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        return "/".join(["users", user_hash, *paths])

    def _append_history(self, user_id: str, events: List[SyncEvent]):
        """将 history 事件追加到 {yyyy-mm}.jsonl。"""
        now = datetime.now(timezone.utc)
        file_path = self._user_path(user_id, "history", f"{now.year:04d}-{now.month:02d}.jsonl")

        # 构建追加内容
        lines = []
        for ev in events:
            line = json.dumps(ev.payload, ensure_ascii=False, separators=(",", ":"))
            lines.append(line)
        append_content = "\n".join(lines) + "\n"

        # 读取现有文件（如果有）
        existing = self.client.get_file(file_path)

        if existing is None:
            # 创建新文件
            content = append_content
            message = f"[KimiClaw] Create history {now.year:04d}-{now.month:02d} for user {user_id[:8]}"
            success = self.client.create_file(file_path, content, message)
            if not success:
                raise RuntimeError(f"Failed to create history file: {file_path}")
        else:
            # 追加内容（GitHub Contents API 不支持追加，需全量重写）
            new_content = existing["content"] + append_content
            message = f"[KimiClaw] Append {len(events)} history events for user {user_id[:8]}"
            success = self.client.update_file(file_path, new_content, existing["sha"], message)
            if not success:
                # 冲突：重新读取、合并、重试一次
                logger.info(f"Conflict detected for {file_path}, retrying merge...")
                existing = self.client.get_file(file_path)
                if existing:
                    new_content = existing["content"] + append_content
                    success = self.client.update_file(file_path, new_content, existing["sha"], message + " (retry)")
                if not success:
                    raise RuntimeError(f"Failed to update history file after retry: {file_path}")

    def _merge_json_file(self, user_id: str, event_type: str, events: List[SyncEvent]):
        """将 memory/entity/profile 事件合并到 JSON 文件。"""
        mapping = {
            "memory": ("memories", "active.json"),
            "entity": ("entities", "graph.json"),
            "profile": ("", "profile.json"),
        }
        if event_type not in mapping:
            return
        dir_name, file_name = mapping[event_type]
        file_path = self._user_path(user_id, dir_name, file_name) if dir_name else self._user_path(user_id, file_name)

        # 读取现有文件
        existing = self.client.get_file(file_path)
        data: Dict = {}
        sha: Optional[str] = None

        if existing:
            try:
                data = json.loads(existing["content"])
                sha = existing["sha"]
            except json.JSONDecodeError:
                data = {}

        # 合并事件到数据结构中
        for ev in events:
            payload = ev.payload
            if event_type == "memory":
                # payload: {"id": "xxx", "data": "...", "metadata": {...}}
                mem_id = payload.get("id")
                if mem_id:
                    data[mem_id] = payload
            elif event_type == "entity":
                # payload: {"entity": "name", "linked_memory_ids": [...]}
                entity_name = payload.get("entity")
                if entity_name:
                    data[entity_name] = payload
            elif event_type == "profile":
                # payload: 直接更新 profile 顶层字段
                data.update(payload)

        # 写入
        content = json.dumps(data, ensure_ascii=False, indent=2)
        message = f"[KimiClaw] Update {event_type} for user {user_id[:8]} ({len(events)} events)"

        if sha is None:
            success = self.client.create_file(file_path, content, message)
            if not success:
                raise RuntimeError(f"Failed to create {event_type} file: {file_path}")
        else:
            success = self.client.update_file(file_path, content, sha, message)
            if not success:
                # 冲突重试
                existing = self.client.get_file(file_path)
                if existing:
                    try:
                        data = json.loads(existing["content"])
                        for ev in events:
                            payload = ev.payload
                            if event_type == "memory":
                                mem_id = payload.get("id")
                                if mem_id:
                                    data[mem_id] = payload
                            elif event_type == "entity":
                                entity_name = payload.get("entity")
                                if entity_name:
                                    data[entity_name] = payload
                            elif event_type == "profile":
                                data.update(payload)
                    except json.JSONDecodeError:
                        pass
                    content = json.dumps(data, ensure_ascii=False, indent=2)
                    success = self.client.update_file(file_path, content, existing["sha"], message + " (retry)")
                if not success:
                    raise RuntimeError(f"Failed to update {event_type} file after retry: {file_path}")

    def _handle_compact(self, user_id: str, events: List[SyncEvent]):
        """处理 compaction 产生的归档事件（将 active 记忆移动到 archive）。"""
        for ev in events:
            payload = ev.payload
            archive_items = payload.get("archive_items", [])
            if not archive_items:
                continue

            # 读取 archive.json
            archive_path = self._user_path(user_id, "memories", "archive.json")
            existing = self.client.get_file(archive_path)
            archive_data: Dict = {}
            sha = None

            if existing:
                try:
                    archive_data = json.loads(existing["content"])
                    sha = existing["sha"]
                except json.JSONDecodeError:
                    archive_data = {}

            # 合并归档项
            for item in archive_items:
                mem_id = item.get("id")
                if mem_id:
                    archive_data[mem_id] = item

            content = json.dumps(archive_data, ensure_ascii=False, indent=2)
            message = f"[KimiClaw] Archive {len(archive_items)} memories for user {user_id[:8]}"

            if sha is None:
                self.client.create_file(archive_path, content, message)
            else:
                self.client.update_file(archive_path, content, sha, message)

    # ──────────────────────────────
    # 读取：从 GitHub 恢复
    # ──────────────────────────────
    def pull_memories(self, user_id: str) -> Optional[Dict]:
        """从 GitHub 拉取用户的活跃记忆。"""
        file_path = self._user_path(user_id, "memories", "active.json")
        existing = self.client.get_file(file_path)
        if existing:
            try:
                return json.loads(existing["content"])
            except json.JSONDecodeError:
                return {}
        return None

    def pull_history(self, user_id: str, year_month: Optional[str] = None) -> List[Dict]:
        """从 GitHub 拉取用户的历史事件（默认当月）。"""
        now = datetime.now(timezone.utc)
        ym = year_month or f"{now.year:04d}-{now.month:02d}"
        file_path = self._user_path(user_id, "history", f"{ym}.jsonl")
        existing = self.client.get_file(file_path)
        if not existing:
            return []
        records = []
        for line in existing["content"].strip().split("\n"):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def pull_profile(self, user_id: str) -> Optional[Dict]:
        """从 GitHub 拉取用户 profile（滚动摘要）。"""
        file_path = self._user_path(user_id, "profile.json")
        existing = self.client.get_file(file_path)
        if existing:
            try:
                return json.loads(existing["content"])
            except json.JSONDecodeError:
                return {}
        return None

    # ──────────────────────────────
    # 辅助：创建 tag（快照）
    # ──────────────────────────────
    def create_tag(self, tag_name: str, message: str = "KimiClaw snapshot") -> bool:
        """创建轻量级 tag（用于 compaction 前备份）。"""
        # 先获取当前 branch 的最新 commit sha
        url = f"{GITHUB_API_BASE}/repos/{self.client.repo}/git/ref/heads/{self.client.branch}"
        try:
            resp = requests.get(url, headers=self.client.headers, timeout=30)
            resp.raise_for_status()
            commit_sha = resp.json()["object"]["sha"]

            # 创建 tag object
            tag_url = f"{GITHUB_API_BASE}/repos/{self.client.repo}/git/tags"
            tag_payload = {
                "tag": tag_name,
                "message": message,
                "object": commit_sha,
                "type": "commit",
            }
            resp = requests.post(tag_url, headers=self.client.headers, json=tag_payload, timeout=30)
            resp.raise_for_status()
            tag_sha = resp.json()["sha"]

            # 创建 ref
            ref_url = f"{GITHUB_API_BASE}/repos/{self.client.repo}/git/refs"
            ref_payload = {"ref": f"refs/tags/{tag_name}", "sha": tag_sha}
            resp = requests.post(ref_url, headers=self.client.headers, json=ref_payload, timeout=30)
            resp.raise_for_status()
            logger.info(f"GitHub tag created: {tag_name}")
            return True
        except Exception as e:
            logger.error(f"GitHub create_tag failed: {e}")
            return False


# ──────────────────────────────────────────────────────────────────
# 便捷函数：直接构造事件
# ──────────────────────────────────────────────────────────────────
def make_history_event(user_id: str, memory_id: str, event: str,
                       old_memory=None, new_memory=None, **kwargs) -> SyncEvent:
    """构造一条 history 类型同步事件。"""
    payload = {
        "memory_id": memory_id,
        "event": event,           # ADD | UPDATE | DELETE
        "old_memory": old_memory,
        "new_memory": new_memory,
        **kwargs,
    }
    return SyncEvent("history", user_id, payload)


def make_memory_event(user_id: str, memory_id: str, data: str,
                      metadata: Optional[Dict] = None) -> SyncEvent:
    """构造一条 memory 类型同步事件（写入 active.json）。"""
    payload = {
        "id": memory_id,
        "data": data,
        "metadata": metadata or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return SyncEvent("memory", user_id, payload)

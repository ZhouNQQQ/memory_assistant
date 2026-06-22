"""
Compaction Engine — 记忆压缩、去重合并、时间衰减与滚动摘要。

设计原则：
- 安全：每次 compaction 前生成快照，失败可回滚
- 可配置：策略通过配置注入，支持不同场景调整参数
- 可观测：返回详细的 compaction 报告
- 与 GitHub 协同：归档记忆推送到 GitHub archive.json，摘要更新 profile.json
"""

import json
import logging
import math
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Callable

import numpy as np

from ..storage.github_manager import SyncEvent, GitHubSyncManager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 策略 1：时间衰减（TimeDecayStrategy）
# ──────────────────────────────────────────────────────────────────
class TimeDecayStrategy:
    """
    基于时间衰减的记忆优先级调整。

    原理：记忆得分 = 原始相似度 × 时间衰减系数
    衰减系数：decay = exp(-λ × days_since_last_update)
    当衰减系数低于阈值时，将记忆从 active 归档到 archive。
    """

    def __init__(self, half_life_days: float = 90.0, archive_threshold: float = 0.3):
        self.half_life_days = half_life_days
        self.archive_threshold = archive_threshold
        self.lambda_rate = math.log(2) / half_life_days

    def compute_decay(self, last_updated: datetime) -> float:
        """计算时间衰减系数（0.0 ~ 1.0）。"""
        days = (datetime.now(timezone.utc) - last_updated).total_seconds() / 86400
        return math.exp(-self.lambda_rate * max(days, 0))

    def should_archive(self, memory: dict) -> bool:
        """判断记忆是否应该归档。"""
        updated_at = memory.get("updated_at") or memory.get("created_at")
        if not updated_at:
            return False
        try:
            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            decay = self.compute_decay(dt)
            return decay < self.archive_threshold
        except (ValueError, TypeError):
            return False

    def apply(self, memories: List[dict]) -> Tuple[List[dict], List[dict]]:
        """
        返回：(保留列表, 归档列表)
        同时更新保留记忆的 metadata.decay_score。
        """
        active, archive = [], []
        for mem in memories:
            if self.should_archive(mem):
                archive.append(mem)
            else:
                updated_at = mem.get("updated_at") or mem.get("created_at")
                if updated_at:
                    try:
                        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        mem.setdefault("metadata", {})
                        mem["metadata"]["decay_score"] = round(self.compute_decay(dt), 4)
                    except (ValueError, TypeError):
                        pass
                active.append(mem)
        return active, archive


# ──────────────────────────────────────────────────────────────────
# 策略 2：去重合并（DeduplicationStrategy）
# ──────────────────────────────────────────────────────────────────
class DeduplicationStrategy:
    """
    合并语义相似的记忆条目。

    触发条件：同一 user_id 下，多条记忆 cosine 相似度 > threshold。
    合并方式：保留信息最丰富的一条，将其他作为 merged_from 历史记录。
    """

    def __init__(self, similarity_threshold: float = 0.92):
        self.similarity_threshold = similarity_threshold

    def find_duplicate_groups(self, memories: List[dict], embeddings: List[List[float]]) -> List[List[int]]:
        """
        返回相似记忆的分组索引列表。
        例如 [[0, 2], [1, 3]] 表示第 0 条和第 2 条相似，第 1 条和第 3 条相似。
        """
        if len(memories) < 2 or len(embeddings) < 2:
            return []

        # 计算余弦相似度矩阵
        from numpy import dot
        from numpy.linalg import norm

        # 归一化向量
        norms = np.array([norm(e) for e in embeddings])
        norms[norms == 0] = 1  # 避免除零
        normalized = np.array(embeddings) / norms[:, np.newaxis]

        sim_matrix = np.dot(normalized, normalized.T)

        # 贪心分组：找到所有相似对，合并传递闭包
        groups = []
        visited = set()

        for i in range(len(memories)):
            if i in visited:
                continue
            group = [i]
            for j in range(i + 1, len(memories)):
                if j in visited:
                    continue
                if sim_matrix[i][j] > self.similarity_threshold:
                    group.append(j)
                    visited.add(j)
            if len(group) > 1:
                groups.append(group)
            visited.add(i)

        return groups

    def merge_group(self, group_memories: List[dict]) -> dict:
        """合并一组相似记忆，保留信息最丰富的一条。"""
        # 策略：保留字数最多的一条作为主记忆
        primary = max(group_memories, key=lambda m: len(m.get("data", "")))

        merged = dict(primary)
        merged.setdefault("metadata", {})
        merged["metadata"]["merged_from"] = [m.get("id") for m in group_memories if m.get("id") != primary.get("id")]
        merged["metadata"]["merged_count"] = len(group_memories)
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()

        return merged

    def apply(self, memories: List[dict], embeddings: List[List[float]]) -> Tuple[List[dict], int]:
        """
        对记忆列表执行去重合并。
        返回：(合并后的记忆列表, 合并掉的记忆数量)
        """
        groups = self.find_duplicate_groups(memories, embeddings)
        if not groups:
            return memories, 0

        # 标记需要删除的索引
        to_remove = set()
        merged_count = 0

        for group in groups:
            group_mems = [memories[i] for i in group]
            merged = self.merge_group(group_mems)

            # 更新主记忆（group 第一个元素）
            main_idx = group[0]
            memories[main_idx] = merged

            # 标记其余为删除
            for idx in group[1:]:
                to_remove.add(idx)
                merged_count += 1

        # 构建新列表，跳过被合并的
        result = [m for i, m in enumerate(memories) if i not in to_remove]
        return result, merged_count


# ──────────────────────────────────────────────────────────────────
# 策略 3：滚动摘要（SummarizationStrategy）
# ──────────────────────────────────────────────────────────────────
class SummarizationStrategy:
    """
    对大量碎片化记忆进行 LLM 摘要，生成高层级滚动摘要。

    适用场景：用户已积累数百条记忆，检索时 top_k 只能覆盖最近活跃的。
    滚动摘要提供一个"用户画像快照"，在检索前注入 LLM 上下文。
    """

    def __init__(self, max_memories: int = 100, max_summary_length: int = 800):
        self.max_memories = max_memories
        self.max_summary_length = max_summary_length

    def _format_prompt(self, memories: List[dict]) -> str:
        """构建 LLM 摘要 prompt。"""
        # 按类别分组，每组最多取 10 条
        categories = defaultdict(list)
        for mem in memories:
            cat = mem.get("metadata", {}).get("category", "misc")
            categories[cat].append(mem.get("data", ""))

        # 截断每类条目
        category_text = {}
        for cat, items in categories.items():
            category_text[cat] = items[:10]

        prompt = f"""基于以下用户的结构化记忆，生成一份简洁的用户画像摘要（不超过{self.max_summary_length}字）。
按类别组织：偏好、个人详情、计划、职业、健康等。
保留具体名称和日期，不要泛泛而谈。不要添加你未在记忆中找到的信息。

{json.dumps(category_text, ensure_ascii=False, indent=2)}

请输出纯文本摘要，不要 JSON："""
        return prompt

    def generate_summary(self, memories: List[dict], llm_generate: Callable[[str], str]) -> str:
        """
        生成滚动摘要。

        Args:
            memories: 活跃记忆列表（已按时间排序）
            llm_generate: 调用 LLM 的函数，接收 prompt 返回 str
        """
        if not memories:
            return ""

        # 取最近 N 条记忆生成摘要
        recent = memories[-self.max_memories:] if len(memories) > self.max_memories else memories
        prompt = self._format_prompt(recent)

        try:
            summary = llm_generate(prompt)
            return summary.strip()
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return ""


# ──────────────────────────────────────────────────────────────────
# Compaction Engine 主流程
# ──────────────────────────────────────────────────────────────────
class CompactionEngine:
    """
    Compaction 执行引擎：协调多种策略，安全地压缩记忆存储。
    """

    def __init__(
        self,
        memory_instance,
        config: dict,
        github_sync: Optional[GitHubSyncManager] = None,
    ):
        self.memory = memory_instance
        self.github_sync = github_sync

        # 解析配置
        self.half_life_days = config.get("half_life_days", 90)
        self.archive_threshold = config.get("archive_threshold", 0.3)
        self.similarity_threshold = config.get("similarity_threshold", 0.92)
        self.keep_history = config.get("keep_history", 1000)
        self.max_memories_for_summary = config.get("max_memories_for_summary", 100)

        # 初始化策略
        self.time_strategy = TimeDecayStrategy(self.half_life_days, self.archive_threshold)
        self.dedup_strategy = DeduplicationStrategy(self.similarity_threshold)
        self.summary_strategy = SummarizationStrategy(self.max_memories_for_summary)

        self._is_running = False

    # ──────────────────────────────
    # 主入口
    # ──────────────────────────────
    def compact(self, user_id: Optional[str] = None, dry_run: bool = False) -> dict:
        """
        执行一次 Compaction。

        Returns:
            报告：{
                "archived": N,
                "merged": M,
                "deleted": K,
                "summary_generated": bool,
                "summary_length": int,
                "before_count": int,
                "after_count": int,
                "errors": list,
            }
        """
        if self._is_running:
            raise RuntimeError("Compaction already in progress")

        self._is_running = True
        report = {
            "archived": 0,
            "merged": 0,
            "deleted": 0,
            "summary_generated": False,
            "summary_length": 0,
            "before_count": 0,
            "after_count": 0,
            "errors": [],
        }

        try:
            # 1. 获取所有记忆（通过 vector_store.list）
            filters = {"user_id": user_id} if user_id else None
            raw_results = self.memory.vector_store.list(filters=filters, top_k=10000)

            # 解析为统一格式（兼容不同 vector store 返回格式）
            all_memories = self._parse_list_results(raw_results)
            report["before_count"] = len(all_memories)

            if not all_memories:
                return report

            # 2. 创建快照（备份）
            if not dry_run:
                self._create_snapshot(user_id)

            # 3. 时间衰减 → 分离活跃 / 归档
            active, archive = self.time_strategy.apply(all_memories)
            report["archived"] = len(archive)

            # 4. 去重合并（在 active 中执行）
            if len(active) >= 2 and not dry_run:
                try:
                    # 获取 embeddings 用于相似度计算
                    embeddings = []
                    for m in active:
                        text = m.get("data", "")
                        emb = self.memory.embedding_model.embed(text, "search")
                        embeddings.append(emb)

                    active, merged_count = self.dedup_strategy.apply(active, embeddings)
                    report["merged"] = merged_count

                    # 从向量存储中删除被合并的条目
                    self._delete_merged_memories(all_memories, active, user_id)
                except Exception as e:
                    report["errors"].append(f"Dedup failed: {e}")
                    logger.warning(f"Deduplication failed: {e}")

            # 5. 更新向量存储中的衰减分数（如果 active 中有变更）
            if not dry_run:
                self._update_decay_scores(active)

            # 6. 生成滚动摘要
            try:
                llm_generate = self.memory.llm.generate_response
                summary = self.summary_strategy.generate_summary(active, llm_generate)
                if summary:
                    report["summary_generated"] = True
                    report["summary_length"] = len(summary)
                    # 更新到 profile（通过 GitHub 同步）
                    if self.github_sync and not dry_run:
                        self.github_sync.queue_event(SyncEvent(
                            "profile", user_id or "default",
                            {"summary": summary, "updated_at": datetime.now(timezone.utc).isoformat()}
                        ))
            except Exception as e:
                report["errors"].append(f"Summary failed: {e}")

            # 7. 将归档记忆推送到 GitHub archive.json
            if archive and not dry_run and self.github_sync:
                try:
                    self.github_sync.queue_event(SyncEvent(
                        "compact", user_id or "default",
                        {"archive_items": archive}
                    ))
                except Exception as e:
                    report["errors"].append(f"Archive sync failed: {e}")

            # 8. 清理 SQLite 历史表（保留最近 N 条）
            if not dry_run:
                self._trim_history_table(user_id, keep=self.keep_history)

            report["after_count"] = len(active)

        except Exception as e:
            report["errors"].append(str(e))
            logger.error(f"Compaction failed: {e}")
        finally:
            self._is_running = False

        return report

    # ──────────────────────────────
    # 辅助方法
    # ──────────────────────────────
    def _parse_list_results(self, raw_results) -> List[dict]:
        """
        统一解析 vector_store.list() 的返回结果。
        不同 vector store 返回格式不同（Chroma 返回 list[OutputData]，Qdrant 返回 list[ScoredPoint]）。
        """
        memories = []
        if raw_results is None:
            return memories

        # Chroma 返回 [list[OutputData]] 或 list[OutputData]
        if isinstance(raw_results, list):
            # 可能是嵌套列表（Chroma 的 list() 返回 [results]）
            items = raw_results
            if items and isinstance(items[0], list):
                items = items[0]

            for item in items:
                if hasattr(item, "payload") and item.payload:
                    payload = dict(item.payload)
                    payload["id"] = getattr(item, "id", payload.get("id"))
                    memories.append(payload)
                elif isinstance(item, dict):
                    # 如果 dict 有 payload，将其内容提升到顶层
                    if "payload" in item and isinstance(item["payload"], dict):
                        merged = dict(item["payload"])
                        merged["id"] = item.get("id", merged.get("id"))
                        memories.append(merged)
                    else:
                        memories.append(item)

        return memories

    def _create_snapshot(self, user_id: Optional[str]):
        """创建 compaction 前快照。"""
        try:
            # 本地：SQLite 备份
            db_path = self.memory.config.history_db_path
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_path = f"{db_path}.{timestamp}.bak"
            shutil.copy(db_path, backup_path)
            logger.info(f"SQLite snapshot created: {backup_path}")

            # GitHub：创建 tag（轻量级）
            if self.github_sync:
                tag_name = f"pre-compact-{user_id or 'all'}-{timestamp}"
                self.github_sync.create_tag(tag_name, "KimiClaw compaction snapshot")
        except Exception as e:
            logger.warning(f"Snapshot creation failed: {e}")

    def _delete_merged_memories(self, original: List[dict], merged: List[dict], user_id: Optional[str]):
        """从向量存储中删除被合并的记忆条目。"""
        merged_ids = {m.get("id") for m in merged}
        for mem in original:
            mid = mem.get("id")
            if mid and mid not in merged_ids:
                try:
                    self.memory.vector_store.delete(vector_id=mid)
                    logger.debug(f"Deleted merged memory: {mid}")
                except Exception as e:
                    logger.warning(f"Failed to delete memory {mid}: {e}")

    def _update_decay_scores(self, memories: List[dict]):
        """将衰减分数写回向量存储的 metadata。"""
        for mem in memories:
            mid = mem.get("id")
            if mid and "decay_score" in mem.get("metadata", {}):
                try:
                    self.memory.vector_store.update(
                        vector_id=mid,
                        payload=mem.get("metadata", {})
                    )
                except Exception as e:
                    logger.debug(f"Failed to update decay score for {mid}: {e}")

    def _trim_history_table(self, user_id: Optional[str], keep: int = 1000):
        """清理 SQLite 历史表，保留最近 N 条。"""
        try:
            # 获取所有 memory_id 列表
            # 由于 SQLiteManager 没有直接暴露 trim 方法，我们直接执行 SQL
            conn = self.memory.db.connection
            cursor = conn.cursor()

            if user_id:
                # 获取该用户的所有 memory_id（从 history 表）
                cursor.execute(
                    "SELECT memory_id FROM history WHERE memory_id IN "
                    "(SELECT id FROM history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?)",
                    (user_id, keep)
                )
            else:
                cursor.execute(
                    "SELECT memory_id FROM history ORDER BY created_at DESC LIMIT ?",
                    (keep,)
                )
            keep_ids = {row[0] for row in cursor.fetchall()}

            if not keep_ids:
                return

            # 删除不在保留列表中的旧记录（只删除已软删除的或非常旧的）
            # 保守策略：只删除 is_deleted=1 且不在最近 keep 条中的
            cursor.execute(
                "DELETE FROM history WHERE is_deleted = 1 AND id NOT IN "
                "(SELECT id FROM history ORDER BY created_at DESC LIMIT ?)",
                (keep,)
            )
            conn.commit()
            logger.info(f"Trimmed history table, kept last {keep} records")
        except Exception as e:
            logger.warning(f"History trim failed: {e}")

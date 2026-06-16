# KimiClaw-Memory 自定义 mem0 架构方案

> 目标：基于 mem0 核心架构，为 KimiClaw 构建一套可自定义存储位置（GitHub 后端）、支持自定义 Compaction 的记忆系统。
> 调研日期：2026-06-16
> 基于 mem0 源码版本：`mem0ai` 包（`~/IdeaProjects/mem0-source`）

---

## 一、mem0 核心架构拆解

### 1.1 核心类关系图

```
Memory (mem0/memory/main.py)
├── config: MemoryConfig          # 配置总入口
│   ├── vector_store: VectorStoreConfig   # 向量存储配置（provider + config）
│   ├── llm: LlmConfig                    # LLM 配置
│   ├── embedder: EmbedderConfig          # 嵌入模型配置
│   ├── reranker: RerankerConfig        # 重排序配置（可选）
│   ├── history_db_path: str            # SQLite 历史数据库路径
│   └── custom_instructions: str         # 自定义提取指令
│
├── embedding_model               # EmbedderFactory.create() 产出
├── vector_store                  # VectorStoreFactory.create() 产出 → 继承 VectorStoreBase
├── llm                           # LlmFactory.create() 产出
├── db: SQLiteManager             # 历史记录存储（add/update/delete 历史）
├── reranker                      # RerankerFactory.create() 产出（可选）
└── entity_store                  # 实体关联存储（懒加载，复用 vector_store）
```

### 1.2 关键抽象基类与工厂

| 组件 | 抽象基类 | 工厂 | 注册方式 |
|------|---------|------|---------|
| 向量存储 | `VectorStoreBase` (`mem0/vector_stores/base.py`) | `VectorStoreFactory` (`mem0/utils/factory.py`) | `provider_to_class` 字典映射 |
| LLM | 无统一基类 | `LlmFactory` | `provider_to_class` 字典映射 + 配置类 |
| 嵌入模型 | `BaseEmbedder` | `EmbedderFactory` | `provider_to_class` 字典映射 |
| 重排序 | `BaseReranker` | `RerankerFactory` | `provider_to_class` 字典映射 + 配置类 |

### 1.3 存储层现状（mem0 原生）

```
┌─────────────────────────────────────────────────────────┐
│                     Memory 实例                            │
├─────────────────────────────────────────────────────────┤
│  向量存储层：VectorStoreBase 实现（Qdrant/FAISS/Chroma等）│  ← 可替换 ✓
│  历史存储层：SQLiteManager（~/.mem0/history.db）          │  ← 固定，不可替换 ✗
│  配置存储：~/.mem0/config.json                            │  ← 固定，不可替换 ✗
│  消息缓存：SQLite messages 表（最近10条）                │  ← 固定，不可替换 ✗
└─────────────────────────────────────────────────────────┘
```

### 1.4 核心流水线（`add()` 方法）

```
Phase 0: 上下文收集
  └── 从 SQLite 读取最近 10 条消息（session_scope）

Phase 1: 现有记忆检索
  └── 用 embedding 搜索 vector_store，取 top 10 现有记忆

Phase 2: LLM 提取（单次调用）
  └── ADDITIVE_EXTRACTION_PROMPT → 从对话中提取新记忆

Phase 3: 批量嵌入
  └── embed_batch() 所有提取的记忆文本

Phase 4-5: 去重与哈希
  └── MD5 哈希去重（现有 + 本批次）

Phase 6: 批量持久化
  └── vector_store.insert() + SQLite batch_add_history()

Phase 7: 实体关联
  └── extract_entities_batch() → entity_store 批量插入/更新

Phase 8: 保存消息
  └── SQLite save_messages()（只保留最近10条）
```

**关键结论**：mem0 原生没有自动 Compaction 机制。`history.db` 只记录 ADD/UPDATE/DELETE 事件，不做自动压缩或时间衰减。

---

## 二、自定义存储方案：GitHub 后端

### 2.1 设计原则

GitHub 作为存储后端，有以下特性需要适配：

| GitHub 特性 | 对记忆系统的影响 | 设计策略 |
|------------|----------------|---------|
| 无向量检索能力 | 不能做实时向量搜索 | 向量存储保留本地（Qdrant/FAISS），GitHub 做冷备/同步 |
| 文件级操作 | 适合存储结构化文本 | 记忆内容以 Markdown/JSON 序列化到 GitHub |
| Git 版本历史 | 天然变更追踪 | 替代 SQLite history 表，用 commit log 做审计 |
| API 速率限制 | 不能高频写入 | 批量同步 + 本地缓存 + 定时/事件触发推送 |
| 公开/私有仓库 | 数据隐私可控 | 支持私有仓库 + 加密敏感字段 |

### 2.2 架构：分层混合存储

```
┌────────────────────────────────────────────────────────────────────┐
│                         KimiClaw Memory 层                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐        │
│  │   热数据层    │    │   温数据层    │    │   冷数据层    │        │
│  │  (本地内存)   │    │  (本地磁盘)   │    │  (GitHub 远端)│        │
│  └──────────────┘    └──────────────┘    └──────────────┘        │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │  向量存储（本地 Qdrant/FAISS）                             │     │
│  │  ├── 实时语义搜索（top_k + 过滤）                          │     │
│  │  ├── 实体关联索引                                         │     │
│  │  └── 支持 BM25 混合检索（Qdrant）                         │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │  结构化存储（本地 SQLite）                                 │     │
│  │  ├── messages 表（最近10条，缓存）                          │     │
│  │  └── 可替换为 GitHub 写回前的本地缓冲                       │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │  GitHub 后端（远程持久化）                                  │     │
│  │  ├── 记忆内容：memories.json（按 user_id 分片）             │     │
│  │  ├── 历史审计：history.jsonl（ADD/UPDATE/DELETE 事件）      │     │
│  │  ├── 实体图谱：entities.json（实体关联）                    │     │
│  │  └── 配置覆盖：config.json（自定义配置）                    │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │  同步层（SyncManager）                                      │     │
│  │  ├── 定时同步：每 N 分钟或每 M 条变更触发                    │     │
│  │  ├── 批量合并：本地缓冲 → 批量 commit                       │     │
│  │  └── 冲突解决：last-write-wins + 时间戳版本                  │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 2.3 为什么向量存储不放在 GitHub？

| 场景 | 本地向量存储 | GitHub 存储向量 |
|------|------------|----------------|
| 实时搜索延迟 | < 100ms | > 500ms（API 往返） |
| 批量相似度计算 | 支持 | 不支持 |
| 增量更新 | 原生支持 | 需全量重写 |
| 存储空间 | 本地磁盘 | 仓库体积膨胀 |

**结论**：向量索引必须保留本地。GitHub 只存储**可序列化的内容数据**（记忆文本、metadata、历史事件、实体关系），作为持久化备份和跨设备同步源。

### 2.4 GitHub 存储结构设计

```
github-repo: kimi-claw-memory/
├── README.md                          # 仓库说明 + 索引
├── config/
│   └── global.json                    # 全局配置（加密密钥、同步策略）
├── users/
│   └── {user_id_hash}/
│       ├── profile.json               # 用户摘要（姓名、偏好概览）
│       ├── memories/
│       │   ├── active.json            # 当前活跃记忆（去重后）
│       │   └── archive.json           # 已归档记忆（Compaction 后）
│       ├── history/
│       │   └── {yyyy-mm}.jsonl        # 按月分片的历史事件
│       ├── entities/
│       │   └── graph.json             # 实体关联图（linked_memory_ids）
│       └── sessions/
│           └── {session_id}.json      # 会话级临时上下文（可选）
```

### 2.5 实现路径：自定义存储适配层

mem0 原生不支持直接替换 `SQLiteManager`，但可以通过以下两种方式实现：

#### 方案 A：包装 Memory 类（推荐，侵入性小）

```python
# 核心思路：继承 mem0.Memory，重写写入方法，在写入本地后同步到 GitHub

from mem0.memory.main import Memory as Mem0Memory
from mem0.configs.base import MemoryConfig

class KimiClawMemory(Mem0Memory):
    def __init__(self, config: MemoryConfig = None, github_config: dict = None):
        super().__init__(config or MemoryConfig())
        
        # 注入 GitHub 同步层
        self.sync_manager = GitHubSyncManager(github_config) if github_config else None
        
        # 替换 SQLiteManager 为自定义版本（或包装）
        # 注意：这里不能直接替换，因为 self.db 在父类 __init__ 中已创建
        # 策略：在父类初始化后，包装 db 的方法
        self._wrap_db()
    
    def _wrap_db(self):
        """包装 SQLiteManager 的写入方法，在本地写入后触发 GitHub 同步"""
        original_add_history = self.db.add_history
        original_batch_add_history = self.db.batch_add_history
        original_save_messages = self.db.save_messages
        
        def _sync_add_history(*args, **kwargs):
            result = original_add_history(*args, **kwargs)
            if self.sync_manager:
                self.sync_manager.queue_history_event(*args, **kwargs)
            return result
        
        def _sync_batch_add_history(records):
            result = original_batch_add_history(records)
            if self.sync_manager:
                for record in records:
                    self.sync_manager.queue_history_event(**record)
            return result
        
        self.db.add_history = _sync_add_history
        self.db.batch_add_history = _sync_batch_add_history
        self.db.save_messages = original_save_messages  # messages 可不同步
```

#### 方案 B：自定义 SQLiteManager 子类（更彻底，但需修改 mem0 源码）

```python
# 在 mem0/memory/storage.py 同级创建自定义存储

from mem0.memory.storage import SQLiteManager
import requests  # GitHub API

class GitHubSQLiteManager(SQLiteManager):
    """
    继承 SQLiteManager，保持本地 SQLite 缓存，
    同时通过 GitHub API 写入远程持久化。
    """
    def __init__(self, db_path: str = ":memory:", github_repo: str = None, 
                 github_token: str = None, branch: str = "main"):
        super().__init__(db_path)
        self.github_repo = github_repo
        self.github_token = github_token
        self.branch = branch
        self._pending_commits = []  # 缓冲队列
        self._sync_interval = 60    # 60秒同步一次
        self._start_background_sync()
    
    def add_history(self, memory_id, old_memory, new_memory, event, *, 
                    created_at=None, updated_at=None, is_deleted=0, 
                    actor_id=None, role=None):
        # 1. 本地 SQLite 写入（保持不变）
        super().add_history(...)
        
        # 2. 加入 GitHub 同步队列
        self._pending_commits.append({
            "type": "history",
            "memory_id": memory_id,
            "event": event,
            "old_memory": old_memory,
            "new_memory": new_memory,
            "created_at": created_at,
            ...
        })
    
    def _flush_to_github(self):
        """将缓冲队列批量写入 GitHub"""
        if not self._pending_commits:
            return
        
        # 使用 GitHub Contents API 或 GraphQL
        # 策略：读取当前文件 → 合并变更 → 提交新 commit
        ...
        self._pending_commits = []
```

### 2.6 同步策略（GitHubSyncManager）

```python
class GitHubSyncManager:
    """
    GitHub 同步管理器：处理本地记忆与 GitHub 仓库的双向同步。
    
    设计要点：
    - 写操作优先本地，异步批量同步到 GitHub
    - 读操作优先本地，启动时从 GitHub 恢复
    - 冲突解决：时间戳 + 版本号，last-write-wins
    """
    
    def __init__(self, repo: str, token: str, branch: str = "main",
                 sync_interval: int = 300, batch_size: int = 50):
        self.repo = repo
        self.token = token
        self.branch = branch
        self.sync_interval = sync_interval  # 秒
        self.batch_size = batch_size
        self._queue = []
        self._lock = threading.Lock()
        self._last_sync = 0
    
    def queue_memory_event(self, event_type: str, payload: dict):
        """将记忆变更加入同步队列"""
        with self._lock:
            self._queue.append({
                "event_type": event_type,  # ADD | UPDATE | DELETE | COMPACT
                "payload": payload,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": self._get_next_version(),
            })
            
            if len(self._queue) >= self.batch_size:
                self._flush()
    
    def _flush(self):
        """批量提交到 GitHub"""
        with self._lock:
            if not self._queue:
                return
            
            batch = self._queue[:self.batch_size]
            self._queue = self._queue[self.batch_size:]
        
        # 1. 获取当前文件内容（GitHub API）
        # 2. 合并 batch 中的变更（去重、排序）
        # 3. 生成新的 commit（使用 GitHub Contents API 的 sha 防冲突）
        # 4. 如果冲突（409），拉取最新内容重新合并
        
    def pull_from_github(self, user_id: str) -> dict:
        """从 GitHub 恢复用户记忆数据（启动时调用）"""
        # 读取 users/{user_id}/memories/active.json
        # 读取 users/{user_id}/history/*.jsonl
        # 合并到本地向量存储
        ...
```

---

## 三、自定义 Compaction 组件

### 3.1 为什么需要 Compaction？

mem0 原生设计问题：
- 每次对话都产生 ADD 事件，历史表无限增长
- 相似记忆反复 ADD（下游有 LLM 去重，但历史记录仍累积）
- 过时的 UPDATE/DELETE 历史占用空间，但极少被查询
- 没有时间衰减机制：3年前的记忆与昨天的记忆权重相同

### 3.2 Compaction 目标

| 目标 | 说明 |
|------|------|
| 空间压缩 | 合并冗余历史事件，减少存储体积 |
| 时间衰减 | 旧记忆降低检索优先级，或归档到冷存储 |
| 冲突消解 | 同一事实的多次 UPDATE 合并为最终状态 |
| 版本快照 | 定期生成只读快照，加速恢复 |

### 3.3 Compaction 架构设计

```
┌────────────────────────────────────────────────────────────────────┐
│                      Compaction Engine                              │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  触发条件（Trigger）                                                │
│  ├── 定时触发：每 N 天执行一次（cron）                              │
│  ├── 容量触发：历史记录超过 M 条时触发                              │
│  ├── 事件触发：启动时检测未完成的 compaction                        │
│  └── 手动触发：用户主动调用 compact()                               │
│                                                                    │
│  策略层（Strategy）                                                  │
│  ├── TimeDecayStrategy        # 时间衰减 + 归档                       │
│  ├── DeduplicationStrategy    # 相似记忆合并                          │
│  ├── SummarizationStrategy   # 聚类记忆摘要（LLM 生成滚动摘要）       │
│  └── ArchiveStrategy         # 冷数据迁移到 GitHub archive          │
│                                                                    │
│  执行层（Executor）                                                  │
│  ├── 读取：扫描本地 SQLite + 向量存储                               │
│  ├── 分析：计算记忆分数、相似度聚类、时间权重                         │
│  ├── 决策：标记保留/归档/删除                                       │
│  ├── 执行：向量存储 update/delete + 历史表 compact                  │
│  └── 同步：将变更推送到 GitHub（方案 A 的 SyncManager）             │
│                                                                    │
│  安全层（Safety）                                                    │
│  ├── 预执行备份：生成 compaction 前快照                               │
│  ├── 原子性：要么全成功，要么回滚（SQLite 事务）                     │
│  └── 可中断：支持长时间 compaction 的断点续传                        │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 3.4 具体 Compaction 策略

#### 策略 1：时间衰减（TimeDecayStrategy）

```python
class TimeDecayStrategy:
    """
    基于时间衰减的记忆优先级调整。
    
    原理：记忆得分 = 原始相似度 × 时间衰减系数
    衰减系数：decay = exp(-λ × days_since_last_update)
    
    当衰减系数低于阈值（如 0.3）时，将记忆从 active.json 移动到 archive.json。
    """
    
    HALF_LIFE_DAYS = 90   # 半衰期：90天
    ARCHIVE_THRESHOLD = 0.3
    
    def compute_decay(self, last_updated: datetime) -> float:
        days = (datetime.now(timezone.utc) - last_updated).days
        lambda_rate = math.log(2) / self.HALF_LIFE_DAYS
        return math.exp(-lambda_rate * days)
    
    def should_archive(self, memory: dict) -> bool:
        decay = self.compute_decay(memory["updated_at"])
        return decay < self.ARCHIVE_THRESHOLD
    
    def apply(self, memories: list[dict]) -> tuple[list, list]:
        """返回 (保留列表, 归档列表)"""
        active, archive = [], []
        for mem in memories:
            if self.should_archive(mem):
                archive.append(mem)
            else:
                # 更新 metadata 中的 decay_score（供检索时排序）
                mem["metadata"] = mem.get("metadata", {})
                mem["metadata"]["decay_score"] = self.compute_decay(mem["updated_at"])
                active.append(mem)
        return active, archive
```

#### 策略 2：去重合并（DeduplicationStrategy）

```python
class DeduplicationStrategy:
    """
    合并语义相似的记忆条目。
    
    触发条件：同一 user_id 下，多条记忆 cosine 相似度 > 0.92。
    合并方式：保留信息最丰富的一条，将其他作为历史版本。
    """
    
    SIMILARITY_THRESHOLD = 0.92
    
    def find_duplicates(self, memories: list[dict], embeddings: list[list[float]]) -> list[list[int]]:
        """返回相似记忆的分组索引"""
        from sklearn.metrics.pairwise import cosine_similarity
        
        sim_matrix = cosine_similarity(embeddings)
        groups = []
        visited = set()
        
        for i in range(len(memories)):
            if i in visited:
                continue
            group = [i]
            for j in range(i + 1, len(memories)):
                if sim_matrix[i][j] > self.SIMILARITY_THRESHOLD:
                    group.append(j)
                    visited.add(j)
            if len(group) > 1:
                groups.append(group)
            visited.add(i)
        
        return groups
    
    def merge_group(self, group_memories: list[dict]) -> dict:
        """合并一组相似记忆"""
        # 策略：保留字数最多的一条作为主记忆
        # 其他作为 linked_history_ids
        primary = max(group_memories, key=lambda m: len(m["data"]))
        
        merged = primary.copy()
        merged["metadata"] = merged.get("metadata", {})
        merged["metadata"]["merged_from"] = [m["id"] for m in group_memories if m["id"] != primary["id"]]
        merged["metadata"]["merged_count"] = len(group_memories)
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return merged
```

#### 策略 3：滚动摘要（SummarizationStrategy）

```python
class SummarizationStrategy:
    """
    对大量碎片化记忆进行 LLM 摘要，生成高层级滚动摘要。
    
    适用场景：用户已积累数百条记忆，检索时 top_k 只能覆盖最近活跃的。
    滚动摘要提供一个"用户画像快照"，在检索前注入 LLM 上下文。
    """
    
    def generate_rolling_summary(self, memories: list[dict], llm) -> str:
        """
        输入：用户所有活跃记忆（按时间排序）
        输出：结构化摘要（偏好、计划、关系、职业等）
        
        类似 mem0 的 "summary" 字段，但由 Compaction 引擎定期生成。
        """
        # 按类别分组
        categories = defaultdict(list)
        for mem in memories:
            cat = mem.get("metadata", {}).get("category", "misc")
            categories[cat].append(mem["data"])
        
        # 构建 prompt
        prompt = f"""
基于以下用户的结构化记忆，生成一份简洁的用户画像摘要（不超过500字）。
按类别组织：偏好、个人详情、计划、职业、健康等。
保留具体名称和日期，不要泛泛而谈。

{json.dumps({k: v[:10] for k, v in categories.items()}, ensure_ascii=False, indent=2)}
"""
        
        return llm.generate_response(prompt)
```

### 3.5 Compaction 执行流程

```python
class CompactionEngine:
    """
    Compaction 执行引擎：协调多种策略，安全地压缩记忆存储。
    """
    
    def __init__(self, memory_instance: KimiClawMemory, 
                 strategies: list = None,
                 github_sync: GitHubSyncManager = None):
        self.memory = memory_instance
        self.strategies = strategies or [
            TimeDecayStrategy(),
            DeduplicationStrategy(),
            SummarizationStrategy(),
        ]
        self.github_sync = github_sync
        self._is_running = False
    
    def compact(self, user_id: str = None, dry_run: bool = False) -> dict:
        """
        执行一次 Compaction。
        
        Returns:
            报告：{
                "archived": N,      # 归档数量
                "merged": M,        # 合并数量
                "deleted": K,       # 删除数量（过期）
                "summary_generated": bool,
                "before_size": int,  # 压缩前体积（字节）
                "after_size": int,   # 压缩后体积
            }
        """
        if self._is_running:
            raise RuntimeError("Compaction already in progress")
        
        self._is_running = True
        report = {"archived": 0, "merged": 0, "deleted": 0, 
                  "summary_generated": False, "errors": []}
        
        try:
            # 1. 获取所有记忆（通过 vector_store.list）
            filters = {"user_id": user_id} if user_id else None
            all_memories = self.memory.vector_store.list(filters=filters, top_k=10000)
            
            # 2. 备份（快照到 SQLite + GitHub tag）
            self._create_snapshot(user_id)
            
            # 3. 时间衰减
            time_strategy = next(s for s in self.strategies if isinstance(s, TimeDecayStrategy))
            active, archive = time_strategy.apply(all_memories)
            report["archived"] = len(archive)
            
            # 4. 去重合并（在 active 中执行）
            dedup_strategy = next(s for s in self.strategies if isinstance(s, DeduplicationStrategy))
            # 获取 embeddings 用于相似度计算
            embeddings = [self.memory.embedding_model.embed(m["data"], "search") for m in active]
            groups = dedup_strategy.find_duplicates(active, embeddings)
            
            for group in groups:
                group_mems = [active[i] for i in group]
                merged = dedup_strategy.merge_group(group_mems)
                # 更新向量存储：删除旧条目，插入合并后的条目
                for idx in group[1:]:  # 保留第一个，删除其余
                    self.memory.vector_store.delete(vector_id=active[idx]["id"])
                    report["merged"] += 1
            
            # 5. 生成滚动摘要
            summary_strategy = next(s for s in self.strategies if isinstance(s, SummarizationStrategy))
            summary = summary_strategy.generate_rolling_summary(active, self.memory.llm)
            report["summary_generated"] = True
            
            # 6. 将归档记忆写入 archive.json（通过 GitHub 同步）
            if self.github_sync and archive:
                self.github_sync.push_archive(user_id, archive)
            
            # 7. 将摘要写入 profile.json
            if self.github_sync:
                self.github_sync.update_profile(user_id, {"summary": summary})
            
            # 8. 清理 SQLite 历史表（保留最近 1000 条）
            self._trim_history_table(user_id, keep=1000)
            
        except Exception as e:
            report["errors"].append(str(e))
            # 回滚：从快照恢复（可选）
            # self._restore_from_snapshot(user_id)
        
        finally:
            self._is_running = False
        
        return report
    
    def _create_snapshot(self, user_id: str):
        """创建 compaction 前快照"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"pre-compact-{user_id or 'all'}-{timestamp}"
        
        # 本地：SQLite 备份
        import shutil
        shutil.copy(self.memory.config.history_db_path, 
                    f"{self.memory.config.history_db_path}.{snapshot_name}.bak")
        
        # GitHub：创建 tag（轻量级）
        if self.github_sync:
            self.github_sync.create_tag(snapshot_name)
```

---

## 四、完整实现路线图

### 4.1 目录结构（KimiClaw 项目）

```
kimiclaw-memory/
├── src/
│   ├── __init__.py
│   ├── extractor.py              # 现有：LLM 记忆提取
│   ├── memory/                    # 新增：mem0 定制层
│   │   ├── __init__.py
│   │   ├── kimi_claw_memory.py   # 包装 Memory 类（方案 A）
│   │   ├── storage/
│   │   │   ├── __init__.py
│   │   │   ├── github_manager.py # GitHub 同步管理器
│   │   │   └── sync_manager.py   # 通用同步层（可扩展 S3/其他）
│   │   ├── compaction/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py         # CompactionEngine
│   │   │   ├── strategies.py     # 各策略实现
│   │   │   └── scheduler.py      # 定时触发器（可选 cron）
│   │   └── vector_store/          # 可选：自定义向量存储
│   │       └── github_vector.py   # 不推荐实现，但可保留接口
│   ├── evaluate.py               # 现有
│   └── injector.py               # 新增：写入 USER.md 的适配器
├── config/
│   └── memory.yaml               # KimiClaw 记忆系统配置
├── tests/
│   ├── test_github_sync.py
│   ├── test_compaction.py
│   └── test_memory_pipeline.py
└── README.md
```

### 4.2 实施阶段

#### Phase 1：基础包装层（1-2天）

- [ ] 创建 `KimiClawMemory` 类，继承 `mem0.Memory`
- [ ] 实现 `GitHubSyncManager` 基础队列 + flush
- [ ] 实现 SQLite 写入方法的包装（add_history 钩子）
- [ ] 测试：本地 add() 后能看到 GitHub 队列中有事件

#### Phase 2：GitHub 后端完整实现（2-3天）

- [ ] 实现 GitHub Contents API 的 CRUD（token、repo、branch）
- [ ] 实现 `memories.json`、`history.jsonl` 的读写
- [ ] 实现启动时 `pull_from_github()` 恢复数据
- [ ] 实现批量同步 + 冲突处理（sha 版本控制）
- [ ] 测试：跨设备同步（模拟两次启动，验证数据一致性）

#### Phase 3：Compaction 组件（3-4天）

- [ ] 实现 `TimeDecayStrategy`（时间衰减计算 + 归档）
- [ ] 实现 `DeduplicationStrategy`（相似度聚类 + 合并）
- [ ] 实现 `SummarizationStrategy`（LLM 滚动摘要）
- [ ] 实现 `CompactionEngine` 主流程（备份 → 执行 → 提交）
- [ ] 接入定时触发（cron 或事件触发）
- [ ] 测试：构造 100 条模拟记忆，执行 compaction，验证压缩率

#### Phase 4：接入 QClaw（2-3天）

- [ ] 实现 `injector.py`：将记忆写入 `~/.qclaw/workspace/USER.md`
- [ ] 保持 USER.md 的分节格式（Name/Preferences/Notes/Context）
- [ ] 实现 SOUL.md 的偏好类记忆更新
- [ ] 端到端测试：对话 → 提取 → 决策 → 写入 USER.md → QClaw 加载

#### Phase 5：优化与治理（持续）

- [ ] 加密敏感字段（GitHub 仓库可能是私有，但再加一层）
- [ ] 增量同步优化（减少 API 调用）
- [ ] Compaction 性能优化（大批量时流式处理）
- [ ] 监控与告警（同步失败、compaction 异常）

### 4.3 关键配置示例

```yaml
# config/memory.yaml

memory:
  # 复用 mem0 原生配置结构
  vector_store:
    provider: qdrant
    config:
      collection_name: kimiclaw_memories
      embedding_model_dims: 1024  # 匹配使用的嵌入模型
      path: /tmp/kimiclaw/qdrant   # 本地向量存储路径
      on_disk: true
  
  llm:
    provider: openai
    config:
      model: glm-4-flash
      api_key: ${GLM_API_KEY}
      base_url: https://open.bigmodel.cn/api/paas/v4
  
  embedder:
    provider: openai
    config:
      model: embedding-3
      api_key: ${KIMI_API_KEY}
  
  # 自定义：GitHub 同步配置
  github_sync:
    enabled: true
    repo: "your-username/kimiclaw-memory"
    branch: "main"
    token: ${GITHUB_TOKEN}           # 或从 keychain 读取
    sync_interval: 300               # 5分钟
    batch_size: 20
    encrypt_sensitive: true          # 是否加密敏感字段
    
  # 自定义：Compaction 配置
  compaction:
    enabled: true
    schedule: "0 2 * * 0"           # 每周日凌晨2点
    half_life_days: 90              # 时间衰减半衰期
    archive_threshold: 0.3
    similarity_threshold: 0.92
    summary_model: glm-4-flash
    keep_history: 1000              # 保留最近 N 条历史
    
  # 自定义：QClaw 注入配置
  qclaw:
    user_md_path: ~/.qclaw/workspace/USER.md
    soul_md_path: ~/.qclaw/workspace/SOUL.md
    max_memories_in_md: 50          # USER.md 中保留的记忆条目上限
```

---

## 五、风险与注意事项

### 5.1 技术风险

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| GitHub API 速率限制 | 同步失败 | 批量缓冲 + 指数退避重试 + 本地优先 |
| 大仓库克隆慢 | 启动恢复慢 | 分片存储（按 user_id + 时间）+ 增量拉取 |
| Compaction 崩溃 | 数据丢失 | 事务 + 快照 + 可回滚 |
| 向量与文本不同步 | 检索异常 | 同步时原子提交（向量 + GitHub 同一事务）|
| 敏感信息泄露 | 隐私风险 | 私有仓库 + 字段级加密 + 避免 token 入 Git |

### 5.2 与 mem0 的兼容性

- **不修改 mem0 源码**：全部通过继承和包装实现，便于跟进 mem0 版本更新
- **保留 mem0 流水线**：Phase 0-8 的 V3 流水线保持原样，只在写入层增加钩子
- **可降级**：GitHub 同步失败时，回退为纯本地 mem0（SQLite + 本地向量存储）

### 5.3 用户记忆偏好（来自 vault memory）

> ⚠️ 用户拒绝在记忆系统中加入 `importance`、`entity`、`confidence` 等 LLM 自评元数据。
> 
> 影响：KimiClaw 的 metadata 中不存储这些字段。Compaction 策略基于客观指标（时间、相似度），而非 LLM 评分。

---

## 六、核心代码骨架

### 6.1 KimiClawMemory（主入口）

```python
# src/memory/kimi_claw_memory.py

from mem0.memory.main import Memory
from mem0.configs.base import MemoryConfig
from .storage.github_manager import GitHubSyncManager
from .compaction.engine import CompactionEngine

class KimiClawMemory(Memory):
    """
    KimiClaw 自定义记忆系统。
    
    继承 mem0.Memory，增加：
    1. GitHub 远程同步
    2. 自动 Compaction
    3. QClaw USER.md 注入适配
    """
    
    def __init__(self, config: dict = None):
        # 1. 解析自定义配置
        github_config = config.pop("github_sync", None) if config else None
        compaction_config = config.pop("compaction", None) if config else None
        
        # 2. 初始化 mem0 原生 Memory
        mem0_config = MemoryConfig(**config) if config else MemoryConfig()
        super().__init__(mem0_config)
        
        # 3. 注入 GitHub 同步层
        self.sync_manager = None
        if github_config and github_config.get("enabled"):
            self.sync_manager = GitHubSyncManager(**github_config)
            self._install_sync_hooks()
        
        # 4. 初始化 Compaction 引擎
        self.compaction_engine = None
        if compaction_config and compaction_config.get("enabled"):
            self.compaction_engine = CompactionEngine(
                memory_instance=self,
                config=compaction_config,
                github_sync=self.sync_manager,
            )
    
    def _install_sync_hooks(self):
        """在 SQLiteManager 的写入方法上安装同步钩子"""
        # 见上文方案 A 实现
        pass
    
    def add(self, messages, **kwargs):
        """重写 add，在 mem0 原生 add 后触发同步"""
        result = super().add(messages, **kwargs)
        # 同步由钩子自动处理，这里无需额外操作
        return result
    
    def compact(self, user_id: str = None, dry_run: bool = False):
        """手动触发 Compaction"""
        if not self.compaction_engine:
            raise RuntimeError("Compaction not enabled")
        return self.compaction_engine.compact(user_id=user_id, dry_run=dry_run)
```

### 6.2 使用示例

```python
from src.memory.kimi_claw_memory import KimiClawMemory

# 初始化
memory = KimiClawMemory({
    "vector_store": {
        "provider": "qdrant",
        "config": {"collection_name": "test", "embedding_model_dims": 1024, "path": "/tmp/qdrant"}
    },
    "llm": {
        "provider": "openai",
        "config": {"model": "glm-4-flash", "api_key": "xxx"}
    },
    "embedder": {
        "provider": "openai",
        "config": {"model": "embedding-3", "api_key": "xxx"}
    },
    "github_sync": {
        "enabled": True,
        "repo": "user/kimiclaw-memory",
        "token": "ghp_xxx",
        "sync_interval": 300,
    },
    "compaction": {
        "enabled": True,
        "half_life_days": 90,
        "schedule": "0 2 * * 0",
    }
})

# 添加记忆
memory.add("用户: 我叫张明，是一名 Java 架构师", user_id="user_001")

# 检索记忆
results = memory.search("用户的职业是什么？", user_id="user_001")

# 手动触发 Compaction
report = memory.compact(user_id="user_001")
print(f"归档: {report['archived']}, 合并: {report['merged']}")
```

---

## 七、总结

| 需求 | 方案 | 状态 |
|------|------|------|
| 自定义存储位置到 GitHub | 分层混合存储：本地向量 + 本地 SQLite + GitHub 远程文本同步 | 可设计完成，需实现 |
| 自定义 Compaction 组件 | 自研 CompactionEngine（时间衰减 + 去重合并 + 滚动摘要） | 可设计完成，需实现 |
| 基于 mem0 改造 | 继承 `Memory` 类，通过钩子注入同步层，不修改 mem0 源码 | 可行 |
| 对接 QClaw | 在写入层增加 `injector.py`，将记忆映射到 USER.md 分节 | 待 Phase 4 |

下一步建议：先实现 **Phase 1（基础包装层）** + **GitHub SyncManager 的队列与 flush**，验证从 mem0 到 GitHub 的数据通路后，再投入 Compaction 开发。

# 学习资料：Compaction + 时间衰减策略

> 来源：mem0 源码分析（`mem0-plugin/scripts/on_pre_compact.py`、`capture_compact_summary.py`）+ mem0 平台 API（`decay` 参数）+ 通用记忆系统理论
> 定位：理解记忆系统的"垃圾回收"机制，以及为什么必须做衰减，否则记忆会无限膨胀。

---

## 一、什么是 Compaction（记忆压缩/垃圾回收）

### 类比理解

| 系统 | 膨胀问题 | 治理机制 |
|------|----------|----------|
| **数据库** | 数据量增长、碎片化 | Compaction：合并小文件、删除 tombstone、重建索引 |
| **日志系统** | 日志无限增长 | 日志轮转 + 归档 + TTL |
| **缓存系统** | 缓存满后命中率下降 | LRU/LFU 淘汰 |
| **记忆系统** | 记忆无限累积、重复、过时 | Compaction：合并重复、删除过期、压缩冗余、衰减旧记忆权重 |

### 记忆系统如果不做 Compaction，会怎样？

```
场景：用户和 KimiClaw 聊了 1000 轮

Day 1: "我喜欢川菜"           → 记忆 A
Day 2: "我比较喜欢吃川菜"      → 记忆 B（和 A 语义重复）
Day 3: "川菜是我的最爱"        → 记忆 C（和 A/B 语义重复）
Day 30: "我最近改吃粤菜了"     → 记忆 D（A/B/C 已经过时，但还在库里）
Day 100: "我减肥中，不吃辣了"  → 记忆 E（A/B/C/D 都过时了）

如果不做 Compaction：
- 库里同时有 5 条关于"用户口味"的记忆
- 检索时可能召回过时的 A/B/C，干扰当前回答
- 向量库膨胀，检索变慢
- 注入上下文时，宝贵的 token 被过时记忆占用
```

---

## 二、mem0 中的 Compaction 形态（从源码看）

### 2.1 核心库（memory/main.py）没有显式 Compaction

**重要发现**：mem0 的 Python 核心库（`mem0/memory/main.py`）**没有实现自动 compaction**。

它有的只是：
- `update()`：用新记忆覆盖旧记忆（但旧版本存到 history 表里）
- `delete()`：删除单条记忆
- `search()`：检索时按分数排序

**没有定时任务、没有自动合并、没有容量触发。**

### 2.2 平台层（API）有 decay 参数

**源码位置**：`mem0/client/project.py:401-413`

```python
decay: Toggle Memory Decay for this project. When True, search-time
    ranking boosts recently-used memories and gently dampens stale ones; when
    False, ranking is restored to the pre-decay behaviour. Off by default.
```

**关键点**：
- mem0 的 decay 是**搜索时**生效的，不是**删除**记忆
- 开启后，检索排序时：近期使用的记忆分数提升，旧记忆分数衰减
- **记忆本身还在库里**，只是排不到前面了
- 默认是关闭的（Off by default）

### 2.3 插件层（mem0-plugin）有上下文压缩钩子

**源码位置**：`mem0-plugin/scripts/on_pre_compact.py`

这是 Claude Code 插件的钩子：当 Claude 的上下文要 compaction（压缩）时，触发 PreCompact hook。

```python
# 这个脚本在 compaction 之前执行
source = "pre-compaction"  # 标记这是 compaction 前捕获的状态
SESSION_STATE_EXPIRY_DAYS = 90  # 捕获的状态 90 天后过期

# 存储到 mem0 时带 expiration_date
expires = (date.today() + timedelta(days=SESSION_STATE_EXPIRY_DAYS)).isoformat()
body = {
    "expiration_date": expires,
    "infer": True,
}
```

**另一个脚本**：`capture_compact_summary.py`

```python
# 在 SessionStart 时捕获 compact summary
# 找到 transcript 中标记 isCompactSummary=true 的条目，存储到 mem0
COMPACT_SUMMARY_EXPIRY_DAYS = 90  # 同样 90 天过期
```

**总结 mem0 的 compaction 现状：**

| 层级 | 有没有 Compaction | 形式 |
|------|------------------|------|
| 核心库（Python） | ❌ 没有 | 只有增删改查 |
| 平台 API | ⚠️ 有 decay（搜索排序衰减） | 不删除，只降权 |
| 插件层 | ✅ 有 PreCompact hook | 在 Claude 上下文压缩前后捕获状态 |
| 存储层 | ⚠️ 支持 expiration_date | 写入时可设过期时间，但核心库不自动清理 |

---

## 三、时间衰减策略：exponential vs power-law

### 3.1 为什么需要时间衰减？

记忆的价值不是恒定的。一条"用户喜欢川菜"的记忆：
- 昨天说的 → 价值高（当前有效）
- 三个月前说的 → 价值中等（可能还有效）
- 一年前说的 → 价值低（可能已过时）
- 三年前说的 → 价值趋近于零（大概率过时）

**时间衰减 = 给记忆的价值随时间下降。**

### 3.2 Exponential Decay（指数衰减）

```python
weight(t) = w0 * exp(-λ * t)

w0 = 初始权重（比如 1.0）
λ = 衰减系数（越大衰减越快）
t = 距离上次使用/更新的时间

示例（λ=0.01，每天衰减）：
Day 0:  weight = 1.0 * exp(0) = 1.0
Day 30: weight = 1.0 * exp(-0.3) = 0.74
Day 90: weight = 1.0 * exp(-0.9) = 0.41
Day 180: weight = 1.0 * exp(-1.8) = 0.17
Day 365: weight = 1.0 * exp(-3.65) = 0.026
```

**特点**：
- 早期衰减快，后期衰减慢
- 适合"最近最重要"的场景
- 衰减系数 λ 需要调参

### 3.3 Power-Law Decay（幂律衰减）

```python
weight(t) = w0 * t^(-α)

w0 = 初始权重
α = 衰减指数（通常 0.5~2.0）
t = 时间

示例（α=1.0）：
Day 1:   weight = 1.0 / 1 = 1.0
Day 7:   weight = 1.0 / 7 = 0.14
Day 30:  weight = 1.0 / 30 = 0.033
Day 90:  weight = 1.0 / 90 = 0.011
Day 365: weight = 1.0 / 365 = 0.0027
```

**特点**：
- 衰减比指数更快（尤其后期）
- 有长尾效应：旧记忆永远不会降到绝对零，只是权重极小
- 适合"旧记忆偶尔还有用"的场景（比如多年前学的技能）

### 3.4 怎么选？

| 场景 | 推荐策略 | 原因 |
|------|----------|------|
| 用户偏好（口味、习惯） | Exponential（慢衰减） | 偏好变化慢，但确实会变 |
| 临时计划（下周出差） | Exponential（快衰减） | 过期后完全无价值 |
| 技能知识（Python 用法） | Power-Law | 多年前学的也可能有用，但新学的更重要 |
| 社交关系（A 是 B 的领导） | 不衰减或极慢衰减 | 关系相对稳定 |

---

## 四、Compaction 的触发时机（工业级做法）

### 4.1 定时触发

```python
# 每天凌晨 2 点跑一次
schedule: "0 2 * * *"

# 做什么：
# 1. 找所有超过 90 天未使用的记忆
# 2. 检查是否有更新的版本（如"喜欢川菜"→"改吃粤菜"）
# 3. 旧版本标记为过期或删除
# 4. 合并语义重复的记忆（3 条"喜欢川菜"合并成 1 条）
```

### 4.2 容量阈值触发

```python
# 当用户记忆量超过阈值时触发
if memory_count > 10000:
    trigger_compaction()

# 做什么：
# 1. 按综合价值（权重 × 重要性 × 时间衰减）排序
# 2. 删除 bottom 10%（或归档到冷存储）
# 3. 保留 top 90%，确保质量不下降
```

### 4.3 Background Review 触发（与 K.2 关联）

```python
# 每 N 轮对话触发 Background Review
# 其中一步就是 Compaction：

Background Review 流程：
  1. 回顾近期记忆 → 提取可复用技能（Skill Creation）
  2. 压缩冗余记忆（Compaction）
  3. 更新用户画像（USER.md）
  4. 删除/归档过时记忆
```

### 4.4 与 mem0 的 decay 对比

| 机制 | 触发时机 | 作用 | 记忆是否删除 |
|------|----------|------|-------------|
| **mem0 decay** | 每次搜索时 | 排序时给旧记忆降权 | 不删除 |
| **定时 Compaction** | 定时任务（如每天） | 删除/合并过期记忆 | 删除 |
| **容量触发 Compaction** | 容量超阈值 | 淘汰低价值记忆 | 删除/归档 |
| **Background Review** | 每 N 轮对话 | 综合治理（含 compaction） | 删除/合并 |

---

## 五、Compaction 失败了怎么恢复？（Q17 追问）

### 5.1 幂等设计

Compaction 不是一次性操作，而是**可以重复执行的幂等操作**。

```python
# 幂等 = 执行多次和执行一次结果相同

def compact_memories(user_id):
    # 1. 找出候选记忆（如超过 90 天未使用）
    candidates = find_stale_memories(user_id, days=90)
    
    # 2. 对每条候选记忆：
    for mem in candidates:
        # 检查是否已经被 compact 过（标记位）
        if mem.get("compacted_at"):
            continue  # 已经处理过，跳过
        
        # 执行压缩/删除
        do_compact(mem)
        
        # 标记已处理
        mem["compacted_at"] = now()
        save(mem)
    
    # 如果中途失败，下次重新执行时，已标记的会跳过
    # 不会重复删除，也不会漏掉未处理的
```

### 5.2 失败恢复策略

| 失败场景 | 恢复方式 |
|----------|----------|
| Compaction 任务中途崩溃 | 幂等重试：已处理的跳过，未处理的继续 |
| 误删了重要记忆 | 从 history 表恢复（mem0 的 update 保留旧版本到 history） |
| 合并错了（把不相关的合并了） | 回滚：用 history 中的旧版本重建 |
| 系统停机期间错过 compaction | 下次启动时补跑，或 catch-up 模式批量处理 |

### 5.3 用户给的方案（版本号/时间戳）

用户之前口述过："给每条会话加上类似版本号的概念，或者时间戳"

**正确**。这正是幂等的实现方式：
- **时间戳**：记录最后 compaction 时间，避免重复处理
- **版本号**：每次 compaction 生成新版本，旧版本保留在 history 中
- **标记位**：`compacted_at` / `archived_at` 字段

---

## 六、与 Background Review 的关系（K.2 关联）

| 机制 | 频率 | 职责 | 是否包含 Compaction |
|------|------|------|-------------------|
| **即时写入** | 每轮对话结束 | 提取新记忆，写入向量库 | 否 |
| **Background Review** | 每 N 轮（如 20 轮） | 回顾、压缩、画像更新 | **是** |
| **定时 Compaction** | 每天/每周 | 批量清理过期记忆 | 是（纯清理） |

**Background Review 的 compaction 是"有智慧的清理"**：
- 不是简单按时间删，而是先回顾近期对话
- 判断哪些记忆已经过时（如"下周出差"→出差回来了就过时）
- 判断哪些记忆可以合并（3 条"喜欢川菜"→1 条）
- 判断哪些记忆可以升级为技能（反复出现的操作→Skill）

**定时 Compaction 是"无智慧的清理"**：
- 按规则执行：超过 90 天删、超过 10000 条淘汰 bottom 10%
- 不需要 LLM 参与，纯规则驱动

---

## 七、面试考点

| 问题 | 答案要点 |
|------|----------|
| "Compaction 什么时候触发？" | 三种方式：定时（如每天）、容量阈值（如超 1 万条）、Background Review（每 N 轮对话） |
| "Compaction 失败了怎么恢复？" | 幂等设计：时间戳/版本号/标记位，失败重跑时跳过已处理的。误删了从 history 恢复。 |
| "mem0 有 Compaction 吗？" | 核心库没有。平台 API 有 `decay`（搜索时降权，不删除）。插件层有 PreCompact hook（Claude 上下文压缩时捕获）。 |
| "exponential decay 和 power-law decay 的区别？" | 指数衰减：早期快后期慢，适合偏好/计划。幂律衰减：有长尾，旧记忆永远不降到零，适合技能知识。 |
| "为什么 decay 是搜索时生效，不是删除记忆？" | 删除是硬操作，不可逆。decay 是软操作，旧记忆还在，只是排不到前面。如果用户后来确认旧记忆还有效，可以恢复权重。 |
| "如果不做 Compaction，记忆系统会怎样？" | 无限膨胀、检索变慢、召回过时记忆干扰当前回答、上下文注入被过期记忆占用 token。 |

---

## 八、知识串联图

```
用户对话
   │
   ▼
即时写入（每轮） ────────→ 新记忆进入向量库
   │
   ▼
Background Review（每 N 轮）
   ├─ 回顾近期记忆 → 提取 Skill
   ├─ Compaction → 合并重复、删除过时
   └─ 更新 USER.md
   │
   ▼
定时 Compaction（每天/每周）
   ├─ 删除超过 TTL 的记忆
   ├─ 合并语义重复
   └─ 容量超限则淘汰 bottom 10%
   │
   ▼
用户查询时
   ├─ 向量检索（over-fetch）
   ├─ BM25 检索
   ├─ 混合评分
   └─ 可选 decay 降权（旧记忆分数 × 衰减系数）
   │
   ▼
最终 top-k 注入上下文
```

---

> 产出物：`materials/04_compaction_decay.md`

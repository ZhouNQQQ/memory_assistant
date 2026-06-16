# 学习资料：LangGraph 普及 — 图编排 Agent 工作流

> 定位：普及知识面，理解 LangGraph 是什么、核心概念、和记忆系统的关系
> 来源：综合多个教程整理，去掉了源码细节，保留核心概念

---

## 一、LangGraph 是什么

**一句话：** LangGraph 是 LangChain 生态中的**图编排框架**，用"节点（Node）+ 边（Edge）+ 状态（State）"来定义 Agent 的工作流。

**为什么不用普通的函数调用？**

```python
# 普通方式：线性调用
result1 = step1(input)
result2 = step2(result1)
result3 = step3(result2)
# 问题：不支持循环、不支持条件分支、不支持并行

# LangGraph 方式：图编排
# 支持：循环（反思重试）、条件分支（if/else）、并行执行
# 支持：状态持久化（checkpoint）、人工介入（human-in-the-loop）
```

---

## 二、核心概念（4 个）

### 1. State（状态）

**是什么：** 一个全局共享的数据结构，所有节点都能读和写。

**类比：** 像 Git 的仓库，每次 commit 后状态更新，所有节点都能看到最新的状态。

```python
from typing import TypedDict

class AgentState(TypedDict):
    messages: list          # 对话历史
    user_query: str         # 用户问题
    retrieved_docs: list    # 检索到的文档
    final_answer: str       # 最终答案
    # ... 任何你需要在节点间传递的数据
```

**关键点：**
- State 是可序列化的（能存到磁盘/数据库）
- 每个节点返回的是 **State 的增量更新**（不是完整替换）
- LangGraph 会自动合并增量到全局 State

---

### 2. Node（节点）

**是什么：** 一个函数，接收 State，返回 State 的更新。

```python
def retrieve_node(state: AgentState):
    """检索节点：从向量库检索相关文档"""
    query = state["user_query"]
    docs = vector_store.search(query)
    return {"retrieved_docs": docs}  # 只返回要更新的字段

def generate_node(state: AgentState):
    """生成节点：用 LLM 生成回答"""
    docs = state["retrieved_docs"]
    query = state["user_query"]
    answer = llm.generate(f"基于以下文档回答问题：{docs}\n问题：{query}")
    return {"final_answer": answer}
```

**关键点：**
- 节点是无状态的，只依赖输入的 State
- 节点可以调用 LLM、工具、数据库、API
- 节点可以跨工作流复用

---

### 3. Edge（边）

**是什么：** 节点之间的连接，决定执行顺序。

**两种边：**

| 类型 | 写法 | 含义 |
|------|------|------|
| **普通边** | `add_edge("A", "B")` | A 执行完，一定执行 B |
| **条件边** | `add_conditional_edges("A", route_fn, {"x": "B", "y": "C"})` | A 执行完，根据 route_fn 的返回值决定走 B 还是 C |

```python
# 普通边：检索 → 生成
workflow.add_edge("retrieve", "generate")

# 条件边：判断是否需要重试
def should_retry(state):
    if state["retry_count"] < 3 and not state["answer_good_enough"]:
        return "retry"      # 重试
    return "end"            # 结束

workflow.add_conditional_edges(
    "generate",
    should_retry,
    {"retry": "retrieve", "end": END}
)
```

**关键点：**
- 条件边让 Agent 能**自主决策**下一步做什么
- 循环 = 条件边指回前面的节点（如 `generate` → `retrieve`）

---

### 4. Checkpoint（检查点）

**是什么：** 每次节点执行后，State 被自动保存到持久化存储。

**有什么用：**
- **断点续传**：工作流执行到一半崩溃了，从 checkpoint 恢复，不用从头来
- **时间旅行**：可以回滚到之前的 State，重新走不同的分支
- **人工介入**：执行到某个节点暂停，等人审批后再继续

```python
# 启用 checkpoint
workflow.compile(checkpointer=MemorySaver())

# 执行后，每步的 State 都自动保存
# 下次调用时可以传入 thread_id，从上次状态继续
```

---

## 三、LangGraph 的运行流程

```
用户输入 → 初始化 State
    │
    ▼
┌─────────────────────────────────────────┐
│ 入口节点（Entry Point）                  │
│ 如：retrieve_node（检索文档）            │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 普通边：retrieve → generate            │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 条件边：generate → 判断是否需要重试       │
│ 需要重试 → 回到 retrieve                │
│ 不需要 → 结束（END）                    │
└─────────────────────────────────────────┘
    │
    ▼
  输出最终 State
```

---

## 四、LangGraph vs 传统 Agent 编排

| 特性 | 传统方式（函数调用） | LangGraph（图编排） |
|------|---------------------|---------------------|
| 循环/重试 | 自己写 while 循环 | 条件边天然支持 |
| 条件分支 | 自己写 if/else | 条件边定义清晰 |
| 并行执行 | 自己写多线程 | 支持并行节点 |
| 状态持久化 | 自己管理 | Checkpoint 自动保存 |
| 可视化 | 代码逻辑难读 | 图结构就是可视化文档 |
| 调试 | 打断点逐行 | 可以看每步的 State 快照 |

---

## 五、LangGraph 和记忆系统的关系

**核心问题：LangGraph 的 State 里放记忆，但 State 是临时的。**

```python
# 场景：用户和 LangGraph Agent 对话

# 第一次对话
state = {"messages": ["你好"], "user_name": "张三"}
result = graph.invoke(state)  # Agent 回复
# 对话结束，state 销毁（除非启用了 checkpoint）

# 第二次对话（新会话）
state = {"messages": ["帮我查订单"]}  # 用户姓名丢了！
# 问题：如果 State 里没有持久化记忆层，换会话后信息丢失
```

**解决方案：在 LangGraph 的 State 中集成记忆层**

```python
class AgentState(TypedDict):
    messages: list
    user_query: str
    # ...

# 在入口节点，从记忆层加载用户画像
async def entry_node(state: AgentState):
    user_id = state.get("user_id")
    # 从记忆层加载："张三喜欢川菜、是 Java 架构师"
    user_profile = memory.search(f"user_id={user_id}", limit=5)
    return {"user_profile": user_profile}

# 在结束节点，把新记忆写入记忆层
async def exit_node(state: AgentState):
    new_memory = extract_from_conversation(state["messages"])
    memory.add(new_memory)  # 持久化
    return {}
```

**关键点：**
- LangGraph 负责**编排流程**（节点怎么跳转）
- 记忆层负责**持久化数据**（跨会话记住用户信息）
- 两者是互补关系：LangGraph 用记忆层来增强 State

---

## 六、LangGraph 的常见应用场景

| 场景 | LangGraph 怎么帮 | 记忆层的作用 |
|------|------------------|------------|
| **RAG 问答** | 检索 → 生成 → 判断是否需要重试（循环） | 记住用户领域偏好，检索时过滤 |
| **多 Agent 协作** | 研究员 Agent → 写手 Agent → 审核 Agent | 记住各 Agent 的分工和产出 |
| **客服自动化** | 理解问题 → 查知识库 → 生成回答 → 人工升级 | 记住用户历史工单，避免重复提问 |
| **代码助手** | 理解需求 → 生成代码 → 测试 → 修复（循环） | 记住用户编码风格和项目架构 |

---

## 七、与其他框架的对比（普及版）

| 框架 | 核心特点 | 和 LangGraph 的区别 |
|------|----------|-------------------|
| **LangGraph** | 图编排，节点+边+状态，支持循环/条件 | 本节课主角 |
| **LangChain** | 链式调用，LCEL（LangChain Expression Language） | 线性流程，LangGraph 是它的扩展 |
| **AutoGen** | 多 Agent 对话，Agent 之间可以聊天 | 对话驱动，LangGraph 是图驱动 |
| **AgentScope** | 阿里开源，支持分布式部署 | 侧重分布式，LangGraph 侧重编排 |

**关系：LangChain 是基础 → LangGraph 是 LangChain 的图编排扩展**

---

## 八、面试考点（普及阶段）

| 问题 | 一句话答案 |
|------|----------|
| "LangGraph 是什么？" | LangChain 的图编排框架，用节点+边+状态定义 Agent 工作流 |
| "LangGraph 为什么需要图？普通函数调用不行吗？" | 普通调用是线性的，图支持循环、条件分支、并行 |
| "State 是什么？有什么用？" | 全局共享数据结构，所有节点读写，用来传递上下文 |
| "Checkpoint 是什么？" | 自动保存 State 快照，支持断点续传和人工介入 |
| "LangGraph 和记忆系统的关系？" | LangGraph 编排流程，记忆层持久化数据。State 里的记忆换会话会丢，需要外接记忆层 |

---

## 九、一句话总结

> **LangGraph = 用"图"来编排 Agent 的工作流程。节点是操作，边是流转，状态是共享数据。它让 Agent 能循环、分支、并行，但 State 是临时的，需要外接记忆层才能实现跨会话持久化。**

---

> 延伸阅读：
> - 官方文档：`https://langchain-ai.github.io/langgraph/`
> - 和 LangChain 的关系：LangGraph 是 LangChain 生态的一部分，不能脱离 LangChain 单独使用

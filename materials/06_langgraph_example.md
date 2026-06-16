# LangGraph 真实案例：客服 Agent（完整讲解版）

> 用一个真实用户使用场景，串联解释 State / Node / Edge / Checkpoint / thread_id / 记忆层 的全部概念

---

## 场景设定

**产品**：智能客服 Agent
**用户**：张三，经常询问订单状态
**核心需求**：
- 张三说"帮我查一下那个订单"——Agent 要知道"那个订单"是指哪个（需要记住上下文）
- 张三隔了 3 小时又问"那个订单发货了吗"——Agent 还要记得"那个订单"（跨时间）
- 张三明天又来问"我上次问的订单怎么样了"——Agent 要记住"上次"（跨会话）

---

## 代码结构

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
from langgraph.checkpoint.memory import MemorySaver
import operator

# ==================== 1. State（全局状态）====================

class ChatState(TypedDict):
    """State 是 LangGraph 的"共享数据仓库"
    
    每次 graph.invoke() 时，LangGraph 创建一个 State 实例
    所有节点都能读/写这个 State
    节点返回增量（dict），LangGraph 自动合并到 State
    """
    messages: Annotated[list, operator.add]  # 对话历史，增量追加
    user_id: str                               # 用户 ID
    user_name: str                             # 用户姓名（从记忆层加载）
    last_order_id: str                         # 最后提到的订单（从记忆层加载）
    intent: str                                # 当前意图（诊断节点识别）
    order_info: dict                           # 订单查询结果
    response: str                              # 最终回复

# ==================== 2. Node（节点 = 操作单元）====================

def load_memory_node(state: ChatState):
    """入口节点：从记忆层加载用户画像
    
    这个节点只做一件事：根据 user_id 去记忆层查用户信息
    然后把查到的信息写入 State
    """
    user_id = state["user_id"]
    
    # 从记忆层（如 mem0）加载
    profile = memory.search(f"user_id={user_id}", limit=5)
    
    # 返回增量：只返回要更新的字段
    return {
        "user_name": profile.get("name", "未知用户"),
        "last_order_id": profile.get("last_order_id"),
    }

def diagnose_node(state: ChatState):
    """诊断节点：识别用户意图
    
    输入 State 中的 messages 和 user_name
    输出 intent（意图）和是否需要查订单
    """
    last_message = state["messages"][-1]
    user_name = state["user_name"]
    last_order = state.get("last_order_id")
    
    # LLM 判断意图
    if "那个订单" in last_message and last_order:
        intent = "query_last_order"  # 查上次的订单
    elif "查订单" in last_message:
        intent = "query_order"
    else:
        intent = "general_chat"
    
    return {"intent": intent}

def query_order_node(state: ChatState):
    """查订单节点：调用订单 API"""
    intent = state["intent"]
    
    if intent == "query_last_order":
        order_id = state["last_order_id"]  # 从 State 里取上次订单
    else:
        order_id = extract_order_id(state["messages"][-1])
    
    # 调用订单 API
    order_info = order_api.query(order_id)
    
    return {"order_info": order_info}

def generate_node(state: ChatState):
    """生成节点：用 LLM 生成回复"""
    user_name = state["user_name"]
    order_info = state.get("order_info")
    
    # 调用 LLM
    response = llm.generate(
        f"用户 {user_name} 问：{state['messages'][-1]}\n"
        f"订单信息：{order_info}\n"
        f"请生成友好回复"
    )
    
    return {"response": response}

def save_memory_node(state: ChatState):
    """出口节点：保存新记忆到记忆层
    
    把这次对话中提取的新信息写入记忆层
    下次用户再来，这些记忆还在
    """
    user_id = state["user_id"]
    new_facts = extract_facts_from_messages(state["messages"])
    
    # 写入记忆层（如 mem0）
    memory.add(new_facts, user_id=user_id)
    
    return {}  # 不更新 State，只写外部记忆层

# ==================== 3. Edge（边 = 流转规则）====================

# 普通边：固定顺序
def route_by_intent(state: ChatState):
    """条件边：根据意图决定下一步"""
    if state["intent"] == "general_chat":
        return "chat"  # 走普通聊天分支
    return "query"   # 走查订单分支

# 构建图
workflow = StateGraph(ChatState)

# 添加节点
workflow.add_node("load_memory", load_memory_node)
workflow.add_node("diagnose", diagnose_node)
workflow.add_node("query_order", query_order_node)
workflow.add_node("generate", generate_node)
workflow.add_node("save_memory", save_memory_node)

# 添加边
workflow.set_entry_point("load_memory")           # 入口：总是从加载记忆开始
workflow.add_edge("load_memory", "diagnose")     # 加载完 → 诊断
workflow.add_conditional_edges(                   # 条件边：诊断后分支
    "diagnose",
    route_by_intent,
    {
        "query": "query_order",  # 意图是查订单 → 去查订单
        "chat": "generate",      # 意图是聊天 → 直接生成回复
    }
)
workflow.add_edge("query_order", "generate")      # 查完订单 → 生成回复
workflow.add_edge("generate", "save_memory")     # 生成完 → 保存记忆
workflow.add_edge("save_memory", END)              # 保存完 → 结束

# ==================== 4. Checkpoint（检查点）====================

# 启用 Checkpoint：用 MemorySaver（内存存储，适合演示）
# 生产环境用 RedisSaver / PostgresSaver
checkpointer = MemorySaver()

# 编译图：启用 Checkpoint
graph = workflow.compile(checkpointer=checkpointer)

# ==================== 5. 执行（3 种场景）====================

# --- 场景 A：第一次对话（全新 thread）---
config_a = {"configurable": {"thread_id": "zhangsan_session_001"}}

result_a = graph.invoke(
    {"messages": ["帮我查一下订单"], "user_id": "zhangsan"},
    config=config_a
)

# 执行流程：
# 1. load_memory → 从 mem0 加载：user_name="张三", last_order_id="ORD-12345"
# 2. diagnose → 识别意图：intent="query_order"
# 3. query_order → 查订单 API：order_info={"id": "ORD-12345", "status": "已发货"}
# 4. generate → LLM 生成："张三，您的订单 ORD-12345 已发货"
# 5. save_memory → 写入 mem0："张三最后查的订单是 ORD-12345"
# 
# Checkpoint 自动保存：
# thread_id="zhangsan_session_001" 的 State 快照被保存

# --- 场景 B：同一会话，3 小时后继续（同一个 thread_id）---
config_b = {"configurable": {"thread_id": "zhangsan_session_001"}}  # 同一个 thread_id！

result_b = graph.invoke(
    {"messages": ["那个订单发货了吗？"], "user_id": "zhangsan"},
    config=config_b
)

# 执行流程：
# 1. LangGraph 从 Checkpoint 加载上次 State：
#    State 初始值 = {"messages": ["帮我查一下订单", ...], "last_order_id": "ORD-12345", ...}
# 2. 合并新输入：messages += ["那个订单发货了吗？"]
# 3. load_memory → 又从 mem0 加载：user_name="张三", last_order_id="ORD-12345"
# 4. diagnose → intent="query_last_order"（因为"那个订单" + last_order_id 存在）
# 5. query_order → 查 ORD-12345
# 6. generate → "张三，订单 ORD-12345 已发货，预计明天送达"
# 7. save_memory → 更新 mem0："张三最后查的订单仍是 ORD-12345"

# --- 场景 C：明天新会话（没有 Checkpoint）---
# 如果不传 config（没有 Checkpoint）：
result_c = graph.invoke(
    {"messages": ["我上次问的订单怎么样了？"], "user_id": "zhangsan"}
    # 没有 config！没有 thread_id！
)

# 执行流程：
# 1. State 从零开始：{"messages": ["我上次问的订单怎么样了？"], "user_id": "zhangsan"}
# 2. load_memory → 从 mem0 加载：user_name="张三", last_order_id="ORD-12345"
# 3. 但 State 里没有任何上次对话的历史！
# 4. diagnose → 看到"上次"，但 State 里没历史，LLM 只能猜
# 5. 如果没有记忆层，last_order_id 也是空的 → 问"请问您指的是哪个订单？"
```

---

## 概念对照表（用例子中的术语解释）

| 概念 | 在例子中的对应 | 一句话解释 |
|------|--------------|----------|
| **State** | `ChatState`（messages, user_name, last_order_id 等） | 共享数据仓库，所有节点读/写 |
| **Node** | `load_memory_node`, `diagnose_node`, `query_order_node` | 操作单元，接收 State 返回增量 |
| **Edge** | `load_memory → diagnose`, `diagnose → query_order` | 流转规则，条件边决定分支 |
| **Checkpoint** | `MemorySaver()` + `thread_id` | 保存 State 快照，同 thread_id 复用 |
| **thread_id** | `"zhangsan_session_001"` | Checkpoint 的键，区分不同会话 |
| **记忆层** | `memory.search()` / `memory.add()` | 外部持久化系统（如 mem0），跨 thread_id 共享 |
| **增量更新** | `return {"user_name": "张三"}` | 节点只返回要改的字段，LangGraph 合并 |

---

## 回答用户的具体问题

### 1. "什么情况没有 Checkpoint？"

```python
# 没有 Checkpoint 的情况：不传 checkpointer
graph = workflow.compile()  # 没有 checkpoint！

# 每次调用都是全新的
result1 = graph.invoke({"messages": ["你好"]})
result2 = graph.invoke({"messages": ["帮我查订单"]})
# 两次调用完全独立，State 不共享
```

**绝大多数入门教程都不启用 Checkpoint。** 因为 Checkpoint 是进阶功能，需要配置持久化存储。

### 2. "thread_id 是什么？"

```python
# thread_id = 会话的唯一标识
config = {"configurable": {"thread_id": "zhangsan_session_001"}}

# 同一个 thread_id 的多次调用共享 Checkpoint 保存的 State
# 不同 thread_id 之间隔离：
#   "zhangsan_session_001" 和 "zhangsan_session_002" 是两个独立会话
#   它们的 State 互不干扰
```

**类比：thread_id = 微信群聊的群号。同一个群的聊天记录共享，不同群的记录不共享。**

### 3. "State 的生命周期和图编排流程一样？"

**不完全一样：**

| 场景 | State 生命周期 |
------|-------------|
| 没有 Checkpoint | `graph.invoke()` 开始 → 创建 State → 执行结束 → State 销毁 |
| 有 Checkpoint（不同 thread_id） | 同上，每个 thread_id 独立 |
| 有 Checkpoint（同一个 thread_id） | 第一次 `invoke()` 创建并保存 → 第二次 `invoke()` 加载并更新 → 继续保存 |

**增量合并：发生在每个节点执行后。**

```python
def some_node(state):
    return {"new_field": "value"}  # 增量：只返回新字段

# LangGraph 内部自动做：
# state["new_field"] = "value"  # 合并到现有 State
```

### 4. "将 Node 更新到 State 是谁做的？"

```python
# Node 只返回字典：
def my_node(state):
    return {"response": "hello"}

# 合并是 LangGraph 运行时（Pregel/Runtime）自动做的：
# 1. 执行 my_node(state) → 得到 {"response": "hello"}
# 2. state.update({"response": "hello"})  # 自动合并
# 3. Checkpoint 保存（如果启用）
# 4. 走 Edge 到下一个 Node
```

**Node 只管业务逻辑，State 的合并和持久化是 LangGraph 引擎做的。**

### 5. "记忆层放在 Checkpoint？"

**记忆层不是放在 Checkpoint 里。记忆层是外接系统，Node 显式调用。**

```python
# Checkpoint 保存的是 State 快照（运行时的临时数据）
# 记忆层保存的是长期知识（用户画像、历史事实）

# 关系：
# Checkpoint 可以保存"从记忆层加载的数据"（如 user_name, last_order_id）
# 但这些数据的本源在记忆层，不在 Checkpoint

# 区别：
# - Checkpoint 丢了 → 只是当前会话的状态丢了，下次重新加载记忆层即可
# - 记忆层丢了 → 用户的所有长期知识都丢了，无法恢复
```

---

## 一句话总结

> **没有 Checkpoint 时，每次 `invoke()` 都是新 State，不共享。有 Checkpoint + `thread_id` 时，同 `thread_id` 的调用共享 State。Checkpoint 保存的是 State 快照，记忆层是外部持久化系统，Node 显式调用。增量更新 = Node 返回部分字段，LangGraph 引擎自动合并。**


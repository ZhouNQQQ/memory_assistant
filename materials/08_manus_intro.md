# 学习资料：Manus 普及 — 通用 AI Agent

> 定位：普及知识面，理解 Manus 是什么、核心架构、和 LangGraph/Cursor 的区别
> 来源：综合多个教程和 OpenManus 源码分析整理

---

## 一、Manus 是什么

**一句话：** Manus 是**全球首款通用 AI Agent**（2025 年 3 月发布），能自主执行复杂任务（浏览网页、写代码、生成报告、数据分析），不需要人工逐步指导。

**和 ChatGPT 的区别：**

| ChatGPT | Manus |
|---------|-------|
| 你问一句，它答一句 | 你说一个任务，它自己做完 |
| 不能操作外部工具 | 能调用浏览器、命令行、Python、文件系统 |
| 没有记忆 | 有长期记忆，记住你的偏好和历史 |
| 每次对话独立 | 能跨会话保持任务状态 |

**类比：**
- ChatGPT = 咨询顾问（你问它答）
- Manus = 项目经理（你给目标，它自己调研、执行、交付）

---

## 二、核心架构：多 Agent 协同

Manus 不是单个 Agent，而是**多个 Agent 组成的团队**，每个 Agent 负责不同环节。

```
用户输入："帮我调研新能源汽车市场，生成一份报告"
    │
    ▼
┌─────────────────────────────────────────┐
│ 规划代理（Planning Agent）                 │
│ 拆解任务：                                │
│   1. 搜索最新市场数据                      │
│   2. 分析主要品牌（比亚迪、特斯拉）         │
│   3. 生成对比表格                          │
│   4. 写成报告并导出 PDF                    │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 执行代理（Execution Agent）              │
│ 调用工具：                                │
│   - 浏览器代理：搜索网页、抓取数据          │
│   - 代码代理：写 Python 做数据分析          │
│   - 文件代理：保存结果到 Excel/PDF          │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 验证代理（Verification Agent）            │
│ 检查：                                    │
│   - 数据是否完整？                        │
│   - 分析是否有逻辑错误？                   │
│   - 报告格式是否正确？                     │
│ 不通过 → 回到执行代理修复                 │
│ 通过 → 交付给用户                         │
└─────────────────────────────────────────┘
```

**关键点：**
- 每个 Agent 运行在**独立的虚拟机/沙箱**中，互不干扰
- Agent 之间通过 API 或消息队列通信
- 可以**并行执行**（多个 Agent 同时工作）

---

## 三、分层代理架构（OpenManus 源码）

OpenManus 是 Manus 的开源复刻版，它的源码展示了分层架构：

```
BaseAgent（基础代理）
    ├── 状态管理（AgentState）
    ├── 执行循环（run loop）
    └── 抽象方法（think / act）
    │
    ▼
ReActAgent（思考-行动代理）
    ├── think()：调用 LLM，决定下一步
    └── act()：执行工具，观察结果
    │
    ▼
ToolCallAgent（工具调用代理）
    ├── 工具集合（ToolCollection）
    └── 工具调用逻辑
    │
    ▼
Manus（具体智能体）
    ├── 浏览器工具
    ├── 命令行工具
    ├── Python 执行工具
    └── 文件保存工具
```

**分层的好处：**
- **可扩展**：新增一种 Agent（如 DataAnalysisAgent），只需继承 ToolCallAgent
- **可复用**：ReActAgent 的 think/act 逻辑可以被所有子 Agent 复用
- **可替换**：LLM 可以换（GPT-4 / Claude / 国产模型），不影响上层逻辑

---

## 四、ReAct 模式：Manus 的核心循环

Manus 采用的是 **ReAct 范式**（Reasoning + Acting），不是 Plan-and-Execute。

```python
# ReAct 循环
while not done:
    # 1. Think：LLM 思考当前状态，决定下一步
    thought = llm.think("我现在有什么信息？下一步该做什么？")
    
    # 2. Act：执行工具（搜索/写代码/浏览网页）
    action = tool.execute(thought)
    
    # 3. Observe：观察工具返回的结果
    observation = action.result
    
    # 4. 更新记忆
    memory.add(thought, action, observation)
    
    # 5. 检查是否完成
    if terminate_tool.should_stop(observation):
        break
```

**和 Cursor 的 Plan-and-Execute 对比：**

| | Manus（ReAct） | Cursor Composer（Plan-and-Execute） |
|--|---------------|--------------------------------------|
| 第一步 | 直接开始思考+行动 | 先做完整规划 |
| 灵活性 | 高（可以随时调整方向） | 低（规划定好后按步骤执行） |
| 适用场景 | 开放性问题（调研、分析） | 确定性任务（代码重构） |
| 风险 | 可能偏离目标 | 规划错误会导致整轮失败 |

**Manus 为什么用 ReAct？**
- 用户给的是**模糊目标**（"帮我调研市场"），不是明确步骤
- 环境是动态的（网页内容会变，搜索结果不确定）
- 需要边做边观察，根据观察结果调整策略

---

## 五、工具系统：Manus 的"手"

Manus 能调用多种工具，每种工具是一个独立的 Agent：

| 工具 | 功能 | 示例 |
|------|------|------|
| **Browser（浏览器）** | 模拟浏览器，搜索网页、抓取数据 | 搜索"2025 新能源汽车销量" |
| **Bash（命令行）** | 执行 shell 命令 | `git clone`、`npm install` |
| **Python Execute** | 执行 Python 代码 | 数据分析、图表生成 |
| **File Saver** | 保存文件到本地 | 保存报告为 PDF/Excel |
| **Terminate** | 自主判断任务完成 | 当目标达成时自动结束 |
| **AskHuman** | 遇到困难时向人求助 | 数据缺失时问用户 |

**工具系统的关键设计：**
- 所有工具继承 `BaseTool` 抽象类，统一接口
- LLM 通过**函数调用（Function Calling）**选择工具
- 工具参数用 JSON Schema 描述，LLM 自动填充参数

---

## 六、记忆系统

Manus 的记忆系统和我们学的 mem0 类似：

| 记忆类型 | 作用 | 存储方式 |
|----------|------|----------|
| **短期记忆** | 当前任务的上下文（对话历史、中间结果） | 内存中的 State |
| **长期记忆** | 用户偏好、历史任务、常用工具 | 向量库/数据库 |
| **工作记忆** | 当前步骤的关键信息（如刚搜索到的数据） | LLM 的上下文窗口 |

**和 Qclaw 的对比：**
- Qclaw 只有**长期记忆**（USER.md）
- Manus 有**短期+长期+工作记忆**三层
- Manus 的短期记忆是**结构化**的（State 对象），不是纯文本

---

## 七、"Less Structure, More Intelligence" 理念

Manus 团队的核心理念：

> **给 AI 更少的结构化约束，AI 会体现更高的智能。**

**传统做法（高结构）：**
```python
# RAG 流程：第一步检索 → 第二步构建 prompt → 第三步生成
# 流程是固定的，LLM 只能按预定步骤执行
```

**Manus 做法（低结构）：**
```python
# LLM 自主决定：
#   - 要不要搜索？搜什么关键词？
#   - 搜到结果后，要不要继续搜？
#   - 什么时候停止搜索，开始写报告？
#   - 报告写完后，要不要生成图表？
```

**关键洞察：**
- 对**笨模型**（早期 GPT-3），需要高结构约束防止跑偏
- 对**聪明模型**（GPT-4 / Claude），减少约束才能发挥潜力
- Manus 的成功前提是：底层 LLM 足够聪明，能自主决策

---

## 八、Manus 和 LangGraph / Cursor 的关系

| | Manus | LangGraph | Cursor Composer |
|--|-------|-----------|-----------------|
| **范式** | ReAct | ReAct（底层） | Plan-and-Execute |
| **架构** | 多 Agent 协同 | 图编排（Node+Edge） | 单 Agent 多文件操作 |
| **运行环境** | 云端虚拟机 | 本地/服务器 | 本地编辑器 |
| **工具** | 浏览器+命令行+Python | 依赖用户定义 | 代码操作+编译器 |
| **记忆** | 三层记忆系统 | State + 外接记忆层 | Checkpoint + 文件系统 |

**关系：**
- Manus 的**多 Agent 协同**可以用 LangGraph 的图编排来实现（每个 Agent 是一个 Node，Agent 间通信是 Edge）
- Manus 的**ReAct 循环**是 LangGraph 的底层执行模式
- Manus 的**工具调用**是 LangGraph Node 里可以做的事
- 所以：**Manus 是 LangGraph 的"高级应用"**——用图编排实现了多 Agent 协同

---

## 九、面试考点

| 问题 | 答案 |
|------|------|
| "Manus 是什么？" | 全球首款通用 AI Agent，能自主执行复杂任务（浏览、编码、生成报告） |
| "Manus 和 ChatGPT 的区别？" | ChatGPT 是问答，Manus 是执行。你给目标，它自己做完 |
| "Manus 的核心架构是什么？" | 多 Agent 协同：规划代理 + 执行代理 + 验证代理，运行在独立沙箱中 |
| "Manus 用的是什么范式？" | ReAct（思考→行动→观察→循环），不是 Plan-and-Execute |
| "Manus 为什么用 ReAct？" | 任务边界模糊（"调研市场"），环境动态，需要边做边观察调整 |
| "Manus 的记忆系统有几层？" | 三层：短期记忆（State）、长期记忆（向量库）、工作记忆（LLM 上下文） |
| "Less Structure, More Intelligence 是什么意思？" | 对聪明模型减少结构化约束，让它自主决策，而不是用固定流程限制它 |
| "Manus 和 LangGraph 的关系？" | Manus 的多 Agent 协同可以用 LangGraph 的图编排实现，是 LangGraph 的高级应用 |

---

## 十、一句话总结

> **Manus 是全球首款通用 AI Agent，采用多 Agent 协同架构（规划+执行+验证）和 ReAct 范式（思考→行动→观察→循环），运行在云端沙箱中，能自主调用浏览器、命令行、Python 等工具完成复杂任务。它的"Less Structure, More Intelligence"理念意味着：对聪明的 LLM 减少结构化约束，让它自主决策。从 LangGraph 的视角看，Manus 就是多 Agent 的图编排实现。**

---

> 延伸阅读：
> - OpenManus 开源项目：`https://github.com/mannaandpoem/OpenManus`
> - 官方 Manus：`https://manus.im/`

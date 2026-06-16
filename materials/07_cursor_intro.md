# 学习资料：Cursor 普及 — AI 编辑器的 Agent 模式

> 定位：普及知识面，理解 Cursor 是什么、三种模式、Composer Agent 的工作流、和 LangGraph 的关系
> 来源：综合多个使用经验整理，去掉了实现细节，保留核心概念

---

## 一、Cursor 是什么

**一句话：** Cursor 是**基于 VS Code 的 AI 编辑器**，核心能力是把 AI 集成到代码编辑器的每个环节。

**为什么不是"又一个 Copilot"？**

| 工具 | 定位 | 能力 |
|------|------|------|
| **GitHub Copilot** | 代码补全插件 | 你写代码时它补全，被动 |
| **Cursor** | 完整的 AI 编辑器 | 可以主动帮你改代码、理解项目、执行操作 |
| **Claude Code** | 命令行 AI 助手 | 纯命令行，没有编辑器界面 |

**Cursor 的核心区别：** 它不只是"帮你写代码"，而是**能主动理解你的项目、规划修改、执行多文件操作**。

---

## 二、三种模式（使用场景）

### 1. Chat（聊天）

**场景：** 你写代码时遇到报错，按 `Cmd+K` 弹出对话窗口，问"这个报错是什么意思？"

**特点：**
- 和代码上下文关联：Cursor 会自动把当前文件的代码、报错信息、光标位置传给 AI
- AI 回答时可以直接引用代码片段
- 你可以让 AI "修改这段代码"，它会直接改到文件里

**类比：** 像有一个编程导师坐在你旁边，你指着代码问，他直接帮你改。

```
用户：
  光标在第 15 行，报错 "TypeError: undefined is not a function"
  
Chat 输入："这个报错是什么意思？"

Cursor 自动传给 AI 的上下文：
  - 当前文件前 50 行代码
  - 光标位置（第 15 行）
  - 报错信息
  - 项目类型（React/Node/Python）

AI 回答：
  "第 15 行的 `data.map()` 中，`data` 可能是 undefined。
  建议先检查：`if (!data) return null;`"
  
用户："帮我加上检查"
→ AI 直接修改第 15 行前后的代码
```

---

### 2. Tab（代码补全）

**场景：** 你写代码时，Cursor 预测你接下来要写什么，按 `Tab` 接受。

**特点：**
- 比 Copilot 更智能：能跨文件理解（比如你在 A 文件定义了函数，在 B 文件使用，Tab 能补全正确的函数名）
- 能补全大块代码（不只是单行，可以补全整个函数）
- 能根据注释生成代码（你写注释描述功能，Tab 生成实现）

**类比：** 像有一个非常了解你项目的高级程序员，知道你接下来要写什么。

---

### 3. Composer（多文件 Agent）🔥

**这是 Cursor 的核心 Agent 模式。**

**场景：** 你说"帮我重构这个项目的认证模块，把 JWT 改成 Session"，Composer 会：
1. **规划**：分析哪些文件需要改（auth.js、middleware.js、routes.js）
2. **执行**：逐个文件修改
3. **验证**：检查修改后是否有报错
4. **循环**：如果有问题，修复后再验证

**这就是 Agent：** 不是被动回答你的问题，而是**主动规划、执行、验证**。

---

## 三、Composer 的 Agent 工作流（和 LangGraph 的关系）

Composer 的 Agent 模式本质上就是**图编排**（和 LangGraph 的思想一致）：

```
用户输入："重构认证模块"
    │
    ▼
┌─────────────────────────────────────────┐
│ 规划节点（Planning）                       │
│ 分析项目结构，确定要修改的文件列表          │
│ 输出：["auth.js", "middleware.js", "routes.js"] │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 执行节点（Execution）                     │
│ 逐个文件读取 → 修改 → 保存               │
│ 输出：修改后的文件内容                     │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│ 验证节点（Verification）                  │
│ 运行测试 / 检查语法 / 检查类型              │
│ 条件判断：通过？→ 结束 : 回到执行节点修复   │
└─────────────────────────────────────────┘
    │
    ▼
  通过 → 提交修改
  失败 → 循环回到执行节点
```

**和 LangGraph 的对应关系：**

| Cursor Composer | LangGraph 概念 | 说明 |
|----------------|---------------|------|
| 规划 → 执行 → 验证 | StateGraph（Node + Edge） | 就是图编排 |
| 修改的文件列表 | State（全局状态） | 规划节点输出，执行节点读取 |
| 验证失败 → 回到执行 | 条件边（Conditional Edge） | 循环 = 条件边指回前面的节点 |
| 支持 "Accept / Reject / Modify" | Human-in-the-loop | 人在节点间介入，审批或修改 |
| 每次操作后自动保存 | Checkpoint | 可以回滚到之前的修改点 |

**所以 Cursor 的 Composer = LangGraph 的工业级落地：**
- 用图编排来定义 Agent 工作流
- 每个节点是具体的操作（规划、修改、验证）
- 条件边决定是否需要重试
- 支持人工介入（human-in-the-loop）

---

## 四、Cursor 和 LangGraph 的关系

### 关系1：Cursor 内部可能用 LangGraph（或类似架构）

Cursor 没有公开源码，但从产品行为推断：
- Composer 的"规划→执行→验证"循环 = 图编排
- 支持循环重试 = 条件边
- 支持人在中间审批 = human-in-the-loop
- 支持撤销/回滚 = Checkpoint

这些特征和 LangGraph 的设计完全一致。Cursor 可能内部用了 LangGraph，或者自己实现了类似的图编排引擎。

### 关系2：Cursor 的 Chat 上下文 = LangGraph 的 State

```
Cursor Chat 的上下文：
  - 当前文件代码
  - 光标位置
  - 报错信息
  - 项目类型
  - 最近的操作历史

= LangGraph 的 State（全局共享数据）
```

### 关系3：Cursor 的 Agent 模式 = LangGraph 的图执行

```
Cursor Composer Agent：
  用户输入 → 规划 → 执行 → 验证 → （循环）

= LangGraph 的图编排：
  START → 规划节点 → 执行节点 → 验证节点 → 条件边 → 结束/重试
```

---

## 五、Cursor 和 Claude Code / KimiClaw 的关系

| 工具 | 界面 | Agent 能力 | 记忆系统 |
|------|------|-----------|----------|
| **Cursor** | 编辑器 GUI | Composer 多文件 Agent | 项目上下文（代码文件） |
| **Claude Code** | 命令行 | 代码生成、文件操作 | 有上下文记忆（但有限） |
| **KimiClaw** | 命令行 | 目前无 Agent 模式 | 只有 USER.md（无向量库） |

**Cursor 的优势：**
- 可视化：代码修改直接在编辑器里展示，diff 一目了然
- 集成：和代码编辑器深度集成，不需要切换窗口
- 人介入：每个修改都可以 Accept/Reject/Modify

**Claude Code 的优势：**
- 命令行：适合自动化脚本、CI/CD 集成
- 灵活：可以操作任何文件系统，不限于代码项目

---

## 六、真实使用案例

### 案例：重构 React 组件

```
用户输入：
  "把 UserProfile 组件从 Class 组件改成 Function 组件，
   用 React Hooks 替代生命周期"

Composer Agent 工作流：

[规划节点]
  分析：找到 UserProfile.js
  计划：
    1. 改写 Class 为 Function
    2. componentDidMount → useEffect
    3. this.state → useState
    4. 检查 props 类型是否需要改

[执行节点]
  读取 UserProfile.js
  生成修改后的代码
  保存到 UserProfile.js
  → 展示 diff（红线删除，绿线新增）

[验证节点]
  检查语法错误
  检查 TypeScript 类型（如果有）
  检查 ESLint 规则
  
[条件判断]
  通过？→ 用户看到"Accept / Reject / Modify"按钮
  失败？→ 回到执行节点修复

[用户介入]
  用户点击 "Accept"
  修改生效，文件保存

[Checkpoint]
  这次操作被记录，可以撤销
```

---

## 七、面试考点（普及阶段）

| 问题 | 一句话答案 |
|------|----------|
| "Cursor 是什么？" | 基于 VS Code 的 AI 编辑器，核心是多文件 Agent（Composer） |
| "Cursor 和 Copilot 的区别？" | Copilot 是被动补全，Cursor 能主动规划、执行、验证多文件修改 |
| "Cursor 的 Composer 怎么体现 Agent 概念？" | 规划→执行→验证→循环，自主决策是否需要重试 |
| "Cursor 和 LangGraph 的关系？" | Composer 的"规划→执行→验证"循环就是图编排，和 LangGraph 思想一致 |
| "Cursor 的 Chat 上下文对应 LangGraph 的什么？" | 对应 State（全局共享数据，所有操作节点都能看到） |
| "Cursor 支持人在中间审批，对应 LangGraph 的什么？" | Human-in-the-loop（人在节点间介入） |
| "Cursor 和 Claude Code 的区别？" | Cursor 是编辑器 GUI，Claude Code 是命令行。Cursor 适合可视化代码修改，Claude Code 适合自动化脚本 |

---

## 八、一句话总结

> **Cursor 是 AI 编辑器，核心能力是 Composer 多文件 Agent。它能自主规划修改、执行操作、验证结果、循环修复。这和 LangGraph 的图编排思想一致：规划节点→执行节点→验证节点，条件边决定是否循环。Cursor 的 Chat 上下文就是 LangGraph 的 State，人在中间审批就是 human-in-the-loop。**

---

> 延伸阅读：
> - 官网：`https://www.cursor.com/`
> - Composer 文档：`https://docs.cursor.com/composer`

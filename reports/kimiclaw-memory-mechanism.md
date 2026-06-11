# QClaw / KimiClaw 记忆机制调研（Task 0.3）

> 实测日期：2026-06-11　执行方式：本地文件系统探查
> 结论：**QClaw 有记忆载体，但没有自动记忆引擎**——这正是本项目要补的短板。

## 1. 安装与位置

| 项 | 结果 |
|----|------|
| QClaw 配置目录 | `~/.qclaw`（存在） |
| OpenClaw 身份目录 | `~/.openclaw`（存在，仅 device 身份） |
| Kimi 客户端 | `/Applications/Kimi.app`（存在） |
| CLI（which qclaw/openclaw） | 未在 PATH 找到（通过应用/配置目录运行） |

## 2. 记忆载体（核心）

QClaw 的记忆 = `~/.qclaw/workspace/` 下的 markdown 文件，agent 启动时加载：

| 文件 | 作用 | 是否长期记忆 |
|------|------|-------------|
| `USER.md` | 关于用户：姓名/称呼/时区/在意的事/项目/偏好 | ✅ **用户长期记忆主载体** |
| `SOUL.md` | agent 行为/偏好/边界 | ✅ 偏好类 |
| `IDENTITY.md` | agent 身份（名字/性格/emoji） | agent 侧 |
| `AGENTS.md` / `TOOLS.md` / `HEARTBEAT.md` | 能力/工具/心跳上下文 | 上下文 |
| `BOOTSTRAP.md` | 首次唤醒引导脚本（用完应删除） | 临时 |

- `workspace/` 是 **git 跟踪**的（有 `.git`）→ 记忆变更天然版本化，可回溯/审计。
- 当前 `USER.md` 还是**空模板**（字段未填），`BOOTSTRAP.md` 未删除 → 这是个全新未初始化的 workspace。

## 3. qmemory 是什么（澄清）

`~/.qclaw/qmemory/*.json` **不是**长期记忆，是会话/任务运行态：
```json
{"taskId":"...","agentId":"main","sessionKey":"agent:main:main",
 "status":"running","steps":[],"startedAt":...,"updatedAt":...}
```
→ 类似任务调度/会话状态，本项目只读参考，不作为记忆写入目标。

## 4. 注入机制（关键洞察）

BOOTSTRAP.md 明确写了 QClaw 的记忆工作方式：
- agent 唤醒时读取 workspace 下的 md 文件作为上下文
- 记忆更新靠 **agent 自己手动编辑** USER.md / SOUL.md（"Write it down. Make it real."）
- **没有任何从对话自动提取 / 去重 / 更新记忆的引擎**

→ 结论：**注入是"免费"的**——只要把记忆写进 `USER.md`，QClaw 下次启动自动加载。本项目的价值集中在"写入路径"（自动提取+决策），读取路径复用 QClaw 原生加载。

## 5. 对 Phase 3 的接入契约（据此实现）

- **写入目标**：`~/.qclaw/workspace/USER.md`（markdown，保持其分节格式：Name/What to call them/Timezone/Notes + ## Context）
- **写入方式**：自动提取的记忆 → 映射到 USER.md 对应分节 → 更新（保留 git 历史）
- **冲突/去重**：在写入前由 updater（真实 embedding）决策 ADD/UPDATE/DELETE/NOOP
- **读取/注入**：无需额外开发，QClaw 启动自动加载 USER.md
- **安全（Phase 4）**：因为是直接改 agent 上下文文件，prompt injection 写入恶意记忆的攻击面真实存在 → Task 4.2 的攻击分析有真实落点

## 6. 验收对照

- [x] 明确写出 KimiClaw 读记忆的文件路径：`~/.qclaw/workspace/USER.md`（及同目录 md）
- [x] 明确 Phase 3 记忆层输出兼容格式：USER.md 的 markdown 分节结构

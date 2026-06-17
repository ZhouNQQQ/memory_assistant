# 交接文档：把 kimiclaw-memory 记忆框架接入 Kimi Claw（MCP）

> 给接手 AI：先读"目标"和"当前进度"，再按"接入方式"操作。本文档同时记录了一个**尚未闭环的最后一步**——让 Kimi Claw 真正发现并调用我们的 MCP 工具。

---

## 一、目标

把本仓库 `kimiclaw-memory` 做成一个**可嵌入的 mem0 记忆框架**，通过 **MCP server** 接入 **Kimi Claw 桌面版**，让 Kimi 在对话中能主动调用长期记忆能力（写入/检索/压缩），并与 Kimi Claw 自带的原生记忆**共存、互不侵入**。

- 主集成方式：**MCP server**（跨运行时统一契约）
- 主集成目标：**Kimi Claw 桌面版**（本质是本地运行的 OpenClaw 实例，支持 MCP）
- 共存原则：本框架数据独立存放于 `~/.kimiclaw_memory/`，**绝不读写 Kimi/Daimon 的 vault/transcripts**

## 二、当前进度

**已完成且验证：**
- ✅ 框架本体：Python 包 `kimiclaw_memory`（门面 `Memory`）+ MCP server（控制台入口 `kimiclaw-memory-mcp`，5 个工具）
- ✅ 83 个测试全过（既有 41 + 新增 42）
- ✅ **真实 GLM 链路**跑通：add→事实提取(GLM)→embedding-3→Chroma→search→user_id 隔离
- ✅ **真实 MCP stdio 客户端握手**验证：`initialize` + `tools/list`(5 工具) + `memory_add`/`memory_search` 实跑成功
- ✅ 已推送到 GitHub：`https://github.com/ZhouNQQQ/memory_assistant.git`（main 分支）

**未闭环的最后一步（需要接手处理）：**
- ⚠️ **Kimi Claw 还没真正发现这个 MCP server。** 之前先后写过两个位置都不对/未生效：
  1. `~/.kimi/mcp.json`（这是 kimi-code 命令行工具的配置，已还原）
  2. `…/kimi-desktop/daimon-share/daimon/runtime/kimi-code/home/mcp.json`（写了但 Kimi 未识别）
- 用户提供的**正确接入方式**见下一节（Kimi Claw = 本地 OpenClaw 实例，用 `openclaw mcp add` 或 `~/.kimi-claw/openclaw.json` / `~/.openclaw/openclaw.json`）。**接手 AI 请按此方式重新配置并验证。**

## 三、我们这个 MCP server 的接入信息

- **运行方式**：本地命令（stdio 传输），不是 http、不是 npx。
- **命令（绝对路径，已验证存在）**：
  ```
  /Users/zhounanqiao12867/Documents/技术文档/AICoding/kimiclaw-memory/.venv/bin/kimiclaw-memory-mcp
  ```
- **必需环境变量**（embedder 走 GLM，Kimi 自身无 embeddings 端点）：
  | 变量 | 值 | 说明 |
  |------|----|----|
  | `ZHIPU_API_KEY` | 智谱 GLM key | LLM 提取 + embedding |
  | `EMBEDDING_MODEL` | `embedding-3` | GLM 嵌入模型 |
  | `KIMI_API_KEY` | `""`（置空） | 阻止误用无 embeddings 端点的 Kimi |
  | `KIMICLAW_DATA_DIR` | `~/.kimiclaw_memory` | 独立数据目录 |
- **暴露的工具**：`memory_add` / `memory_search` / `memory_get_all` / `memory_delete` / `memory_compact`（均需 `user_id`）。
- **返回约定**：成功 `{"ok": true, "data": ...}`；失败 `{"ok": false, "error": ..., "code": "CONFIG|NOT_FOUND|BACKEND"}`。

## 四、Kimi Claw 接入 MCP 的方式（用户提供）

> Kimi Claw 桌面版本质是本地运行的 OpenClaw 实例，支持 MCP 配置。

**方式一：命令行添加（推荐，适用于 http 端点）**
```
openclaw mcp add <name> --transport http <endpoint>
# 例：openclaw mcp add context7 --transport http https://mcp.context7.com/mcp
```
> 注意：本框架是**本地 stdio 命令**，不是 http 端点，因此用**方式二（配置文件）**更合适。

**方式二：编辑配置文件**（适用于本地 command/stdio——我们的情况）
配置文件位于 `~/.kimi-claw/openclaw.json`（或 `~/.openclaw/openclaw.json`），在 `mcp.servers` 下添加：
```json
{
  "mcp": {
    "servers": {
      "kimiclaw-memory": {
        "command": "/Users/zhounanqiao12867/Documents/技术文档/AICoding/kimiclaw-memory/.venv/bin/kimiclaw-memory-mcp",
        "args": [],
        "env": {
          "KIMI_API_KEY": "",
          "ZHIPU_API_KEY": "<填入 GLM key>",
          "EMBEDDING_MODEL": "embedding-3",
          "KIMICLAW_DATA_DIR": "~/.kimiclaw_memory"
        }
      }
    }
  }
}
```

**方式三：Web 界面配置**
桌面版启动后访问 Control UI 的 `/mcp` 页面，图形化管理 MCP 服务器。

**注意事项**
- 需 Allegretto 及以上会员才能使用桌面版。
- 配置后运行 `openclaw mcp reload` 或重启生效。
- 可用 `openclaw mcp doctor --probe` 检测连接状态。

## 五、接手 AI 的下一步（按官方方式，建议顺序）

1. **写入配置**：在 `~/.kimi-claw/openclaw.json`（或 `~/.openclaw/openclaw.json`）的 `mcp.servers` 下添加 `kimiclaw-memory`（command/args/env 见第三节），填入有效 GLM key。
   - 或用命令行：`openclaw mcp add` 系列（http 端点用 `--transport http`；本框架是本地 stdio 命令，优先用配置文件方式）。
2. **生效**：运行 `openclaw mcp reload` 或重启 Kimi Claw。
3. **探活**：`openclaw mcp doctor --probe`，确认 `kimiclaw-memory` 连接正常、列出 5 个工具。
4. **实测**：让 Kimi 调用 `memory_add`（user_id=zhou 写入一条）→ `memory_search`（user_id=zhou 检索），确认返回 `{"ok": true, ...}`。
5. **验证落库**：检查 `~/.kimiclaw_memory/`（应生成 `chroma/` + `history.db`），确认写入的是本框架而非 Kimi 自带记忆。

## 六、关键路径与命令速查

- 项目根：`/Users/zhounanqiao12867/Documents/技术文档/AICoding/kimiclaw-memory`
- venv（Python 3.11）：`.venv/`，MCP 入口 `.venv/bin/kimiclaw-memory-mcp`
- 规格文档：`.kiro/specs/embeddable-memory-framework/`（design/requirements/tasks）
- 本地真实 stdio 探针（验证 server 是否正常）：
  ```bash
  ZHIPU_API_KEY=<key> EMBEDDING_MODEL=embedding-3 \
    .venv/bin/python tests/mcp_stdio_probe.py --live
  ```
- 全量测试：`.venv/bin/python tests/<name>.py`（github_sync/compaction/injector/config/facade/mcp_server/isolation/integration/properties）

## 七、安全提醒

- GLM key 与 GitHub PAT 曾在对话中明文出现，建议**尽快作废轮换**。
- 写入 openclaw.json 的 key 以明文存于本机配置文件（MCP 机制如此，仅本地）。

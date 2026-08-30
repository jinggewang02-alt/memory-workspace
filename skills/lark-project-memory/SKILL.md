---
name: lark-project-memory
description: 将用户明确确认的飞书群聊或文档作为某个 Memory Home Workspace 的只读项目证据。仅当用户明确要求连接、配置、映射、同步或排查飞书项目记忆时使用；不处理通用 Personal Memory、手动文件收录或其他 Provider。
---

# Lark Project Memory

这是 Memory Workspace 的可选 Provider Skill。它负责飞书认证边界、`lark-cli` 只读命令、
来源字段标准化；Memory Core 负责快照、Source Note、Observation、checkpoint、项目脉络和 UI。

将下列命令中的 `<repo-root>` 解析为包含本 Skill 的 Memory Workspace 仓库根目录。

## 硬边界

- 仅当用户明确要求把飞书用于某个 Workspace 时启用；安装、检测到 `lark-cli`、提到飞书
  或 Agent 本身具有飞书能力都不构成授权。
- 未启用时只读本地 `status`，不得探测 CLI、登录、请求 scope 或读取外部资源。
- 只同步用户明确映射且 `sync_mode=direct_execution` 的群聊或文档。不得自动加入搜索结果、
  活跃会话、直接消息、联系人或名称相似的资源。
- 仅使用 user identity 和只读命令。认证、权限、映射或返回协议错误时停止在准确来源边界，
  不得换账号或扩大范围。
- 连接器证据不得自动写入 Exact Profile；项目视图是可重建证据投影，不是已确认正式决策。

## 最短流程

先检查本地状态，不触发外部读取：

```bash
python3 <repo-root>/scripts/connectors.py status <workspace-id> --provider lark --json
```

只有用户明确选择飞书后，才启用并映射确认来源：

```bash
python3 <repo-root>/scripts/connectors.py enable-lark <workspace-id> --json
python3 <repo-root>/scripts/connectors.py map-chat <workspace-id> --chat-id <chat-id> --label "<群名称>" --json
python3 <repo-root>/scripts/connectors.py map-document <workspace-id> --document-id <document-id> --doc "<URL-or-token>" --label "<文档名称>" --json
```

然后只读取映射范围，并读回本地项目视图：

```bash
python3 <repo-root>/scripts/connectors.py sync <workspace-id> --json
python3 <repo-root>/scripts/connectors.py project-view <workspace-id> --json
```

同步前应确认 `lark-cli` 可用且具备映射来源所需的最小只读权限。首次默认使用有界 30 天
窗口；增量从最近成功 checkpoint 开始。每项原始返回由 Memory Core 保存为不可变快照，
成功后再推进 checkpoint。

实现命令、字段和限制见 `<repo-root>/references/lark-connector.md`。

# Adapter Contract（领域适配层设计）

Status: Draft v0.1
Updated: 2026-08-26

本文档定义"通用记忆内核 + 领域适配器"的分层与适配器契约。用于回答两个问题：

1. 接入一个新生态（飞书 / Slack / Notion）时，如何建立**基准记忆**（baseline）。
2. 生态中的**新产物 / 决策**如何及时进入已有的捕获与审阅闭环。

## 1. 分层

```
┌──────────────────────────────────────────────┐
│  领域适配层（adapters/lark, adapters/slack …）│
│  把各生态的资产与事件翻译成内核的候选与事件       │
├──────────────────────────────────────────────┤
│  通用记忆内核（memory_workspace core）          │
│  Workspace（项目知识）+ Profile Memory（事实）  │
│  + capture / candidate / writer / policy       │
└──────────────────────────────────────────────┘
```

- **内核不感知具体生态**：它只认识 Workspace、Profile、capture-event、candidate 这些通用结构。`data-model.md` 已固定这一点。
- **适配器不落自己的库**：适配层只做"翻译"，产出的候选必须经过用户审阅后，由内核的唯一 writer 写入。适配器自己持有的任何中间数据都视为可丢弃缓存。

## 2. 适配器契约

一个领域适配器只需提供三个能力，命名与现有 `onboarding` / `history_sources` 对齐：

| 能力 | 接口 | 作用 |
|---|---|---|
| `discover` | `discover() -> assets` | 盘查：这个生态里"我"有哪些资产（群 / 联系人 / 项目 / 待办） |
| `baseline` | `baseline() -> candidates` | 把盘查结果转成**基准记忆候选**，交内核审阅 |
| `stream` | `stream() -> capture-events` | 把新事件（@我的消息、审批、会议决策）转成 `capture-event` |

约束：

- 每个能力都必须是**只读盘查**，不得搜索当前 Agent 无权访问的范围（沿用 `history_sources.py` 的边界语义）。
- `baseline` 与 `stream` 的产出都走内核已有路径：`proposed → approved → applied`，不直接写正式记忆。
- 适配器用一个稳定字符串标识（如 `lark`、`slack`），存入 `onboarding-state` 的 `source.adapter` 字段（该字段已存在）。

## 3. 痛点 1：初始化基准记忆

现有 `onboarding` 只针对"用户 Query 习惯"。扩成"领域初始化"：

```
onboarding（内核）
  └─ adapter.baseline()
       1. discover：用 lark-cli 盘查群 / 联系人 / 项目 / 待办
       2. 生成候选，分类为：
          - Profile：精确个人事实（写入 Profile Memory）
          - Entities：谁是谁（写入 Workspace entities/）
          - Projects：我参与的项目及关系（写入 Workspace projects/）
          - Inbox：等待我处理的事项（写入 Workspace，或直接进 review inbox）
       3. 用户审阅批准后，由 writer 落库
```

基准记忆的价值在于：之后"某某是谁""我在做哪些项目"这类问题，先从本地记忆答，而不是每次重新调 lark-cli 搜索。

## 4. 痛点 2：后续产物 / 决策及时记入

内核已有 `capture-event → candidate → 审阅 → writer` 的闭环。缺的是触发源。适配器提供 `stream`：

- 事件源：lark-cli 的 `event` 域（`lark-cli event consume`）或轻量定时盘查。
- 翻译规则：一条 @我的消息 = 一个候选待办；一个审批/会议决策 = 一个项目结论候选。
- 沿用内核 async-capture 的分级：`ignore / session / project / profile`，只有后两者生成候选。

这一层不做"实时强一致"，只做"异步、可审阅、少持久化"。

## 5. 落地顺序

1. 先把本契约与 `data-model.md`、`async-capture.md` 对齐（本文件即第一步）。
2. 实现 `lark` 适配器的 `discover` + `baseline`（解决痛点 1）。
3. 实现 `lark` 适配器的 `stream`（解决痛点 2）。
4. 后续生态（slack / notion）复用同一契约，只新增适配器。

## 6. 非目标

- 不做跨生态的统一身份（open_id / slack_id 各生态独立，映射关系作为 Workspace 内容存，不硬编码）。
- 不让适配器绕过审阅直接写正式记忆。
- 不做实时同步；一切仍是"少持久化、先审阅"的本地优先语义。

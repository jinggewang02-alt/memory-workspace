# Lark Connector：项目级只读证据链

Status: Implemented adapter v0.3
Updated: 2026-08-30

Lark Connector 是 Memory Core 之外的可选 Provider Skill / Adapter。默认关闭；非飞书用户不会看到认证、
权限请求或数据读取。它把“发现可能相关资源”和“同步一个项目已确认的来源”分成两条
边界，避免把用户可见的所有聊天与文档一股脑写进项目记忆。

## 1. 最短可用流程

```bash
python3 scripts/connectors.py status <workspace-id> --provider lark --json
python3 scripts/connectors.py enable-lark <workspace-id> --json

# 明确把属于这个项目的来源映射进来；这两步都不读取飞书
python3 scripts/connectors.py map-chat <workspace-id> \
  --chat-id oc_xxx --label "项目执行群" --json
python3 scripts/connectors.py map-document <workspace-id> \
  --document-id doc_xxx --doc "<飞书文档 URL 或 token>" --label "项目 PRD" --json

# 只读取上述映射
python3 scripts/connectors.py sync <workspace-id> --json
python3 scripts/connectors.py project-view <workspace-id> --json
```

同步成功后会产生：

```text
connected/lark/snapshots/<kind>/.../<timestamp>.json 完整、不可变的 lark-cli 返回
connected/lark/observations/<timestamp>-<id>.jsonl  统一 Observation
connected/lark/manifests/<timestamp>-<id>.json      本次覆盖与快照清单
wiki/sources/S-xxx.md                               可引用的 Source Note
.llm-wiki/connectors/lark/checkpoint.json           最近成功边界
.llm-wiki/index/project-memory.json                 可重建项目视图
```

本地 UI 点开 Workspace 后，会展示近期进展、原文明示的决策/下一步、参与者、项目产物、
最近同步时间和 Source Note。该视图不是另一份正式记忆，也不会把启发式分类冒充确认结论。
Agent 也可用 `project-view` 只读同一份本地协议，不会顺带刷新飞书。

## 2. 激活和映射是两道硬边界

- `status` 不探测 `lark-cli`，也不请求飞书权限。
- `enable-lark` 只写本地显式配置，返回 `external_read_performed=false`。
- `map-chat` / `map-document` 只登记用户确认的项目来源，固定
  `sync_mode=direct_execution`，仍不读取飞书。
- `sync` 只遍历这份映射；无映射时直接停止，不改用活跃会话、关键词搜索或直接消息。
- `disable` 停止后续读取，但不删除既有证据与审计状态。

Agent 不能根据安装了本 Skill、发现 `lark-cli`、用户提到“飞书”、本机存在旧快照或当前
产品带有飞书能力而自动启用。群名、关键词匹配和群成员关系也不能代替用户确认映射。

## 3. 发现计划与项目同步

`plan` 保留为可审阅的发现计划：首次默认回看 30 天、最多 30 个活跃会话，并列出文档
发现命令；它自身不执行 `lark-cli`。发现结果只能帮助用户选择项目来源，不能直接成为
项目记忆。

项目 `sync` 使用同一时间状态机，但命令由确认映射生成：

- Chat：`im +chat-messages-list`，user identity，checkpoint 窗口，只读、无 reactions；
- Document：`docs +fetch`，user identity，Markdown + simple detail，只读；
- 首次同步从当前时间向前使用配置的 lookback；之后从最近成功
  `checkpoint.coverage.end` 增量读取；
- 默认至少间隔 24 小时；`--force` 只跳过到期判断，不扩大来源或权限。

广泛发现计划当前需要聊天、消息与 Drive 搜索相关 scope。精确项目同步只调用映射来源所
需的消息或文档读取能力。`lark-cli` 返回必须满足 exit code 0、`ok=true` 且存在 `data`；
认证、scope、映射或返回协议错误时停止并报告当前来源，不能换账号或放宽范围。

## 4. 数据协议

| 协议 | 文件 | 作用 |
|---|---|---|
| Connector Config | `connector-config.schema.json` | 是否由用户显式启用、时间窗与间隔 |
| Source Map | `connector-source-map.schema.json` | Workspace 与确认群聊/文档的一对多关系 |
| Raw Snapshot | 原始 JSON | 完整保留一次 lark-cli 返回，不覆盖 |
| Observation | `external-observation.schema.json` | 消息/文档、参与者、内容摘要、来源与项目路由 |
| Sync Manifest | `sync-manifest.schema.json` | Core 统一记录本次覆盖、结果数、哈希、快照与限制 |
| Checkpoint | `sync-checkpoint.schema.json` | 最近一次完整成功的增量起点 |
| Project View | `project-memory-view.schema.json` | UI/Agent 可读取的可重建项目脉络 |

Observation 的 `routing.project_id` 来自确认映射，置信度为 1，原因码为
`confirmed_source_mapping`；这只说明“来源属于该 Workspace”，不说明其中每句话都值得
长期记忆。连接器固定 `profile_write_allowed=false`，不会从聊天或文档自动改写精确个人
资料。

Project View 目前做保守编译：

- 消息与文档正文摘要进入近期进展；
- 只有原文包含“决定/决策/Decision”才进入决策区；
- 只有原文包含“下一步/待办/TODO/Action”才进入下一步；
- sender 只作为观察到的参与者，不自动推断职责；
- 文档只作为项目产物，不自动把正文提升为正式约束；
- 每个条目必须保留 Source Note 和不可变 snapshot 引用。

## 5. 成功、失败与 checkpoint

Lark Adapter 先完成所有已映射来源的命令读取，再向 Core 提交标准 Sync Bundle，避免某个
scope 失败时留下被误认为完整的本次同步。Adapter 只负责 `lark-cli` 命令与字段翻译；Core
保存原始快照、Observation 和 manifest 后才推进 checkpoint，并由这些证据重建 Project View。

原始文件使用 create-once 写入，路径冲突会拒绝覆盖。Source Note 是可更新的来源目录，
会指向该来源最新快照并保存内容哈希。来源范围内没有记录，不能推出事件没有发生。

## 6. 当前边界

已实现：显式激活、来源映射、发现计划、只读项目同步、完整快照、Observation、manifest、
Source Note、checkpoint、保守 Project Compiler、项目详情 API/UI 和 Workspace 校验。

尚未实现：在 UI 中配置映射或点击同步、对复杂自然语言做高质量语义决策/任务抽取、把
跨项目人物与主题自动汇总到 Personal Work、事件订阅加速，以及其他外部系统的执行器。

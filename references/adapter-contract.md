# Adapter Contract（适配器契约）

Status: Implemented core v0.5
Updated: 2026-08-30

本文档定义“通用 Memory Core + 可选 Provider Adapter”的单向依赖边界。Adapter 负责
外部认证、命令、分页和字段标准化；Core 负责证据持久化、项目脉络、候选、审阅和正式
写入。Core 不导入任何 Provider，安装 Memory Home 本身也不等于启用外部连接器。

```text
Provider Skill / Adapter
  外部授权 → 有界读取 → 字段标准化
                         │ Sync Bundle
                         ▼
Memory Core
  immutable snapshot → Source Note → Observation → checkpoint → Project View / UI
```

## 1. 两条独立输入链路

```text
当前 Agent 对话 ──> Capture Event ──┐
                                     ├─> Resolver ─> Candidate ─> Review ─> Writer
已启用的外部连接器 ─> Observation ──┘
```

- `Capture Event` 表示当前或历史 Agent Query，服务于用户习惯学习和轻量异步捕获。
- `External Observation` 表示聊天、消息、文档、人物等外部证据，必须保留快照和覆盖范围。
- 两者可以指向同一项目，但不能互相冒充，也不能绕过审阅直接修改正式记忆。

### 当前 Agent 的三个宿主接点

任何 Agent 产品都可以用自己的机制实现，不需要在内核中写死产品名：

| 接点 | 最小动作 | 关键边界 |
|---|---|---|
| 用户 Query 到达 | 明确“记住”走正式写入；其余 Query 最多执行一次本地 `event enqueue` | 不在回答关键路径调用模型分类 |
| 当地晚间 | 运行 `capture.py nightly plan --json`，将整批 Episode 交给至多一个 Worker | 未明确保存的内容只能生成 Candidate |
| Agent 回复完成 | 若实际使用了 `memory_reference`，在末尾追加一行 `参考记忆` | 只列实际使用项，不泄露敏感值和绝对路径 |

推荐晚间时间是 22:00。宿主没有定时能力时，在下次启动、空闲期或打开 Review UI 时补跑。
`quickstart` 只声明 `scheduling_status=host_integration_required`，不会伪装已经安装系统定时器。
完整引用格式见 [response-references.md](response-references.md)，夜间批处理见
[async-capture.md](async-capture.md)。

## 2. 激活是硬边界

每个连接器必须有独立的 `Connector Config`，并满足：

- 用户通过明确操作启用；配置中 `activation.explicit=true` 且 `enabled=true`；
- 仅安装 Skill、存在某个 CLI、出现平台名称或 Agent 正在该平台中运行，均不构成授权；
- 未配置或停用时，状态必须返回 `skipped`，读取计划必须是空数组；
- 连接器只使用配置中声明的身份、范围、窗口和限额；权限不足时停止，不能扩大范围变通；
- 停用保留审计记录和既有快照，但禁止后续外部读取。

Core 的 `connector-config.schema.json` 只校验共同字段；每个 Provider 可以在自己的包内维护
更严格的配置 Schema。当前 Lark Adapter 的时间窗、会话上限、identity 和能力枚举由
`memory_workspace/providers/lark/schemas/connector-config.schema.json` 继续约束。

当前附带的执行适配器只有 `lark` provider。Core 已通过非 Lark 的 synthetic provider
链路测试；这证明协议不依赖飞书，不代表其他真实生态的认证与读取已实现。

## 3. 连接器能力

| 能力 | 产出 | 作用 |
|---|---|---|
| `status` | 激活状态与边界 | 不探测外部 CLI，不读取外部数据 |
| `discover` | 有界资产清单 | 发现聊天、文档和外部对象；发现本身不生成长期记忆 |
| `baseline` | 快照与 Observation | 首次启用时建立有时间、数量上限的证据基线 |
| `incremental` | 快照与 Observation | 从最近成功 checkpoint 到当前时间读取增量 |
| `event acceleration` | 提醒或待核对事件 | 可选加速器；不能代替每日 user-identity 对账 |

每个 Adapter 只能读取自己的 Source Map，并将结果转换成标准 Sync Bundle。Bundle 至少包含：

- `provider / connector_id / captured_at / trigger / coverage`；
- 每个已确认来源的 `source`、建议 `snapshot_ref`、命令类别、结果数和原始 bytes；
- 符合 `external-observation.schema.json` 的 Observation；
- 认证、权限、分页和覆盖限制。

Adapter 不直接写 Source Note、checkpoint 或 Project View。Core 在完整校验本批 Bundle 后，
统一执行 create-once 快照、通用 manifest、Source Note、checkpoint 和项目视图编译。任一外部
读取失败时，Adapter 不提交 Bundle，因此 Core 不会留下半成功批次。

`scripts/connectors.py plan` 是当前 Lark Adapter 的结构化发现计划，不执行外部命令。Lark
`sync` 只接受 Source Map 中由用户确认且 `sync_mode=direct_execution` 的来源；发现清单不能
直接喂给同步器。

## 4. 统一 Observation

适配器把外部结果规范化为 `external-observation.schema.json`，至少保留：

- provider、connector、adapter 版本、user/bot 身份和 Workspace；
- 外部对象类型、稳定 ID、父对象、参与者和最小内容摘要；
- 原始 snapshot 引用、命令类别、时间覆盖和完整性；
- 项目解析结果、置信度、原因码和候选资格；
- 隐私分类、暂存期限，以及 `profile_write_allowed=false`（兼容字段，语义为禁止
  Exact Profile 写入）。

外部人物关系可以支持 Workspace `entities/`、项目材料或 Personal Work 的待审提案。
连接器证据绝不自动写入 Exact Profile；只有用户明确要求保存某个个人事实时，才走
Exact Profile 的逐字写入流程。

规范化后的 Candidate 新实现优先使用 schema v3：

- `scope=workspace`：路由到一个明确 Workspace；
- `scope=personal`：再通过 `target_hint.personal_section` 路由到 `work`、
  `relationships`、`preferences`、`learning` 或 `exact_profile`；
- 连接器只允许提议带证据引用的 Personal Work 内容，不能选择 `exact_profile`；
- 旧 schema v1/v2 的 `project/profile` 仍由兼容 Writer 读取，但不是新协议的命名方式。

## 5. 基线与每日增量

首次基线和每日增量共用以下状态机：

```text
未启用 ──> skipped
   │ 用户明确 enable
   ▼
待基线 ──> 近 30 天有界计划 ──> 快照成功 ──> baseline checkpoint
                                            │ 到达最小间隔
                                            ▼
                                      每日增量计划
                                            │
                                  新快照 + checkpoint
```

- 首次默认回看 30 天、最多 30 个活跃会话、每个会话先取 20 条，必要时最多扩到 50 条。
- 每日增量从最近一次成功 `coverage.end` 后 1 秒开始；默认最小间隔 24 小时。
- 新聊天、新参与者和新文档只是待解析证据，不等于值得记忆。
- 只有新证据跨过项目解析、重要性和新颖性门槛，才生成 Candidate。
- checkpoint 只能在不可变快照真实存在后推进，失败或不完整范围必须保留准确边界。

具体 Lark 字段和命令边界见 [lark-connector.md](lark-connector.md)。

## 6. 内核与 UI 边界

- Provider Adapter：显式授权、只读发现/获取、原始结果交接、规范化 Observation。
- Memory Core：不可变快照、Source Note、manifest、checkpoint 和 provider-neutral 读模型。
- Resolver / Compiler：当前先把已确认来源中的活动、显式决策/下一步标记、人物和文档生成
  可重建项目视图；更深入的语义综合仍需 Candidate 审阅。
- Candidate：给出 proposed patch、证据引用、原因码和新颖性。
- UI：只按通用 provider、kind、覆盖和来源字段展示项目脉络，并捕获批准/拒绝意图。
- Writer：唯一可以在批准后改写正式 Workspace 或 Personal Memory 的组件。

## 7. 非目标

- 不因安装 Skill 自动扫描外部账号。
- 不批量导出所有联系人、群、消息或文档。
- 不把“联系人”当成已经确认的项目人物关系。
- 不把 bot 事件流当作用户全部会话的完整记录。
- 不追求实时强一致；优先异步、可审阅、可停止和可追溯。

# Lark Connector：显式启用与读取计划

Status: Implemented protocol and planner v0.1
Updated: 2026-08-27

Lark Connector 是 Memory Workspace 的可选能力。默认关闭；非飞书用户不会看到认证、
权限请求或数据读取。当前代码实现配置、状态、计划、Schema 与 checkpoint，不在计划器内
直接执行 `lark-cli`。

## 1. 启用边界

```bash
python3 scripts/connectors.py status <workspace-id> --provider lark --json
python3 scripts/connectors.py enable-lark <workspace-id> --json
python3 scripts/connectors.py plan <workspace-id> --provider lark --json
```

- `status` 不探测 `lark-cli`，也不请求飞书权限。
- `enable-lark` 只写本地显式配置，返回 `external_read_performed=false`。
- `plan` 仅在连接器启用且到达同步时间时返回命令；否则 `commands=[]`。
- 用户可随时运行 `disable`。停用不会删除已有证据，但会停止新读取。

Agent 不能根据以下信号自动启用：安装了本 Skill、发现 `lark-cli`、用户提到“飞书”、
当前产品带有飞书能力、或本机存在旧快照。

## 2. 默认基线

首次启用后的默认计划：

1. 以 user identity 获取 p2p/group 会话元数据并按活跃时间排序；
2. 只选择最多 30 个活跃会话；读取近 30 天、每个会话先取 20 条消息；
3. 分别发现窗口内由用户创建、编辑或评论过的文档；不使用“打开过”作为高信号；
4. 保存完整、带时间戳、不可改写的原始快照；
5. 规范化为 External Observation，解析项目后再生成待审 Candidate；
6. 只有快照成功存在后，记录 baseline checkpoint。

“最多 30 个活跃会话”不是“近 30 个联系人”：群聊和单聊都属于会话，联系人身份只是
证据的一部分。这个选择比无边界遍历通讯录更贴近项目脉络，也避免把联系人列表误当记忆。

## 3. 每日增量

默认由 Agent 启动或空闲时检查一次，不阻塞当前 Query。只有最近一次成功同步已满 24
小时时才计划读取：

- 时间窗为最近 checkpoint 的 `coverage.end` 到当前时间；
- 对比新会话和已确认会话的新消息；
- 搜索新增、编辑和评论过的文档；
- 批量比较已确认项目文档的 metadata；
- 新聊天对象、新群和新产物先成为 Observation，不直接写入项目页。

可选事件订阅只用于加快发现，不能替代 user identity 的每日增量对账。bot 可见事件并不
等于用户完整的私聊、群聊和历史记录。

## 4. 当前使用的最小字段

| 对象 | 最小字段 | 用途 |
|---|---|---|
| Chat | `chat_id`, `chat_mode`, `name`, `owner_id`, `external`, `p2p_target_*` | 稳定识别、区分群聊/单聊、发现关系变化 |
| Message | `message_id`, `msg_type`, `create_time`, `update_time`, `sender`, `content`, `mentions`, `thread_id`, `deleted`, `updated` | 时间线、参与者、决策/任务证据 |
| Drive search | `title`, `url`, `doc_type`, `edit_time`, `summary_highlighted` | 发现窗口内相关产物 |
| Known document metadata | `doc_token`, `doc_type`, `title`, `url`, `owner_id`, `latest_modify_user`, `create_time`, `latest_modify_time`, `sec_label_name` | 低成本判断已知产物是否变化 |

计划默认需要：`im:chat:read`、`im:message:readonly`、`search:docs:read` 和
`drive:drive.metadata:readonly`。权限缺失时应停止并报告缺失项，不能改用更宽范围或其他账号。

## 5. 快照与 checkpoint

计划执行者应把原始结果保存到 Workspace 的 `connected/lark/` 下，并在 source note 中
记录对象、命令类别、捕获时间、覆盖窗口和限制。`checkpoint` 接受的 `snapshot-ref` 必须
是 Workspace 内真实存在的相对路径：

```bash
python3 scripts/connectors.py checkpoint <workspace-id> \
  --provider lark \
  --coverage-start <ISO-8601> \
  --coverage-end <ISO-8601> \
  --snapshot-ref connected/lark/manifests/<snapshot>.json \
  --trigger first_enable \
  --json
```

认证失败、权限缺失、分页不完整或映射错误时，不推进成功 checkpoint；保留准确的错误和
覆盖边界。不得用“本次没有读到”推断外部事件没有发生。

## 6. 当前实现边界

已实现：显式激活、停用、到期判断、有界计划、配置/Observation/checkpoint Schema、
Workspace 校验和 CLI。尚未实现：在该 CLI 内自动执行 `lark-cli`、把原始返回规范化为
Observation、项目 Resolver 和 Project Compiler。宿主 Agent 可以按计划执行这些步骤，
但不得把“计划已生成”表述为“飞书数据已同步”。

---
name: memory-workspace
description: 维护本机优先、以项目为中心的个人知识库，并精确保存和召回跨项目个人档案。用户要求创建或检查 Workspace/Wiki/知识库、收录文件或证据、查询已有项目知识、维护来源链，或明确要求记住、修改、调用个人资料时使用；为本人创建简历、自我介绍、申请材料或填写表单而需要个人事实时也使用。还支持把潜在知识轻量入队，交给后台 Worker 或 Subagent 异步生成待审候选。项目证据进入 Workspace，邮箱、学号、地址和结构化经历等精确事实进入 Profile Memory；两者不得静默混用。
---

# Memory Workspace

将所有脚本路径解析为相对于本 `SKILL.md` 所在目录。Workspace 操作使用
`scripts/workspace.py`；跨项目精确个人档案使用 `scripts/store.py`；异步暂存与候选审阅
使用 `scripts/capture.py`。不要用对话记忆代替文件存储。

## 先判断数据属于哪里

| 内容 | 存储 | 例子 |
|---|---|---|
| 项目证据与知识 | Workspace | 文档、聊天快照、来源说明、项目决策、专题总结 |
| 用户在某个项目中的想法 | Workspace `personal/` | 项目担忧、假设、反思、待确认偏好 |
| 跨项目精确个人事实 | Profile Memory | 邮箱、学号、地址、分版本账号、结构化经历 |

不得把整个 Profile Memory 复制进 Workspace，也不得从项目材料中自动提取并写回
个人档案。只有用户明确要求保存个人事实时才写 Profile Memory。

## 共同安全边界

- 正式数据只写入本机持久目录；不得静默回退到项目目录或临时目录。
- `raw/` 来源和连接器快照不可变；不得覆盖、改名、移动或删除。
- 密码、验证码、访问令牌、Cookie、私钥和助记词不得保存。
- 不得上传、公开、发送或授权访问任何真实内容，除非用户明确批准该外部动作。
- 写入失败时明确说没有成功；不要用口头承诺代替读回或校验。
- 事实、推断、用户观点和开放问题必须分开；来源范围内没有记录，不代表事件没有发生。

## Workspace 工作流

第一次在一个环境中写入前运行：

```bash
python3 <skill-dir>/scripts/workspace.py doctor
```

若 `status` 不是 `OK`，先处理显示的确切持久路径或权限问题。临时路径只允许
自动化测试显式设置 `MWORK_ALLOW_TRANSIENT=1`，真实任务不得使用。

### 创建和发现

用户明确要求创建 Workspace 时可直接执行，不要额外确认：

```bash
python3 <skill-dir>/scripts/workspace.py init <workspace-id> --name "<名称>"
python3 <skill-dir>/scripts/workspace.py list --json
python3 <skill-dir>/scripts/workspace.py inspect <workspace-id> --json
```

`workspace-id` 使用稳定的小写 kebab-case。不能可靠判断目标 Workspace 且不同选择会
改变数据归属时，只问一个简短问题。

### 收录手动来源

用户明确要求把某个文件加入知识库时：

1. 解析精确文件和目标 Workspace。
2. 执行 `source ingest`；该命令复制不可变原文、计算 SHA-256、创建 Source Note，
   并记录 Operation。
3. 执行 `check`，确认布局、来源哈希、Wiki 链接和 Operation 都通过。
4. 只报告“已收录”及 Source ID。当前命令不会自动提炼事实；不要声称已经完成知识
   综合。

```bash
python3 <skill-dir>/scripts/workspace.py source ingest \
  <workspace-id> /absolute/path/to/file --title "<标题>" --json
python3 <skill-dir>/scripts/workspace.py check <workspace-id> --json
```

相同内容重复收录会返回原 Source ID，不创建副本。识别到私钥标记时必须停止。

### 审阅 Agent 生成的 Wiki 修改

Agent 生成或删除 `wiki/projects/`、`topics/`、`entities/`、`syntheses/` 页面时，默认
不得直接编辑正式文件：

1. 将候选 Markdown 写到任务暂存文件。
2. 用 `operation propose-file` 创建 Proposal，并传入真实存在的 `--input-ref`。
3. 用 `operation show` 读取 Diff，向用户展示将修改的目标、依据和主要变化。
4. 只有用户明确批准后才运行 `approve` 和 `apply`；用户拒绝时运行 `reject`。
5. Apply 后运行 `check`。哈希过期、暂存内容异常或 Wiki 校验失败时，不得绕过保护。

Proposal 阶段只写 `.llm-wiki/operations/`，不修改正式 Wiki。`raw/` 和
`wiki/sources/` 不接受 Proposal 修改。命令与状态机详见
[references/review-index.md](references/review-index.md)。

### 查询已有知识

1. 用 `list` / `inspect` 解析 Workspace；先运行 `query <id> <关键词> --json`。
2. `query` 会重建可丢弃索引。只读取命中结果对应的最小项目页和 Source Note；不要把
   整个索引或 Workspace 注入上下文。
3. 回答先给结论；材料性事实指向对应 `[[sources/S-...]]`，并标明推断、用户观点、
   分歧或证据缺口。
4. 普通问题优先使用已有快照。只有用户要求当前信息，或旧快照会实质影响答案时，
   才进入连接器刷新流程。

当前 CLI 已实现初始化、来源收录、审阅式 Wiki 文件变更、索引和本地查询。连接器刷新
和自动 Claim 综合尚未实现；不要伪造这些命令，也不要绕开 Operation 审计。
详细命令契约见 [references/workspace-cli.md](references/workspace-cli.md)，证据和 UI
边界见 [references/data-model.md](references/data-model.md)。

## 异步候选捕获

异步捕获用于降低当前 Query 的模型等待时间，不得改变“少持久化、先审阅”的边界：

1. 主 Agent 先完成用户任务；同步链路最多执行一次本地 `event enqueue`，不得调用模型
   做捕获分类。
2. 只有环境明确支持回答后仍能存活的后台任务或 Subagent 时才立即派发；否则保留
   pending，由下次启动、空闲任务、UI 或手动 `capture.py` 处理。
3. Worker 将事件解析为 `ignore`、`session`、`project` 或 `profile`。只有后两者产生
   Candidate；Worker 不得直接写正式 Workspace 或 Profile Memory。
4. Candidate 由用户批准后，单一 Writer 才能调用现有 `workspace.py` / `store.py`；完成
   读回或 Workspace check 后再 `mark-applied`。
5. 异步队列保存本机明文暂存，默认关闭，拒绝密码、Token、Cookie、私钥等秘密，并
   应定期清理过期原始事件。

不得把“对当前回答有用”当成“应长期捕获”。一次性操作指令、普通问答、当前输出反馈、
未验证猜测和设计探索默认 `ignore` 或 `session`。只有跨轮次仍有价值、相对稳定、归属清楚
且值得用户审阅的项目结论或个人事实才生成 Candidate。完整协议、命令和跨 Agent 降级见
[references/async-capture.md](references/async-capture.md)。

## Profile Memory 工作流

写入前先运行：

```bash
python3 <skill-dir>/scripts/store.py doctor
```

- 1–3 个明确事实：直接用 `set` 或 `add` 保存，再用 `get` 逐字读回校验。
- 4 个及以上事实、多条结构化经历或文件导入：先给一份 checklist；用户确认后写入并
  逐项读回。用户明确说无需核对时可跳过确认，但不得跳过读回。
- 召回时先 `search` 或 `list`，再只 `get` 当前任务需要的字段；不得注入整份档案。
- 当前输入与档案冲突时，提醒用户选择本次使用哪个值；不得顺便覆盖。
- 删除属于破坏性操作；除非用户已经给出精确 key 或条目并明确要求删除，否则先确认。

```bash
python3 <skill-dir>/scripts/store.py set 学号 "25210170080"
python3 <skill-dir>/scripts/store.py add 实习经历 \
  --field 公司="字节跳动" --field 岗位="AI Infra 实习生"
python3 <skill-dir>/scripts/store.py search 地址
python3 <skill-dir>/scripts/store.py get 快递地址
```

原文必须逐字往返，不要润色、纠错或标准化姓名、号码、日期、大小写和标点。详细的
少量事实、大批量导入、文件解析和场景召回规则见
[references/profile-workflows.md](references/profile-workflows.md)；底层兼容格式见
[references/store-schema.md](references/store-schema.md)。

## 完成标准

- Workspace 写入：命令成功，并且随后 `check` 返回 `status: OK`。
- Agent 生成的 Wiki 修改：Operation 必须经过 `proposed → approved → applied`；除非
  用户在当前请求中明确批准，否则停在 proposed。
- 异步捕获：入队成功只表示 `pending`；Worker 只生成 Candidate。只有用户批准、正式
  写入完成且已有读回/check 证据时，才能标记 `applied`。
- Profile Memory 写入：命令成功，并且 `get` 返回值与用户原文逐字一致。
- 查询：结论与来源、推断、用户观点和缺口边界清楚。
- 未完成的 MVP 能力：明确报告边界，不用手工伪装成功。

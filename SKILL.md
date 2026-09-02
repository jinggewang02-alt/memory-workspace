---
name: memory-workspace
description: 维护本机优先的统一 Memory Home：Personal Memory 保存精确个人资料与跨项目工作脉络，Workspaces 保存项目证据和 Wiki，System 保存异步队列与审计状态。用户要求创建、检查或查询个人记忆、Workspace/Wiki/知识库，收录证据，记住或调用个人资料，或为本人制作简历、自我介绍、申请和表单时使用。还支持历史 Query 习惯学习、异步 Candidate 审阅和显式启用的外部连接器。
---

# Memory Home

将所有脚本路径解析为相对于本 `SKILL.md` 所在目录。统一根目录默认是
`~/.memory-home/`：初始化和迁移使用 `scripts/home.py`，Workspace 使用
`scripts/workspace.py`，精确个人资料使用 `scripts/store.py`，异步暂存与候选审阅使用
`scripts/capture.py`。不要用对话记忆代替文件存储。

## 先判断数据属于哪里

| 内容 | 存储 | 例子 |
|---|---|---|
| 精确个人资料 | Personal `profile/` | 邮箱、学号、地址、分版本账号、结构化经历 |
| 跨项目工作脉络 | Personal `work/` | 当前职责、项目组合、跨项目协作者、长期主题和时间线 |
| 用户偏好和 Query 习惯 | Personal `preferences/` / `learning/` | 已确认偏好、待确认模式、触发 Policy |
| 项目证据与知识 | `workspaces/<id>/` | 文档、聊天快照、来源说明、项目决策、执行进展 |
| 运行和审计状态 | `system/` | Capture、Candidate、Operation、索引、Connector checkpoint |

Workspace 是以事情为中心的事实层，Personal Work 是以用户为中心的跨 Workspace 投影。
Personal Work 只链接 Workspace 证据或 owner capture，不复制外部原文。只有用户明确要求
保存个人事实时才写 `personal/profile/exact.json`。

## 共同安全边界

- 正式数据只写入本机持久目录；不得静默回退到项目目录或临时目录。
- `raw/` 来源和连接器快照不可变；不得覆盖、改名、移动或删除。
- 密码、验证码、访问令牌、Cookie、私钥和助记词不得保存。
- 不得上传、公开、发送或授权访问任何真实内容，除非用户明确批准该外部动作。
- 写入失败时明确说没有成功；不要用口头承诺代替读回或校验。
- 事实、推断、用户观点和开放问题必须分开；来源范围内没有记录，不代表事件没有发生。

## 首次运行：能力协商

在一个新的 Agent、设备或沙箱中首次读写前，先按实际能力准备环境。不要根据产品名
写死分支，也不要因为看到了 `SKILL.md` 就假定命令、Python、文件权限或持久存储可用。

1. 解析本 `SKILL.md` 的实际目录，确认 `scripts/`、`schemas/` 和
   `memory_workspace/` 随 Skill 一起存在。
2. 找到当前环境已经可用的 Python 启动方式；不得为完成自检而擅自安装软件。
3. 运行幂等的一键准备命令：

```bash
<python> <skill-dir>/scripts/quickstart.py --json
```

4. 按返回状态执行：
   - `READY`：统一 Memory Home 已初始化，可以继续目标工作流。
   - `NEEDS_RUNTIME`：报告缺少的运行时；未经授权不得安装。
   - `NEEDS_PERSISTENT_PATH`：选择或请求明确的持久目录，再重新探测。
   - `NEEDS_PERMISSION`：只请求报告中的确切目录权限，再重新探测。
   - `UNSUPPORTED`：停止本地操作，修复 Skill 安装；不得临时拼凑缺失脚本。
5. 当前环境不能执行探针时，不得声称已经本地保存。只能保持只读，或使用用户已经
   配置且提供同等操作契约的 API/工具适配器。

`quickstart --json` 先执行只读能力探针，只有状态为 `READY` 时才初始化目录。它返回
Memory Home、Personal Learning、Workspace 和 Capture 的实际路径，以及首次历史学习状态。
此模式不会启动常驻 UI；收据中的 `ui.status=not_started` 和 `ui.url=null` 必须按字面
理解，不得把 `launch_command` 误报成已经可以访问的链接。

首次准备成功后，面向用户只提供三个简洁路径，不展示内部模块清单：

1. `直接开始（推荐）`：继续正常对话；明确要求立即保存，其余 Query 仅本地暂存并晚间整理。
2. `导入已有内容`：带入已有文件，或当前 Agent 已获授权可见的历史。
3. `连接工作平台`：只在项目确实需要外部证据时，显式配置 Provider 和来源映射。

真实项目、个人资料和文件出现时再按场景路由；没有历史来源时安静延后；没有待审内容时
不主动要求打开 UI。不要再把 Workspace、Profile、文件、UI、历史拆成五个初始化选项。

只有用户需要处理待审内容，且当前 Agent 能确认浏览器与执行环境位于同一台设备、命令
可以保持长期运行时，才运行：

```bash
<python> <skill-dir>/scripts/quickstart.py
```

如果不能确认上述条件，继续使用 CLI/对话审阅，不展示 `127.0.0.1` 链接。服务启动后还要
读取 `/api/health`，只有返回 `status=ready` 时才能告诉用户链接已经可用。

旧版目录存在时，先运行 `migration-plan`。只有用户明确要求迁移后才运行 `migrate`；它
只能复制和哈希校验，不得删除 `~/.personal-memory/` 或 `~/.memory-workspace/`。完整边界见
[references/memory-home.md](references/memory-home.md)。

探针只检查可观察能力，不创建测试文件，也不能预先证明宿主沙箱已经批准第一次真实
写入。完整状态协议和降级规则见
[references/runtime-capabilities.md](references/runtime-capabilities.md)。

### 首次启用：自动学习近 30 天 Query

一键准备返回 `READY` 后，读取收据中的 `history_learning.status`。需要复查时再运行
`capture.py onboarding status --json`。

- 已经是 `awaiting_review` 或 `completed`：不得重复扫描，继续展示或使用现有结果。
- `ready`：运行 `onboarding run`；只处理适配器明确交付、当前 Agent 原本有权读取且位于
  最近 30 天内的用户 Query。标准一键准备在来源有效时会自动完成这一步。
- 当前 Agent 能通过自己的历史工具读取对话、但探针没有现成文件时：按
  `references/adaptive-policy.md` 的标准 JSONL 契约准备最小字段，再运行
  `onboarding run --file <path> --adapter <capability-name>`。不得因用户安装 Skill 就搜索
  其他账号、产品数据库、直接消息或未授权目录。
- 当前环境不能读取历史时：停在 `needs_history_source`，允许用户以后导入文件；不得把
  “看不到”说成“最近 30 天没有对话”。这不会阻塞 Personal Memory、Workspace 或本地
  Candidate 审阅。
- 历史来源存在但损坏或不符合协议时：标记 `needs_attention` 并报告准确错误；不得把这项
  可选能力的错误升级为基础 Memory Home 安装失败。

首次学习会在 `~/.memory-home/personal/learning/` 生成两份待审产物：
`query-habits.md` 是人可读的 Query 习惯草稿，`policies/policy_*.json` 是机器使用的
记忆触发 Policy 草稿。前者描述相对于用户
自身反复出现的交互模式，后者仍只把后来明确保存的 Episode 当正样本；二者不得混为
一套宽松的自动记忆规则。低成本基线生成后，有语义分析能力的 Agent 可在完全相同的
证据范围内提交 `onboarding refine --file <report.json>`，但不得扩大时间、账号或事件
范围。只有用户在 CLI 或本地 UI 中确认后，才激活 Policy 并把 Markdown 标记为
`confirmed`。

“自动”指首次启用后由 Agent 在回答关键路径之外启动；不是静默扩大读取权限，也不
保证每个宿主都存在安装后钩子。宿主支持安装后/后台任务时可立即执行，否则在第一次
加载本 Skill 时执行一次。

### 可选外部证据：通过独立 Provider Skill 接入

Query 习惯学习与外部项目证据同步是两件事。本基础 Skill 只维护统一 Memory Home、
Workspace 证据协议、项目脉络编译和审阅 UI，不负责某个外部生态的认证、命令或字段解析。

- 安装或加载本 Skill 不得探测任何外部 CLI、账号、权限或数据。
- 只有用户明确要求连接某个外部生态时，才加载对应 Provider Skill。
- Provider Skill 负责显式启用、授权边界、来源映射、只读获取与字段标准化；Memory Core
  只接收标准 Sync Bundle，并统一保存不可变快照、Source Note、Observation、manifest、
  checkpoint 和可重建项目视图。
- Provider 未安装、未启用或不可用时，Personal Memory、本地 Workspace、手动来源、
  Query 学习、Capture 和 UI 必须继续正常工作。
- UI 和项目查询只渲染通用 `provider / kind / source` 字段，不包含特定平台判断。

当前仓库附带一个独立的 Lark Provider Skill，位于
`skills/lark-project-memory/SKILL.md`；只有用户明确选择飞书作为项目证据源时才使用。
通用边界见 [references/adapter-contract.md](references/adapter-contract.md)。

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
   若该 Workspace 已有外部项目证据，再读取 `.llm-wiki/index/project-memory.json`；
   这是 provider-neutral 的本地视图，不会刷新任何外部系统。
3. 回答先给结论；材料性事实指向对应 `[[sources/S-...]]`，并标明推断、用户观点、
   分歧或证据缺口。
4. 若最终回答实际使用了查询返回的 Memory Home 内容，在回答最末尾追加一行简短的
   `参考记忆：...`；只列实际使用项，不暴露绝对路径或敏感值。完整格式见
   [references/response-references.md](references/response-references.md)。
5. 普通问题优先使用已有快照。只有用户要求当前信息，或旧快照会实质影响答案时，
   才进入连接器刷新流程。

当前基础 CLI 已实现初始化、来源收录、审阅式 Wiki 文件变更、索引、本地查询，以及
provider-neutral 的证据落盘和可重建项目视图。外部读取由可选 Provider Skill 实现。
自动 Claim 综合与正式项目页改写尚未实现；
不要把项目视图冒充已确认 Wiki，也不要绕开 Operation 审计。
详细命令契约见 [references/workspace-cli.md](references/workspace-cli.md)，证据和 UI
边界见 [references/data-model.md](references/data-model.md)。

## 异步候选捕获

异步捕获用于降低当前 Query 的模型等待时间，不得改变“少持久化、先审阅”的边界：

1. 适配器默认使用 `adaptive`：主 Agent 先完成用户任务；同步链路最多执行一次本地
   `event enqueue`，不得调用模型做捕获分类，也不向每轮注入提示。用户可用
   `MWORK_ASYNC_CAPTURE=off` 关闭本地暂存。
2. 宿主应在当地晚间（建议 22:00）批量运行 `capture.py nightly plan --json`，再派发至多
   一个 Worker。没有调度能力时保留 pending，由下次启动、空闲任务、UI 或手动命令补跑；
   不要为每条 event 创建一个 Worker/Subagent。
3. Worker 先按 conversation 和时间间隔聚合 Episode，再解析为 `ignore`、`session`、
   `project` 或 `profile`。只有后两者产生 Candidate；一个 Episode 默认只产生一个
   Candidate，Worker 不得直接写正式 Workspace 或 Personal Memory。
4. Candidate 由用户批准后，才运行 `capture.py candidate apply <id>`。该单一 Writer
   会通过 Workspace Operation 或 Exact Profile 兼容写入流程完成正式写入，完成读回/
   check 后自动生成 application receipt；Writer 失败时 Candidate 保持 `approved`，
   不得手工伪造 applied。
5. 异步队列保存本机明文短期暂存，默认 `adaptive`，拒绝密码、Token、Cookie、私钥等秘密，并
   应定期清理过期原始事件。
6. 历史学习只把后来出现明确 Workspace/Exact Profile 保存行为的 Episode 当正样本；普通
   高频问法不是正样本。Policy 先生成 draft，必须由用户激活；拒绝和“抑制相似项”
   会让下一版 Policy 更保守。

不得把“对当前回答有用”当成“应长期捕获”。一次性操作指令、普通问答、当前输出反馈、
未验证猜测和设计探索默认 `ignore` 或 `session`。只有跨轮次仍有价值、相对稳定、归属清楚
且值得用户审阅的项目结论或个人事实才生成 Candidate。完整协议、命令和跨 Agent 降级见
[references/async-capture.md](references/async-capture.md)；历史导入、Episode、个人 Policy、
反馈和离线回放见 [references/adaptive-policy.md](references/adaptive-policy.md)。

当前 Worker/Writer 仍兼容旧 `project/profile` Candidate：分别映射 Workspace 与 Personal
Exact Profile。新协议 v3 使用 `workspace/personal`，其中 Personal Work Markdown 的自动
提议和应用尚未完成；不得把目录骨架或 Schema 就绪说成已经能够自动维护个人工作脉络。

### 本地 Memory Home 管理台

同机桌面环境已经安装项目依赖时，优先启动 Electron：

```bash
npm run desktop
```

Electron 会调用 `quickstart.py` 幂等初始化 Memory Home、选择 Python 3.10+、在随机回环端口
启动 Core，并在 `/api/health` 就绪后显示窗口。首次引导只提供直接开始、导入已有对话、
连接工作平台三条路径。Provider 路径只解释并承接 Workspace、授权和明确来源映射，不得
自动探测账号或读取外部内容。Renderer 无 Node 和文件系统权限，正式写入仍必须走 Core Writer。

不得为了打开桌面 UI 擅自安装 Node、Electron 或 Python；依赖缺失时报告边界并继续使用 CLI。
Electron 不可用但有同机浏览器时，可以启动兼容 UI：

环境有浏览器且用户要查看 Personal Memory、Workspace 或待审候选时，可以启动最小本地 UI：

```bash
python3 <skill-dir>/scripts/ui.py
```

它只监听本机，并把整个 Memory Home 作为顶层：Personal Exact Profile、Personal Work/
Preferences、Workspaces 与候选审阅并列展示。Profile 值默认不出现在 Home 总览；用户主动
查看时才通过会话令牌读取。用户可明确新增或修改单值 Profile，动作必须经过正式 Profile
写入路径和逐字回读；结构化条目、Personal Markdown 与 Workspace 文件在 UI 中保持只读。
Candidate 批准后，用户可再明确点击“写入正式记忆”，由同一个单一 Writer 完成正式写入、
校验和回执。新环境仍须先完成能力探针，无浏览器或不能保持本地进程时继续使用 CLI，
不得为 UI 擅自安装运行时。`127.0.0.1` 只代表运行服务的那台设备；远程 Agent、临时沙箱
或已经退出的命令都不能向用户承诺该链接可访问。启动进程后必须验证 `/api/health`，验证
失败时停止展示链接并报告准确边界。
详细边界见 [references/local-review-inbox.md](references/local-review-inbox.md)。

## 精确个人资料工作流

写入前先运行：

```bash
python3 <skill-dir>/scripts/store.py doctor
```

- 1–3 个明确事实：直接用 `set` 或 `add` 保存，再用 `get` 逐字读回校验。
- 4 个及以上事实、多条结构化经历或文件导入：先给一份 checklist；用户确认后写入并
  逐项读回。用户明确说无需核对时可跳过确认，但不得跳过读回。
- 召回时先 `search` 或 `list`，再只 `get` 当前任务需要的字段；不得注入整份档案。
- `get --json` 返回 `memory_reference`。如果回答使用了该值，最末尾追加简短
  `参考记忆：<label>`；保存后读回的确认回复同样适用。
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
- Agent 生成的 Wiki 修改：Operation 必须经过 `proposed → approved → applied`。用户在
  当前请求中明确要求把精确内容“记住/保存到指定 Workspace”时可在同一轮完成三步；
  其他情况没有明确批准就停在 proposed。
- 异步捕获：入队成功只表示 `pending`；Worker 只生成 Candidate。只有用户批准、正式
  写入完成且已有读回/check 证据时，才能标记 `applied`。
- 首次历史学习：`onboarding run` 成功、`query-habits.md` 可读且 Policy 仍为 draft 时
  只算 `awaiting_review`；只有用户确认后才算 `completed`，不得自动确认推断习惯。
- 精确个人资料写入：命令成功，并且 `get` 返回值与用户原文逐字一致。
- 查询：结论与来源、推断、用户观点和缺口边界清楚。
- 未完成的 MVP 能力：明确报告边界，不用手工伪装成功。

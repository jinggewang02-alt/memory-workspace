# Memory Workspace

**给任意 Agent 一套本地优先、可审阅、以项目为中心的长期记忆。**

Memory Workspace 不是一个需要长期在线的云服务。用户把它下载到自己的电脑或 Agent 运行环境后，Agent 在统一的 `~/.memory-home/` 中维护个人工作记忆、项目 Workspace、证据、Query 习惯和待审变更。

它想解决的不是“多记几条零散信息”，而是让 Agent 在长期协作中逐渐理解：

- 这个项目是什么，已经发生过什么；
- 哪些人、文档、讨论、决策和执行进展彼此相关；
- 哪些内容是外部事实，哪些只是用户的判断；
- 哪些个人资料需要跨项目精确召回；
- 用户通常在什么情况下希望 Agent 记录，什么情况下不希望被打扰。

> 当前版本已经跑通统一 Memory Home、本地 Workspace、精确个人档案、30 天 Query 习惯学习、异步候选记忆和本地审阅 UI，并新增了显式启用的 Lark Connector 协议与有界读取计划。Personal Work 目前已完成目录与 Candidate v3 路由协议，自动维护工作脉络、连接器执行与更完整的“项目脉络编译”仍在迭代中，详见 [Roadmap](#roadmap)。

## 它如何工作

```text
用户与任意 Agent 对话
        │
        ├─ 1. 首次启用：检查运行环境
        ├─ 2. 获得授权时：分析 Agent 可见的近 30 天用户 Query
        ├─ 3. 日常使用：轻量写入本地异步队列，不阻塞当前回答
        └─ 4. 后台整理：生成可解释的候选记忆
                         │
                         ▼
                  本地 Review UI
                  ├─ 批准 / 拒绝
                  ├─ 查看依据
                  └─ 写入并回读校验
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
       Personal Memory          Workspaces
       个人资料与工作脉络         项目证据与 Wiki
```

### 三个职责清晰、同根存储的层

| 层 | 保存什么 | 关键边界 |
| --- | --- | --- |
| Personal Memory | 精确资料、跨项目职责、协作关系、关注主题、偏好与习惯 | 精确字段逐字保存；工作脉络链接 Workspace 证据 |
| Workspaces | 项目来源、上下文、决策、执行与 Wiki | 材料事实需要链接到来源；推断和事实分开 |
| System | Capture、Candidate、Operation、索引和 Connector 状态 | 运行状态不是长期知识；批准后才写入正式记忆 |

## 当前已经实现

- **本地能力探测**：检查 Python、持久目录、读写权限和可用历史入口，返回明确的准备状态。
- **项目 Workspace CLI**：初始化、检查、摄取不可变来源、查询索引，以及提议—审阅—应用 Wiki 变更。
- **精确个人档案**：支持单值和结构化条目的保存、查询、更新、删除与导出。
- **首次 30 天习惯学习**：仅在 Agent 已获授权且能看到历史时，分析近 30 天用户 Query，生成 `query-habits.md` 和待确认的触发策略。
- **异步记忆链路**：当前对话只进行轻量入队，后台再聚合 Episode、解析去向并生成 Candidate，降低对回答耗时的影响。
- **本地 Review UI**：查看习惯报告和候选记忆，执行批准、拒绝、应用，并展示写入回执。
- **协议与 JSON Schema**：事件、候选、决策、应用回执、策略、Workspace 和索引均有可验证的数据结构。
- **统一 Memory Home**：默认在 `~/.memory-home/` 下并列保存 Personal、Workspaces 和 System；旧目录可先预览、再只复制迁移。
- **可选 Lark Connector 计划器**：只有用户明确启用后，才生成近 30 天基线和每日增量的只读计划；默认不会探测、认证或读取飞书。

## 5 分钟开始使用

### 1. 下载项目

```bash
git clone https://github.com/jinggewang02-alt/memory-workspace.git
cd memory-workspace
```

运行要求：Python 3.10+，可执行本地命令，并拥有一个不会随会话消失的可写目录。

### 2. 检查 Agent 环境

```bash
python3 scripts/bootstrap.py --json
```

探测结果会明确返回 `READY`、`NEEDS_RUNTIME`、`NEEDS_PERSISTENT_PATH`、`NEEDS_PERMISSION` 或 `UNSUPPORTED`。这一步只读，不会修改 Agent 或外部平台。

### 3. 初始化项目 Workspace

```bash
python3 scripts/home.py doctor
python3 scripts/home.py init
python3 scripts/workspace.py doctor
python3 scripts/workspace.py init my-workspace --name "My Workspace"
```

精确个人资料可以单独检查：

```bash
python3 scripts/store.py doctor
```

旧版本用户先预览迁移；确认无冲突后再显式复制，旧文件不会删除：

```bash
python3 scripts/home.py migration-plan --json
python3 scripts/home.py migrate --json
```

### 4. 检查首次历史学习

```bash
python3 scripts/capture.py onboarding status --json
```

如果当前 Agent 具备已授权的历史读取能力，可以执行有边界的近 30 天 Query 学习；如果没有，用户可以显式提供规范化 JSONL 文件：

```bash
python3 scripts/capture.py onboarding run --file /absolute/path/to/history.jsonl
```

系统会先生成可阅读的习惯报告和策略草稿。只有用户确认后，策略才会生效：

```bash
python3 scripts/capture.py habits show
python3 scripts/capture.py onboarding confirm
```

不同 Agent 是否能直接访问历史，取决于它自身提供的能力和用户授权。Memory Workspace 不会静默搜索其他账号、扩大读取范围或绕过平台权限。

如果用户明确希望把飞书作为项目证据源，可以单独启用 Lark Connector：

```bash
python3 scripts/connectors.py status my-workspace --provider lark --json
python3 scripts/connectors.py enable-lark my-workspace --json
python3 scripts/connectors.py plan my-workspace --provider lark --json
```

`enable-lark` 只记录本地授权配置，`plan` 只生成有界的只读计划；两者都不会直接执行
`lark-cli`。未启用时返回空计划，也不会请求飞书权限。详见
[Lark Connector](references/lark-connector.md)。

### 5. 打开本地 UI

```bash
python3 scripts/ui.py
```

然后访问 [http://127.0.0.1:8741/](http://127.0.0.1:8741/)。

UI 当前用于两类操作：

1. 查看首次学习生成的 Query 习惯报告；
2. 审阅、批准、拒绝和应用后台产生的候选记忆。

浏览器不会直接改写正式记忆文件。所有变更都经过本地协议、单一 Writer、回读和校验。

## 用户旅程

1. **安装能力**：用户把仓库交给自己的 Agent，并允许它使用持久的 `~/.memory-home/`。
2. **环境准备**：Agent 根据能力探测结果完成最小必要配置，不依赖平台白名单。
3. **学习习惯**：在用户授权且历史可见时，分析近 30 天 Query，形成可审阅的个人触发策略。
4. **可选外部证据**：只有用户明确启用某个 Connector 时，Agent 才按其配置建立基线并检查增量；非飞书用户不会触发 Lark 流程。
5. **日常协作**：Agent 正常回答；值得保留的内容先异步进入候选区，不阻塞当前 Query，也不直接污染长期记忆。
6. **用户掌控**：用户在本地 UI 查看依据、决定是否保留；获批内容才进入 Personal Memory 或目标 Workspace。

## Agent 如何接入

项目不维护一份写死的平台名单，而是使用能力契约。一个 Agent 只要能够：

- 运行 Python 3.10+ 和本地命令；
- 访问一个持久、可写的本地路径；
- 以明确授权的方式提供历史或实时 Query；
- 按 JSON Schema 读写事件和审阅结果；

就可以接入同一套数据协议。平台适配器负责把各自的输入规范化为统一事件；Workspace、策略和 UI 不感知具体平台名称。

适配器协议见 [references/adapter-contract.md](references/adapter-contract.md)，运行环境约定见 [references/runtime-capabilities.md](references/runtime-capabilities.md)。目前已提供通用历史文件交接方式，以及 Lark 的显式配置、状态、计划和 checkpoint 协议；计划的自动执行与规范化仍是后续工作，其他外部系统也仍属于扩展接口。

## 数据与安全边界

- **Local-first**：正式数据默认保存在用户自己的持久目录中。
- **Evidence-first**：原始来源和外部快照不可变；Wiki 中的重要事实需要能回到来源。
- **Review-before-write**：候选内容默认不直接进入正式记忆。
- **Scope-aware**：只能读取用户明确授权、当前 Agent 可见的数据，不能因为关键词或名称相似扩大范围。
- **Connector opt-in**：外部连接器默认关闭；安装 Skill、检测到 CLI 或提到平台名称都不构成启用授权。
- **Secret-safe**：密码、Cookie、访问令牌、私钥、一次性验证码和金融账号不应进入记忆库。
- **Reversible**：策略可回看、候选可拒绝、应用过程有回执，索引可从正式文件重建。

## 目录结构

```text
memory-workspace/
├── SKILL.md                 # Agent 使用入口与行为边界
├── memory_workspace/        # Python 核心实现
├── scripts/                 # Workspace、记忆、Capture、UI 等 CLI
├── ui/                      # 本地审阅界面
├── schemas/                 # 协议 JSON Schema
├── adapters/                # Agent / 平台适配层
├── references/              # 数据模型、工作流与协议说明
├── examples/schema/         # 协议的有效与无效样例
└── tests/                   # 单元与链路测试
```

用户数据不保存在代码仓库里。默认的本机数据结构见
[Memory Home](references/memory-home.md)：`personal/`、`workspaces/` 和 `system/`
位于同一个 `~/.memory-home/` 根目录下。

## Roadmap

下一阶段的重点不是继续增加零散记忆规则，而是把项目的完整脉络变成一等能力：

- 为 Capture Event、Candidate 和来源补齐稳定的 `project_id`；
- 增加 Project Resolver，把人、文档、聊天和任务解析到同一项目；
- 增加 Project Compiler，持续维护项目的 `overview / context / decisions / execution`；
- 实现 Candidate v3 的 Personal Work 单一 Writer，把跨项目职责、人物关系和主题安全地落到可审阅 Markdown；
- 在 UI 中提供项目首页、人物关系、文档来源、决策链和时间线；
- 完成 Lark 读取计划的执行、不可变快照规范化与项目解析，再为更多外部系统实现同一契约；
- 用用户的真实审阅反馈持续校准个人触发策略，而不是依赖一套全局固定规则。

这些能力会继续遵守同一原则：**先保存证据，再形成推断；先让用户审阅，再改变长期记忆。**

## 开发与验证

运行完整测试：

```bash
python3 -m unittest discover -s tests -v
```

进一步阅读：

- [数据模型](references/data-model.md)
- [异步 Capture 设计](references/async-capture.md)
- [本地 Review Inbox](references/local-review-inbox.md)
- [自适应策略](references/adaptive-policy.md)
- [Workspace CLI](references/workspace-cli.md)
- [Memory Home](references/memory-home.md)
- [精确个人资料工作流](references/profile-workflows.md)

## 项目状态

Memory Workspace 目前适合个人、本地、可审阅的 Agent 记忆实验与持续迭代。它还不是一个提供稳定托管服务的 C 端产品，也不承诺所有 Agent 或外部平台都具备相同的历史访问能力。

如果你正在接入新的 Agent，建议先运行能力探测，再根据适配器契约完成最小环境准备。

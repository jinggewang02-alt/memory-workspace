#!/usr/bin/env python3
"""[Codex CLI] UserPromptSubmit hook for Memory Workspace intents.

hook 无法调用 LLM，只做确定性的关键词粗判，把该做的事作为指令注入 agent：
  - 命中 Workspace/Wiki 信号                 → 注入 PROJECT_WIKI 指令
  - 命中"记"信号（记住/存一下/记录…）    → 注入 REMEMBER 指令，让 agent 用 store.py 存
  - 命中文件导入信号                       → 注入 IMPORT_FILE 指令，先 checklist 后存
  - 命中显式召回或个人材料/表单场景         → 注入 RECALL 指令，让 agent 按需 search/get
  - 显式开启异步捕获                       → 只做本地事件入队，可选注入后台处理提示
  - 都不命中                              → 静默放行（零打扰）

Codex 约定：本 hook 的 stdout 会作为 context 注入 agent。
个人档案默认放 ~/.personal-memory；禁止静默回退到项目或临时目录。
"""
import json
import os
import re
import sys

_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
STORE = os.path.join(_SCRIPTS, "store.py")
WORKSPACE = os.path.join(_SCRIPTS, "workspace.py")
CAPTURE = os.path.join(_SCRIPTS, "capture.py")

WORKSPACE_SIGNALS = [
    r"(创建|新建|初始化).{0,12}(workspace|工作区|wiki|知识库)",
    r"(收录|导入|放进|加入|添加).{0,16}(workspace|工作区|wiki|知识库)",
    r"(workspace|工作区|wiki|知识库).{0,16}(收录|导入|检查|诊断|查询|检索|维护)",
    r"(检查|诊断|查询|检索|维护).{0,16}(workspace|工作区|wiki|知识库)",
    r"\b(init|inspect|check|ingest).{0,16}(workspace|wiki)\b",
]

# "记"信号：用户想存东西
REMEMBER_SIGNALS = [
    r"记住", r"记一下", r"记下", r"存一下", r"存起来", r"帮我记", r"记录一下",
    r"\bremember\b", r"\bsave\b", r"note that",
]
FILE_SIGNALS = [
    r"附件", r"文件", r"文档", r"材料", r"简历", r"表格", r"从(这个|这份|附件).{0,12}(整理|提取|读取|记录)",
    r"\b(file|attachment|document|resume|spreadsheet)\b",
    r"(read|import|extract).{0,16}(file|attachment|document|resume)",
]
# 显式"忆"信号：用户直接要求调用已存信息
EXPLICIT_RECALL_SIGNALS = [
    r"用我的", r"我的(邮箱|学号|地址|卡号|电话|手机号|生日|获奖|经历|实习)",
    r"帮我填(一下|写).{0,12}(个人|申请|报名|资料|信息|表)",
    r"我之前(存|记|说)过", r"从我的档案", r"调出", r"取出我的",
    r"my (email|address|student id|phone)", r"fill (in|out)", r"from my (profile|memory)",
]
# 场景"忆"信号：没有提到记忆，但任务通常需要用户本人的事实。
# hook 只负责召回提示；agent 必须先排除通用模板、代码和示例人物任务。
PERSONAL_SCENARIO_SIGNALS = [
    r"自我介绍", r"个人(简介|介绍|陈述|主页|资料|信息)",
    r"(我的|本人).{0,12}(简历|履历|申请|报名|资料|信息)",
    r"帮我(写|改|编辑|完善|准备)(一份|一下|下|这份)?(简历|履历)",
    r"(帮我|为我|我要|我想|准备|填写|完善|提交).{0,20}(校招|求职|入学|学校|奖学金|签证|报名|申请).{0,12}(简历|材料|表|资料|页面)",
    r"(填写|完善|补充|录入).{0,12}(官网|网站|网页|表单|申请页|报名页|账户资料|个人资料|个人信息)",
    r"(官网|网站|网页|表单|申请页|报名页).{0,12}(填写|完善|补充|录入).{0,12}(资料|信息)?",
    r"\b(self[- ]introduction|personal bio|personal profile|personal statement)\b",
    r"\b(my resume|my cv|my application form|my registration form)\b",
    r"\b(fill|complete|update).{0,20}(website|profile|form|application)\b",
]
RECALL_SIGNALS = EXPLICIT_RECALL_SIGNALS + PERSONAL_SCENARIO_SIGNALS

# 这些信号只决定是否进入**异步待评估队列**，不代表内容应该被持久化。
# `MWORK_ASYNC_CAPTURE=all` 会让所有非显式记忆消息进入队列；`signals`
# 只处理下列可能具有跨轮次价值的表达；默认 `off`，不留存对话。
ASYNC_CAPTURE_SIGNALS = [
    r"(最终|正式|已经确认|决定|以后|后续).{0,24}(使用|采用|统一|改成|不再|需要)",
    r"(不要|不能|必须|统一|始终).{0,32}(写死|使用|处理|保存|记录|支持)",
    r"(北极星|核心指标|最终方案|产品要求|项目约束)",
    r"我(长期|一直|通常|更)(关注|偏好|喜欢|习惯)",
    r"我的.{0,20}(是|为|：|:)",
]


def hit(text, patterns):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


PROJECT_WIKI_TMPL = """[memory-workspace] PROJECT_WIKI

用户似乎想操作本机项目知识库。请遵循 memory-workspace skill：

1. 先排除只是在开发一个通用 Wiki 产品、写示例代码或讨论概念的情况；只有实际操作用户的本地知识库时才继续。
2. 第一次写入前运行：python3 {workspace} doctor。只写本机持久目录，不回退到项目或 /tmp。
3. 创建使用 `init`；发现与查询先用 `list --json` / `inspect --json`；明确收录文件时用 `source ingest`。
4. Agent 生成的 Wiki 修改必须先 `operation propose-file` 并展示 Diff；只有用户明确批准后才 `approve` 和 `apply`。
5. 写入后必须运行 `check --json`。只在 status=OK 时报告完成。
6. `source ingest` 只完成不可变收录和来源说明，不代表已经提炼事实或完成综合。
7. 不覆盖 raw 来源，不保存秘密，不上传或公开真实内容，不伪造尚未实现的连接器或自动综合命令。

Workspace CLI：{workspace}"""

REMEMBER_TMPL = """[memory-workspace/profile] REMEMBER

用户似乎想记住某个个人信息。请遵循 memory-workspace skill 的 Profile Memory 工作流：

1. 先运行：python3 {store} doctor。仅向本机持久目录写入；权限失败或路径临时时停止，绝不回退到项目或 /tmp。
2. 判断这是**单值**（邮箱/学号/地址/卡号…）还是**结构化条目**（经历/获奖，含公司/岗位/时间等多字段）。
3. 1–3 个明确事实：无需事前提问，直接落盘并 get 读回校验：
   - 单值：   python3 {store} set <key> "<原文>"
   - 结构化： python3 {store} add <key> --field 字段=值 --field 字段=值 ...
   然后告知："好的，帮你记下来了：<内容>。如果有问题请告诉我，我会帮你修改。"
4. 4 个及以上事实或多条经历：先给一个 checklist 让用户一次性核对，确认后批量保存并逐项 get 校验。
5. **逐字保真**：原文照抄，不要润色、改写、纠正专有名词。

档案文件：{store_hint}"""

IMPORT_FILE_TMPL = """[memory-workspace/profile] IMPORT_FILE

用户想从文件中整理并保存个人信息。请遵循 memory-workspace skill 的 Profile Memory 工作流：

1. 不绑定文件格式或解析工具；使用当前 Agent 环境最合适的现有能力读取文件。无法读取时如实说明。
2. 只提取用户需要保存的个人事实，保留原始拼写、数字和标点；不确定内容标为"待确认"。
3. 给用户一份 checklist，一次性核对；若环境能可靠取得来源位置，可附页码、工作表或段落等依据。确认前不要写入。
4. 用户确认后先运行 python3 {store} doctor，确保路径是可访问的本机持久目录。
5. 用 set/add 批量保存，再逐项 get 读回校验。失败时明确说未保存，绝不回退到项目或 /tmp。

档案文件：{store_hint}"""

RECALL_TMPL = """[memory-workspace/profile] RECALL

用户明确想调用个人信息，或正在进行通常需要本人事实的个人材料/表单任务。请遵循 memory-workspace skill 的 Profile Memory 工作流：

1. 先判断任务是否真的需要用户本人的事实；若只是通用模板、代码或示例人物，忽略本指令。
2. 根据当前场景判断需要哪些字段。字段明确时 search 相关 key；范围较广时先 list 查看 key 概览。
3. 只 get 本次需要的 key（结构化可加 --index N --field 字段），不要读取或展示整份档案。
4. **逐字使用 get 返回的姓名、号码、日期和专有名词**；自我介绍等叙述性文字可以适配场景，但不得改变事实。
5. 缺少必需字段时只询问缺少的内容，不要编造。当前输入与档案冲突时提醒用户确认。
6. 召回是只读操作；除非用户明确要求修改记忆，否则不要把本次内容写回档案。

档案文件：{store_hint}"""

ASYNC_CAPTURE_TMPL = """[memory-workspace] ASYNC_CAPTURE

本轮已通过本地轻量写入进入异步队列：{event_id}。

1. 先完成并返回用户当前 Query；不得等待记忆判断，不得在主链路调用模型做捕获分类。
2. 仅当当前环境支持回答后仍能安全存活的后台任务或 Subagent，且当前没有队列 Worker 时，才派发一个 Worker 批量处理 pending；不要为每个 event 重复创建 Subagent，也不要等待 Worker。
3. 不支持后台任务时保留 pending，之后由空闲任务、下次启动、UI 或 `python3 {capture} event list` 处理。
4. Worker 只能用 `event show` 读取、用 `event resolve` 生成 ignore/session/project/profile 结果；project/profile 只产生候选，不得直接写正式 Workspace 或 Profile Memory。
5. 正式写入仍需用户审阅，并由单一 Writer 通过现有 workspace.py/store.py 流程完成。"""


def _event_string(event, *keys):
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def enqueue_async_capture(event, prompt, *, has_direct_route):
    mode = os.environ.get("MWORK_ASYNC_CAPTURE", "off").strip().lower()
    if mode in {"", "0", "false", "off"}:
        return None
    if mode not in {"all", "signals"}:
        print(
            "[memory-workspace] MWORK_ASYNC_CAPTURE 必须是 off/signals/all；本轮未入队。",
            file=sys.stderr,
        )
        return None
    # 已有明确 Workspace/Profile 写入或召回路径时不重复入队。
    if has_direct_route or (mode == "signals" and not hit(prompt, ASYNC_CAPTURE_SIGNALS)):
        return None
    try:
        retention_days = int(os.environ.get("MWORK_CAPTURE_RETENTION_DAYS", "7"))
    except ValueError:
        print(
            "[memory-workspace] MWORK_CAPTURE_RETENTION_DAYS 不是整数；本轮未入队。",
            file=sys.stderr,
        )
        return None
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    try:
        from memory_workspace.capture import enqueue_event

        return enqueue_event(
            prompt,
            conversation_id=_event_string(event, "conversation_id", "thread_id", "session_id"),
            message_id=_event_string(event, "message_id", "turn_id"),
            workspace_id=(
                _event_string(event, "workspace_id") or os.environ.get("MWORK_WORKSPACE_ID")
            ),
            source_agent="codex-hook",
            retention_days=retention_days,
        )
    except Exception as exc:  # hook 不能因为可选后台能力中断用户 Query
        # 异步候选失败不得拖慢或阻断当前 Query，也不得伪装已经入队。
        print(f"[memory-workspace] async capture skipped: {exc}", file=sys.stderr)
        return None


def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    prompt = event.get("prompt", "")
    if not prompt.strip():
        sys.exit(0)

    # 个人档案默认跨项目通用（放 HOME），不回退到当前项目。
    store_hint = os.path.join(os.path.expanduser("~"), ".personal-memory", "store.json")

    is_remember = hit(prompt, REMEMBER_SIGNALS)
    is_file = hit(prompt, FILE_SIGNALS)
    is_recall = hit(prompt, RECALL_SIGNALS)
    is_workspace = hit(prompt, WORKSPACE_SIGNALS)

    instructions = []
    # 明确提到 Wiki/Workspace 时优先项目知识库，避免把来源文件误存为个人档案。
    if is_workspace:
        instructions.append(PROJECT_WIKI_TMPL.format(workspace=WORKSPACE))
    elif is_remember and is_file:
        instructions.append(IMPORT_FILE_TMPL.format(store=STORE, store_hint=store_hint))
    elif is_remember:
        instructions.append(REMEMBER_TMPL.format(store=STORE, store_hint=store_hint))
    elif is_recall:
        instructions.append(RECALL_TMPL.format(store=STORE, store_hint=store_hint))

    queued = enqueue_async_capture(
        event,
        prompt,
        has_direct_route=is_workspace or is_remember or is_recall,
    )
    if queued and os.environ.get("MWORK_ASYNC_CAPTURE_HINT", "1") != "0":
        instructions.append(
            ASYNC_CAPTURE_TMPL.format(event_id=queued["event_id"], capture=CAPTURE)
        )
    if instructions:
        print("\n\n".join(instructions))
    # 都不命中 → 静默放行
    sys.exit(0)


if __name__ == "__main__":
    main()

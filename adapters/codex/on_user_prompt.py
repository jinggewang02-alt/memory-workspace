#!/usr/bin/env python3
"""[Codex CLI] UserPromptSubmit hook — 个人档案记忆的"记 / 忆"意图识别 + 注入指令。

hook 无法调用 LLM，只做确定性的关键词粗判，把该做的事作为指令注入 agent：
  - 命中"记"信号（记住/存一下/记录…）    → 注入 REMEMBER 指令，让 agent 用 store.py 存
  - 命中文件导入信号                       → 注入 IMPORT_FILE 指令，先 checklist 后存
  - 命中显式召回或个人材料/表单场景         → 注入 RECALL 指令，让 agent 按需 search/get
  - 都不命中                              → 静默放行（零打扰）

Codex 约定：本 hook 的 stdout 会作为 context 注入 agent。
个人档案默认放 ~/.personal-memory；禁止静默回退到项目或临时目录。
"""
import json
import os
import re
import sys

_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
STORE = os.path.join(_SCRIPTS, "store.py")

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


def hit(text, patterns):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


REMEMBER_TMPL = """[personal-memory] REMEMBER

用户似乎想记住某个个人信息。请遵循 personal-memory skill：

1. 先运行：python3 {store} doctor。仅向本机持久目录写入；权限失败或路径临时时停止，绝不回退到项目或 /tmp。
2. 判断这是**单值**（邮箱/学号/地址/卡号…）还是**结构化条目**（经历/获奖，含公司/岗位/时间等多字段）。
3. 1–3 个明确事实：无需事前提问，直接落盘并 get 读回校验：
   - 单值：   python3 {store} set <key> "<原文>"
   - 结构化： python3 {store} add <key> --field 字段=值 --field 字段=值 ...
   然后告知："好的，帮你记下来了：<内容>。如果有问题请告诉我，我会帮你修改。"
4. 4 个及以上事实或多条经历：先给一个 checklist 让用户一次性核对，确认后批量保存并逐项 get 校验。
5. **逐字保真**：原文照抄，不要润色、改写、纠正专有名词。

档案文件：{store_hint}"""

IMPORT_FILE_TMPL = """[personal-memory] IMPORT_FILE

用户想从文件中整理并保存个人信息。请遵循 personal-memory skill：

1. 不绑定文件格式或解析工具；使用当前 Agent 环境最合适的现有能力读取文件。无法读取时如实说明。
2. 只提取用户需要保存的个人事实，保留原始拼写、数字和标点；不确定内容标为"待确认"。
3. 给用户一份 checklist，一次性核对；若环境能可靠取得来源位置，可附页码、工作表或段落等依据。确认前不要写入。
4. 用户确认后先运行 python3 {store} doctor，确保路径是可访问的本机持久目录。
5. 用 set/add 批量保存，再逐项 get 读回校验。失败时明确说未保存，绝不回退到项目或 /tmp。

档案文件：{store_hint}"""

RECALL_TMPL = """[personal-memory] RECALL

用户明确想调用个人信息，或正在进行通常需要本人事实的个人材料/表单任务。请遵循 personal-memory skill：

1. 先判断任务是否真的需要用户本人的事实；若只是通用模板、代码或示例人物，忽略本指令。
2. 根据当前场景判断需要哪些字段。字段明确时 search 相关 key；范围较广时先 list 查看 key 概览。
3. 只 get 本次需要的 key（结构化可加 --index N --field 字段），不要读取或展示整份档案。
4. **逐字使用 get 返回的姓名、号码、日期和专有名词**；自我介绍等叙述性文字可以适配场景，但不得改变事实。
5. 缺少必需字段时只询问缺少的内容，不要编造。当前输入与档案冲突时提醒用户确认。
6. 召回是只读操作；除非用户明确要求修改记忆，否则不要把本次内容写回档案。

档案文件：{store_hint}"""


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

    # 同时命中时优先"记"（用户明确要存），否则按命中项注入
    if is_remember and is_file:
        print(IMPORT_FILE_TMPL.format(store=STORE, store_hint=store_hint))
    elif is_remember:
        print(REMEMBER_TMPL.format(store=STORE, store_hint=store_hint))
    elif is_recall:
        print(RECALL_TMPL.format(store=STORE, store_hint=store_hint))
    # 都不命中 → 静默放行
    sys.exit(0)


if __name__ == "__main__":
    main()

# Codex CLI hook

仅在需要为不支持自动 skill 触发的 Codex CLI 环境添加“记 / 忆”意图识别时配置 hook。

## 配置

1. 将整个 `memory-workspace` 目录放入用户本机持久目录，例如 `~/.codex/skills/memory-workspace`。
2. 在 `~/.codex/config.toml` 中启用：

   ```toml
   [features]
   codex_hooks = true
   ```

3. 将 `adapters/codex/hooks.example.json` 合并到 `~/.codex/hooks.json`。
4. 把示例中的 `/ABSOLUTE/PATH/TO/` 替换为 skill 的绝对路径。
5. 完全重启 Codex CLI。

## 验证

1. 说“帮我记住我的学号是 2021110385”。Agent 应直接保存、读回校验并告知结果。
2. 一次提供至少 4 个独立事实。Agent 应先给 checklist，只确认一次。
3. 说“用我的学号填一下”。Agent 应通过 `search` / `get` 逐字取回。
4. 传入当前 Agent 环境能够读取的任意文件并要求记录个人资料。Agent 应先读取、整理 checklist，确认后保存。
5. 说“帮我写一段用于校招的自我介绍”或“帮我填写官网个人资料”。即使没有提到“记忆”，Agent 也应按场景召回相关字段。
6. 说“帮我写一个简历生成器组件”。Agent 应识别为通用代码任务，不读取个人档案。
7. 说“创建一个项目知识库”或“把这个文件收录进我的 Wiki”。Agent 应使用
   `workspace.py`，并在写入后运行 `check`。

## 可选异步候选捕获

异步捕获默认关闭。开启后，Hook 只做本地事件入队，不在用户 Query 的关键链路运行
模型分类：

```bash
export MWORK_ASYNC_CAPTURE=signals
export MWORK_CAPTURE_RETENTION_DAYS=7
export MWORK_ASYNC_CAPTURE_HINT=1
```

- `signals`：只把可能具有跨轮次价值的非显式记忆消息入队；
- `all`：把所有没有明确 Workspace/Profile 路由的消息入队，隐私和存储成本更高；
- `off`：默认值，不留存异步事件；
- `MWORK_ASYNC_CAPTURE_HINT=0`：只入队，不向 Agent 注入后台派发提示，适合外部 Worker/UI。

显式 Workspace/Profile 写入和召回继续走现有流程，不重复进入异步队列。完整 Worker、Candidate
审阅和跨 Agent 降级协议见 [async-capture.md](async-capture.md)。

## 持久化

默认档案是 `~/.personal-memory/store.json`。配置后运行：

```bash
python3 <skill-dir>/scripts/store.py doctor
```

如果路径位于临时目录，必须改用指向本机持久目录的 `PMEM_DIR` 或 `PMEM_FILE`。权限失败时不得回退到项目目录或 `/tmp`。

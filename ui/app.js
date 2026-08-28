"use strict";

const state = {
  token: "",
  status: "proposed",
  candidates: [],
  selectedId: null,
  detail: null,
  revealedContent: null,
  onboarding: null,
  onboardingRunning: false,
  overview: null,
};

const labels = {
  scope: { project: "项目知识", profile: "个人档案" },
  kind: { fact: "事实", preference: "偏好", decision: "决策", learning: "学习" },
  status: { proposed: "等待审阅", approved: "已批准", rejected: "已忽略", applied: "已应用" },
  phase: {
    explore: "探索阶段",
    clarify: "澄清阶段",
    revise: "修改阶段",
    synthesize: "总结阶段",
    commit: "明确保存",
  },
};

const policyRuleLabels = {
  learning_synthesis: "学习总结",
  repeated_brevity: "表达偏好",
  explicit_save_feedback: "明确保存反馈",
  exact_profile_fact: "精确个人信息",
};

const $ = (selector) => document.querySelector(selector);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* 细线条图标（Lucide 风格内联 SVG，无外部依赖） */
const ICONS = {
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  sparkle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.7L19.6 10l-5.7 1.9L12 17.6l-1.9-5.7L4.4 10l5.7-1.9z"/></svg>',
  arrowRight: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>',
};

function icon(name) {
  const wrap = document.createElement("span");
  wrap.className = "icon";
  wrap.innerHTML = ICONS[name] || "";
  return wrap;
}

function formatDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function shortPolicy(value) {
  if (!value) return "未启用";
  return value.replace(/^policy_/, "").split("_")[0];
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  const request = { method: options.method || "GET", headers };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-Memory-Workspace-Token"] = state.token;
    request.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, request);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("本地服务返回了无法读取的响应。");
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "请求失败（" + response.status + "）");
  }
  return payload;
}

let toastTimer;
function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function activateStatus(status) {
  state.status = status;
  state.selectedId = null;
  state.detail = null;
  document.querySelectorAll(".tab").forEach((item) => {
    const active = item.dataset.status === status;
    item.classList.toggle("is-active", active);
    item.setAttribute("aria-selected", active ? "true" : "false");
  });
}

async function loadOverview() {
  const { overview } = await api("/api/overview");
  state.overview = overview;
  renderSetup(overview.readiness);
  $("#stat-proposed").textContent = overview.counts.proposed;
  $("#stat-approved").textContent = overview.counts.approved;
  $("#stat-applied").textContent = overview.counts.applied;
  $("#pending-episodes").textContent = overview.pending_episode_count
    ? overview.pending_episode_count + " 个对话片段等待后台判断"
    : "当前没有待处理的对话片段";
  if (overview.active_policy) {
    $("#policy-name").textContent = "个性化策略";
    const ruleCount = overview.active_policy.enabled_rules.length;
    $("#policy-note").textContent = ruleCount + " 条规则正在谨慎观察";
  } else {
    $("#policy-name").textContent = "未启用";
    $("#policy-note").textContent = "先由你决定，再慢慢学习";
  }
}

function updateSetupItem(selector, value, copy, status) {
  const item = $(selector);
  item.classList.remove("is-loading", "is-ready", "is-optional", "is-error");
  item.classList.add(status);
  item.querySelector("strong").textContent = value;
  item.querySelector("p").textContent = copy;
}

function renderSetup(readiness) {
  const ready = Boolean(readiness?.ready_to_use);
  const command = $("#setup-command");
  $("#setup-state").textContent = ready ? "可以开始" : "需要初始化";
  $("#setup-state").classList.toggle("is-ready", ready);
  $("#setup-title").textContent = ready
    ? "本地记忆已经准备好"
    : "还需要完成一次本地初始化";
  $("#setup-copy").textContent = ready
    ? "现在可以直接让 Agent 记录个人信息或维护项目知识。"
    : "请在项目目录运行一键启动命令，然后刷新此页面。";
  command.hidden = ready;

  updateSetupItem(
    "#setup-memory",
    ready ? "已准备" : "未完成",
    ready ? "数据保存在本机持久目录。" : "尚未找到完整的 Memory Home。",
    ready ? "is-ready" : "is-error",
  );

  const historyStatus = readiness?.history_learning?.status;
  const historyCopy = {
    completed: ["已启用", "个人 Query 习惯已经确认。", "is-ready"],
    awaiting_review: ["等待确认", "草稿已生成，需要你审阅。", "is-optional"],
    ready: ["可以学习", "已发现授权的历史来源。", "is-optional"],
    needs_history_source: ["稍后连接", "不影响现在开始使用。", "is-optional"],
    needs_attention: ["需要检查", "历史来源异常，不影响基础使用。", "is-error"],
  }[historyStatus] || ["按需开启", "不影响现在开始使用。", "is-optional"];
  updateSetupItem("#setup-history", ...historyCopy);

  const workspaceCount = readiness?.workspaces?.count || 0;
  updateSetupItem(
    "#setup-workspaces",
    workspaceCount ? workspaceCount + " 个" : "按需创建",
    workspaceCount
      ? "Agent 可以继续维护已有项目。"
      : "第一次处理真实项目时再创建。",
    workspaceCount ? "is-ready" : "is-optional",
  );
}

function renderSetupError() {
  $("#setup-state").textContent = "无法读取";
  $("#setup-title").textContent = "暂时无法确认本地状态";
  $("#setup-copy").textContent = "请确认本地服务仍在运行，再刷新页面。";
  ["#setup-memory", "#setup-history", "#setup-workspaces"].forEach((selector) => {
    updateSetupItem(selector, "读取失败", "本地状态暂时不可用。", "is-error");
  });
}

function renderHabits(report) {
  const list = $("#habit-list");
  list.replaceChildren();
  const items = report?.habits || [];
  if (!items.length) {
    const empty = element("div", "habit-item");
    empty.append(
      element("h3", "", "暂未发现稳定习惯"),
      element("p", "", "没有把一次性问法误判为长期偏好。之后仍可继续从新反馈中学习。"),
    );
    list.append(empty);
  } else {
    items.forEach((habit) => {
      const evidence = habit.evidence;
      const item = element("article", "habit-item");
      item.append(
        element("h3", "", habit.title),
        element("p", "", habit.observation),
        element(
          "span",
          "",
          evidence.conversation_count + " 个对话 · " +
            evidence.query_count + " 条 Query · 信心 " +
            Math.round(habit.confidence * 100) + "%",
        ),
      );
      list.append(item);
    });
  }
  list.hidden = false;
}

function renderOnboarding() {
  const data = state.onboarding;
  const status = data?.status || "needs_history_source";
  const badge = $("#learning-state");
  const copy = $("#learning-copy");
  const metrics = $("#learning-metrics");
  const list = $("#habit-list");
  const form = $("#history-source-form");
  const actions = $("#learning-actions");
  badge.classList.remove("is-ready");
  metrics.hidden = true;
  list.hidden = true;
  form.hidden = true;
  actions.hidden = true;

  if (status === "needs_history_source") {
    badge.textContent = "可选";
    copy.textContent = "可稍后由当前 Agent 提供它有权限读取的历史，不影响基础使用。";
    form.hidden = false;
    return;
  }
  if (status === "needs_attention") {
    badge.textContent = "需要检查";
    copy.textContent = data.error || "历史来源暂时无法读取，但不影响基础使用。";
    form.hidden = false;
    return;
  }
  if (status === "ready") {
    badge.textContent = state.onboardingRunning ? "正在学习" : "准备就绪";
    copy.textContent = "已发现当前 Agent 配置的历史来源，将在本机分析最近 30 天。";
    return;
  }

  const report = data.habits;
  const coverage = report.coverage;
  const completed = status === "completed";
  // 完成后整个首次学习面板自动收起隐藏，只留下核心审阅区
  $("#learning-panel").hidden = completed;
  badge.textContent = completed ? "已经确认" : "等待你确认";
  badge.classList.add("is-ready");
  copy.textContent = completed
    ? "这份 Query 习惯已经成为 Agent 的个人交互参考，后续反馈仍会形成新草稿。"
    : "已生成一份待确认的个人 Query 习惯报告。它不会把普通高频问法直接变成记忆触发规则。";
  metrics.replaceChildren(
    element("span", "", coverage.conversation_count + " 个对话"),
    element("span", "", coverage.query_count + " 条 Query"),
    element("span", "", coverage.days + " 天窗口"),
    element("span", "", report.habits.length + " 条习惯草稿"),
  );
  metrics.hidden = false;
  renderHabits(report);
  $("#habits-path").textContent = data.run.habits_markdown_path;
  $("#confirm-learning").hidden = completed;
  actions.hidden = false;
}

async function startOnboarding(historyFile = null) {
  if (state.onboardingRunning) return;
  state.onboardingRunning = true;
  renderOnboarding();
  try {
    const payload = await api("/api/onboarding/run", {
      method: "POST",
      body: { history_file: historyFile, days: 30 },
    });
    state.onboarding = payload.onboarding;
    showToast("近 30 天对话已完成本地分析。");
    await loadOverview();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.onboardingRunning = false;
    renderOnboarding();
  }
}

async function loadOnboarding() {
  const payload = await api("/api/onboarding");
  state.onboarding = payload.onboarding;
  renderOnboarding();
  if (
    state.onboarding.status === "ready" &&
    state.onboarding.history_source?.auto_configured
  ) {
    await startOnboarding();
  }
}

function bindOnboarding() {
  // 折叠展开首次学习面板
  $("#learning-toggle").addEventListener("click", () => {
    const body = $("#learning-body");
    const toggle = $("#learning-toggle");
    const expanded = body.hidden;
    body.hidden = !expanded;
    toggle.setAttribute("aria-expanded", String(expanded));
  });
  $("#history-source-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = $("#history-source").value.trim();
    if (!value) {
      showToast("请填写 Agent 提供的本机 JSONL 路径。", true);
      return;
    }
    await startOnboarding(value);
  });
  $("#confirm-learning").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "正在确认…";
    try {
      const payload = await api("/api/onboarding/confirm", {
        method: "POST",
        body: {},
      });
      state.onboarding = payload.onboarding;
      showToast("学习结果已确认，个性化策略已启用。可撤销的历史草稿仍保留在本机。");
      await loadOverview();
      renderOnboarding();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "确认并启用策略";
    }
  });
}

function renderCandidateList() {
  const list = $("#candidate-list");
  list.replaceChildren();
  if (!state.candidates.length) {
    const empty = element("div", "empty-list");
    empty.append(
      element("div", "empty-symbol", ""),
      element("h3", "", "这里已经很清爽"),
      element("p", "", state.status === "proposed" ? "暂时没有需要审阅的内容。" : "这个分类下还没有内容。"),
    );
    empty.querySelector(".empty-symbol").appendChild(icon("check"));
    list.append(empty);
    return;
  }

  state.candidates.forEach((candidate) => {
    const button = element("button", "candidate-item");
    button.type = "button";
    button.dataset.candidateId = candidate.candidate_id;
    button.classList.toggle("is-selected", candidate.candidate_id === state.selectedId);
    button.setAttribute("aria-pressed", candidate.candidate_id === state.selectedId ? "true" : "false");

    const kicker = element("div", "candidate-kicker");
    const type = element(
      "span",
      "type-chip" + (candidate.scope === "profile" ? " is-profile" : ""),
      labels.kind[candidate.kind] || candidate.kind,
    );
    kicker.append(type, element("time", "candidate-time", formatDate(candidate.proposed_at)));
    button.append(
      kicker,
      element("div", "candidate-preview", candidate.content_preview),
      element("p", "candidate-reason", candidate.reason),
    );
    button.addEventListener("click", () => selectCandidate(candidate.candidate_id));
    list.append(button);
  });
}

async function loadCandidates(preferredId = null) {
  $("#candidate-list").replaceChildren(element("div", "loading", "正在整理候选记忆…"));
  const { candidates } = await api("/api/candidates?status=" + encodeURIComponent(state.status));
  state.candidates = candidates;
  const selectedStillExists = candidates.some((item) => item.candidate_id === preferredId);
  state.selectedId = selectedStillExists ? preferredId : candidates[0]?.candidate_id || null;
  renderCandidateList();
  if (state.selectedId) {
    await selectCandidate(state.selectedId);
  } else {
    renderEmptyDetail();
  }
}

function renderEmptyDetail() {
  const pane = $("#detail-pane");
  const empty = element("div", "empty-detail");
  const symbol = element("div", "empty-symbol", "");
  symbol.appendChild(icon("inbox"));
  empty.append(
    symbol,
    element("h3", "", "没有需要展开的内容"),
    element("p", "", "切换分类，或让 Agent 继续整理新的候选记忆。"),
  );
  pane.replaceChildren(empty);
}

async function selectCandidate(candidateId) {
  state.selectedId = candidateId;
  state.revealedContent = null;
  renderCandidateList();
  $("#detail-pane").replaceChildren(element("div", "loading", "正在读取来龙去脉…"));
  try {
    const { detail } = await api("/api/candidates/" + encodeURIComponent(candidateId));
    state.detail = detail;
    renderDetail();
  } catch (error) {
    showToast(error.message, true);
    renderEmptyDetail();
  }
}

function addMetadata(container, candidate) {
  container.append(
    element("span", "type-chip" + (candidate.scope === "profile" ? " is-profile" : ""), labels.scope[candidate.scope] || candidate.scope),
    element("span", "meta-chip", labels.kind[candidate.kind] || candidate.kind),
    element("span", "meta-chip", "信心 " + Math.round(candidate.confidence * 100) + "%"),
  );
  if (candidate.trigger_phase) {
    container.append(element("span", "meta-chip", labels.phase[candidate.trigger_phase] || candidate.trigger_phase));
  }
}

function renderEvidence(container, evidence) {
  if (!evidence.length) {
    container.append(element("p", "", "没有可展示的来源片段。"));
    return;
  }
  const list = element("div", "evidence-list");
  evidence.forEach((item) => {
    const row = element("div", "evidence-item");
    const source = item.source_kind === "history_import" ? "历史对话" : "当前对话";
    row.append(
      element("time", "", source + " · " + formatDate(item.occurred_at)),
      element("p", "", item.message_preview),
    );
    list.append(row);
  });
  container.append(list);
}

function renderSensitiveContent(wrapper, candidate) {
  const box = element("div", "sensitive-box");
  if (state.revealedContent !== null) {
    box.append(
      element("p", "", "这条候选被标记为敏感内容，仅在当前页面显示。"),
      element("div", "detail-title", state.revealedContent),
    );
    wrapper.append(box);
    return;
  }
  box.append(element("p", "", "这条候选可能包含个人信息。内容默认隐藏，只有你主动查看时才从本机读取。"));
  const reveal = element("button", "text-button", "显示敏感内容");
  reveal.type = "button";
  reveal.prepend(icon("eye"));
  reveal.addEventListener("click", async () => {
    try {
      const payload = await api("/api/candidates/" + encodeURIComponent(candidate.candidate_id) + "/reveal", {
        method: "POST",
        body: {},
      });
      state.revealedContent = payload.content;
      renderDetail();
    } catch (error) {
      showToast(error.message, true);
    }
  });
  box.append(reveal);
  wrapper.append(box);
}

function suggestedTarget(candidate) {
  const hint = candidate.target_hint || {};
  if (candidate.scope === "profile") {
    return hint.profile_key ? "profile:" + hint.profile_key : "";
  }
  return hint.workspace_id
    ? "workspace:" + hint.workspace_id + "/wiki/projects/" + hint.workspace_id + "/decisions.md"
    : "";
}

function showApproveForm(pane, candidate) {
  pane.querySelector(".action-form")?.remove();
  const form = element("form", "action-form");
  form.append(element("h4", "", "批准这条记忆"));

  const contentField = element("div", "field");
  const contentLabel = element("label", "", "记忆内容（可修改）");
  const textarea = document.createElement("textarea");
  textarea.name = "edited_content";
  textarea.value = state.revealedContent ?? candidate.content ?? "";
  if (candidate.sensitivity === "sensitive" && state.revealedContent === null) {
    textarea.placeholder = "敏感内容尚未显示；留空会使用原候选内容。";
  }
  contentField.append(contentLabel, textarea);

  const targetField = element("div", "field");
  const targetLabel = element("label", "", "保存位置");
  const target = document.createElement("input");
  target.name = "target_ref";
  target.required = true;
  target.value = suggestedTarget(candidate);
  target.placeholder = candidate.scope === "profile" ? "profile:字段名" : "workspace:项目/相对路径";
  targetField.append(targetLabel, target);

  const actions = element("div", "form-actions");
  const cancel = element("button", "button button-quiet", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", () => form.remove());
  const submit = element("button", "button button-primary", "确认批准");
  submit.type = "submit";
  actions.append(cancel, submit);
  form.append(contentField, targetField, actions);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      await api("/api/candidates/" + encodeURIComponent(candidate.candidate_id) + "/approve", {
        method: "POST",
        body: {
          target_ref: target.value,
          edited_content: textarea.value || null,
          feedback_reason: "approved_via_local_review_inbox",
        },
      });
      showToast("已批准。确认后即可写入正式记忆。");
      activateStatus("approved");
      await Promise.all([loadOverview(), loadCandidates(candidate.candidate_id)]);
    } catch (error) {
      submit.disabled = false;
      showToast(error.message, true);
    }
  });
  pane.append(form);
  textarea.focus();
}

async function applyCandidate(candidate, button) {
  button.disabled = true;
  button.textContent = "正在写入并校验…";
  try {
    await api("/api/candidates/" + encodeURIComponent(candidate.candidate_id) + "/apply", {
      method: "POST",
      body: {},
    });
    showToast("正式写入与校验均已完成。");
    activateStatus("applied");
    await Promise.all([loadOverview(), loadCandidates(candidate.candidate_id)]);
  } catch (error) {
    button.disabled = false;
    button.textContent = "写入正式记忆";
    showToast(error.message, true);
  }
}

function renderApplicationReceipt(pane, application) {
  if (!application) return;
  const section = element("section", "detail-section application-receipt");
  section.append(element("h4", "", "写入回执"));
  if (application.schema_version === 2) {
    const writerLabel = application.writer.kind === "workspace_operation"
      ? "Workspace Operation"
      : "Profile Memory 精确写入";
    section.append(
      element("p", "receipt-title", writerLabel),
      element("p", "", application.verification.summary),
    );
    const checks = element("div", "receipt-checks");
    application.verification.checks.forEach((check) => {
      checks.append(element("span", "receipt-check", "✓ " + check));
    });
    section.append(checks);
    if (application.writer.operation_id) {
      section.append(element("code", "receipt-id", application.writer.operation_id));
    }
  } else {
    section.append(element("p", "", application.verification));
  }
  pane.append(section);
}

function showRejectForm(pane, candidate) {
  pane.querySelector(".action-form")?.remove();
  const form = element("form", "action-form");
  form.append(element("h4", "", "忽略这条候选"));

  const reasonField = element("div", "field");
  reasonField.append(element("label", "", "为什么不需要它？"));
  const select = document.createElement("select");
  [
    ["not_durable", "只对当前对话有用"],
    ["inaccurate", "内容不够准确"],
    ["unclear_scope", "归属不够清楚"],
    ["duplicate", "已经记录过"],
    ["other", "其他原因"],
  ].forEach(([value, label]) => {
    const option = element("option", "", label);
    option.value = value;
    select.append(option);
  });
  reasonField.append(select);

  const checkboxLabel = element("label", "checkbox-field");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkboxLabel.append(checkbox, document.createTextNode("以后尽量少推荐相似内容"));

  const actions = element("div", "form-actions");
  const cancel = element("button", "button button-quiet", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", () => form.remove());
  const submit = element("button", "button button-primary", "确认忽略");
  submit.type = "submit";
  actions.append(cancel, submit);
  form.append(reasonField, checkboxLabel, actions);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      await api("/api/candidates/" + encodeURIComponent(candidate.candidate_id) + "/reject", {
        method: "POST",
        body: { feedback_reason: select.value, suppress_similar: checkbox.checked },
      });
      showToast(checkbox.checked ? "已忽略，策略会减少相似推荐。" : "已忽略这条候选。");
      await Promise.all([loadOverview(), loadCandidates()]);
    } catch (error) {
      submit.disabled = false;
      showToast(error.message, true);
    }
  });
  pane.append(form);
  select.focus();
}

function renderDetail() {
  const detail = state.detail;
  const candidate = detail.candidate;
  const pane = $("#detail-pane");
  pane.replaceChildren();

  const topline = element("div", "detail-topline");
  topline.append(
    element("span", "detail-state", labels.status[detail.status] || detail.status),
    element("time", "detail-state", formatDate(candidate.proposed_at)),
  );
  pane.append(topline);

  if (detail.content_redacted) {
    renderSensitiveContent(pane, candidate);
  } else {
    pane.append(element("h3", "detail-title", candidate.content));
  }

  const metadata = element("div", "detail-metadata");
  addMetadata(metadata, candidate);
  pane.append(metadata);

  const reason = element("section", "detail-section");
  reason.append(
    element("h4", "", "为什么会出现"),
    element("p", "", detail.resolution.reason),
  );
  pane.append(reason);

  if (candidate.policy_version || candidate.policy_rule) {
    const policySection = element("section", "detail-section");
    const policyText = [
      candidate.policy_rule ? "依据：" + (policyRuleLabels[candidate.policy_rule] || candidate.policy_rule) : null,
      candidate.policy_version ? "个性化策略已参与判断" : null,
    ].filter(Boolean).join(" · ");
    policySection.append(element("h4", "", "判断依据"), element("p", "", policyText));
    pane.append(policySection);
  }

  const evidence = element("section", "detail-section");
  evidence.append(element("h4", "", "来自 " + detail.evidence.length + " 条对话证据"));
  renderEvidence(evidence, detail.evidence);
  pane.append(evidence);

  if (detail.status === "proposed") {
    const actions = element("div", "detail-actions");
    const reject = element("button", "button button-quiet", "忽略");
    reject.type = "button";
    reject.addEventListener("click", () => showRejectForm(pane, candidate));
    const approve = element("button", "button button-primary", "批准记录");
    approve.type = "button";
    approve.append(icon("arrowRight"));
    approve.addEventListener("click", () => showApproveForm(pane, candidate));
    actions.append(reject, approve);
    pane.append(actions);
  } else if (detail.status === "approved") {
    const note = candidate.scope === "profile"
      ? "下一步会写入个人档案，并逐字读回校验。已有不同值时会停止，不会静默覆盖。"
      : "下一步会生成并应用 Workspace Operation，通过结构检查和目标读回后才算完成。";
    pane.append(element("div", "status-note", note));
    const actions = element("div", "detail-actions");
    const apply = element("button", "button button-primary", "写入正式记忆");
    apply.type = "button";
    apply.addEventListener("click", () => applyCandidate(candidate, apply));
    actions.append(apply);
    pane.append(actions);
  } else if (detail.status === "rejected") {
    pane.append(element("div", "status-note", "这条候选已被忽略。反馈会参与下一版个人策略的调整。"));
  } else {
    pane.append(element("div", "status-note", "这条记忆已经完成正式写入与校验。"));
    renderApplicationReceipt(pane, detail.application);
  }
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      if (tab.dataset.status === state.status) return;
      activateStatus(tab.dataset.status);
      try {
        await loadCandidates();
      } catch (error) {
        showToast(error.message, true);
      }
    });
  });
}

async function init() {
  bindTabs();
  bindOnboarding();
  try {
    const session = await api("/api/session");
    state.token = session.token;
    await Promise.all([loadOverview(), loadCandidates(), loadOnboarding()]);
  } catch (error) {
    showToast(error.message, true);
    renderSetupError();
    $("#candidate-list").replaceChildren(element("div", "loading", "无法连接本地数据。请确认服务仍在运行。"));
  }
}

init();

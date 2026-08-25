"use strict";

const state = {
  token: "",
  status: "proposed",
  candidates: [],
  selectedId: null,
  detail: null,
  revealedContent: null,
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

async function loadOverview() {
  const { overview } = await api("/api/overview");
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

function renderCandidateList() {
  const list = $("#candidate-list");
  list.replaceChildren();
  if (!state.candidates.length) {
    const empty = element("div", "empty-list");
    empty.append(
      element("div", "empty-symbol", "✓"),
      element("h3", "", "这里已经很清爽"),
      element("p", "", state.status === "proposed" ? "暂时没有需要审阅的内容。" : "这个分类下还没有内容。"),
    );
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
  empty.append(
    element("div", "empty-symbol", "○"),
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
      showToast("已批准，等待 Agent 完成正式写入。");
      await Promise.all([loadOverview(), loadCandidates()]);
    } catch (error) {
      submit.disabled = false;
      showToast(error.message, true);
    }
  });
  pane.append(form);
  textarea.focus();
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
    approve.addEventListener("click", () => showApproveForm(pane, candidate));
    actions.append(reject, approve);
    pane.append(actions);
  } else if (detail.status === "approved") {
    pane.append(element("div", "status-note", "你已经批准这条候选。它仍需由 Agent 通过单一 Writer 写入正式存储，并在校验成功后标记为已应用。"));
  } else if (detail.status === "rejected") {
    pane.append(element("div", "status-note", "这条候选已被忽略。反馈会参与下一版个人策略的调整。"));
  } else {
    pane.append(element("div", "status-note", "这条记忆已经完成正式写入与校验。"));
  }
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      if (tab.dataset.status === state.status) return;
      state.status = tab.dataset.status;
      state.selectedId = null;
      state.detail = null;
      document.querySelectorAll(".tab").forEach((item) => {
        const active = item === tab;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", active ? "true" : "false");
      });
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
  try {
    const session = await api("/api/session");
    state.token = session.token;
    await Promise.all([loadOverview(), loadCandidates()]);
  } catch (error) {
    showToast(error.message, true);
    $("#candidate-list").replaceChildren(element("div", "loading", "无法连接本地数据。请确认服务仍在运行。"));
  }
}

init();

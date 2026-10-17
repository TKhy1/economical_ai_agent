(() => {
  "use strict";

  const { t } = window.i18n;

  const CATEGORIES = {
    expense: [
      "rent", "food", "groceries", "utilities", "transport", "tuition", "healthcare", "phone",
      "entertainment", "shopping", "subscriptions", "travel", "emergency", "other",
      "books", "courses", "software", "career", "workshop",
    ],
    income: ["income", "allowance", "scholarship", "job", "parttime"],
  };

  const state = {
    budgetPeriod: "weekly",
    profile: null,
    lang: "en",
    chat: [],
    chatBusy: false,
    chatEngine: null,
  };

  // Backend validation messages are English; map the known ones so the Chinese UI
  // never shows a raw server string.
  const BACKEND_ERROR_KEYS = {
    "name must be a string": "error_name_invalid",
    "name must not be empty": "error_name_invalid",
    "name must be valid UTF-8 text": "error_name_invalid",
    "name is too long": "error_name_invalid",
    "security_threshold and monthly_income must be numbers": "error_numbers_invalid",
    "amount must be positive": "error_amount_invalid",
    "category is required": "error_category_invalid",
    "category must be a string": "error_category_invalid",
    "category is too long": "error_category_invalid",
    "note must be a string": "error_note_invalid",
    "note is too long": "error_note_invalid",
    "deadline must be a string in YYYY-MM-DD format": "error_deadline_invalid",
    "deadline must be a valid date in YYYY-MM-DD format": "error_deadline_invalid",
    "name and target_amount are required": "err_goal_fields",
    "target_amount must be a number": "error_goal_amounts",
    "target_amount must be positive": "error_goal_amounts",
    "target_amount must be nonnegative": "error_goal_amounts",
    "current_amount must be a number": "error_goal_amounts",
    "current_amount must be nonnegative": "error_goal_amounts",
    "limits must be numbers": "error_budget_limits",
    "risk_level must be conservative, moderate, or aggressive": "error_profile_options",
    "language must be en or zh": "error_profile_options",
    "message is required": "error_message_required",
    "message must be a string": "error_message_required",
    "message is too long": "error_message_too_long",
    "limit must be an integer": "error_limit_invalid",
    "id must be an integer": "error_id_invalid",
    "action expired": "error_action_expired",
    "action not found": "error_action_used",
  };

  // ---- fetch helpers ----------------------------------------------------
  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `Request failed (${res.status})`);
    }
    return res.json();
  }

  function fmt(n) {
    const v = Number(n || 0);
    return (v < 0 ? "-¥" : "¥") + Math.abs(v).toFixed(2);
  }

  function locale() {
    return state.lang === "zh" ? "zh-CN" : "en-US";
  }

  // Timestamps are stored as naive local time, so read the calendar date straight
  // from the string. Handing it to the Date parser would let it guess a timezone
  // and could shift the entry to the previous day.
  function fmtTxnDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
    if (!match) return "";
    const d = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return d.toLocaleDateString(locale());
  }

  function tr(key, params) {
    return t(state.lang, key, params);
  }

  // `t()` falls back to the key itself; for data-driven labels (categories, tiers)
  // prefer the raw value over "cat_xyz".
  function labelOr(key, fallback) {
    const value = tr(key);
    return value === key ? fallback : value;
  }

  function toast(msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  function toastError(err) {
    const message = err && err.message ? err.message : "";
    if (message.startsWith("action already")) {
      toast(tr("error_action_used"));
      return;
    }
    const key = BACKEND_ERROR_KEYS[message];
    if (key) {
      toast(tr(key));
      return;
    }
    const hasCjk = /[\u4e00-\u9fff]/.test(message);
    if (state.lang === "zh" && /[A-Za-z]/.test(message) && !hasCjk) {
      toast(tr("error_request_failed"));
      return;
    }
    toast(message || tr("error_request_failed"));
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ---- i18n rendering ---------------------------------------------------------
  function applyStaticTranslations() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = tr(el.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      el.placeholder = tr(el.dataset.i18nPlaceholder);
    });
    document.getElementById("page-title").textContent = tr("app_title");
    document.getElementById("html-root").lang = state.lang === "zh" ? "zh-CN" : "en";

    const langSeg = document.getElementById("language-segmented");
    langSeg.querySelectorAll(".seg-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.value === state.lang);
    });

    const activeTxnType = document.querySelector("#txn-type-segmented .seg-btn.active");
    populateCategoryOptions(activeTxnType ? activeTxnType.dataset.value : "expense");

    renderChatEngineBadge();
    renderChatMessages();
  }

  // Which backend answers /api/chat: DeepSeek when a key is configured, the rule engine
  // otherwise. The endpoint is secret-free, so it is safe to call from the page.
  async function loadChatEngine() {
    try {
      state.chatEngine = await api("/api/chat/status");
    } catch (err) {
      state.chatEngine = null;
    }
  }

  function renderChatEngineBadge() {
    const badge = document.getElementById("chat-engine-badge");
    if (!badge) return;
    const engine = state.chatEngine;
    if (engine && engine.configured) {
      badge.textContent = tr("chat_engine_llm", { provider: engine.label || engine.provider });
      badge.title = engine.model || "";
      badge.classList.add("live");
    } else {
      badge.textContent = tr("chat_engine_rules");
      badge.title = tr("chat_engine_rules_hint");
      badge.classList.remove("live");
    }
  }

  async function loadLanguage() {
    try {
      const profile = await api("/api/profile");
      state.profile = profile;
      state.lang = profile.language === "zh" ? "zh" : "en";
    } catch (err) {
      state.lang = "en";
    }
  }

  // ---- navigation ---------------------------------------------------------
  function initTabs() {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });
  }

  function switchView(view) {
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.getElementById(`view-${view}`).classList.add("active");
    document.querySelector(`.tab-btn[data-view="${view}"]`).classList.add("active");
    if (view === "home") refreshHome();
    if (view === "transactions") refreshTransactions();
    if (view === "budget") refreshBudget();
    if (view === "goals") refreshGoals();
    if (view === "settings") refreshProfile();
    if (view === "assistant") {
      renderChatMessages();
      document.getElementById("chat-input").focus();
    }
  }

  // ---- clock ---------------------------------------------------------
  function tickClock() {
    const now = new Date();
    const h = now.getHours() % 12 || 12;
    const m = String(now.getMinutes()).padStart(2, "0");
    document.getElementById("clock").textContent = `${h}:${m}`;
  }

  // ---- tier rendering (shared by home + budget) --------------------------
  function tierBarClass(tier, pct) {
    if (pct >= 100) return "danger";
    if (pct >= 80) return "warning";
    return tier;
  }

  function renderTiers(container, tiers) {
    container.innerHTML = "";
    ["survival", "buffer", "learning"].forEach((tier) => {
      const t = tiers[tier];
      const pct = Math.min(t.pct, 100);
      const row = document.createElement("div");
      row.className = "tier-row";
      row.innerHTML = `
        <div class="tier-head">
          <span class="tier-name">${tr(`tier_${tier}`)}</span>
          <span class="tier-amounts">${fmt(t.spent)} / ${fmt(t.limit)}</span>
        </div>
        <div class="tier-bar-track">
          <div class="tier-bar-fill ${tierBarClass(tier, t.pct)}" style="width:${pct}%"></div>
        </div>`;
      container.appendChild(row);
    });
  }

  function insightText(ins) {
    const p = ins.params || {};
    switch (ins.key) {
      case "tier_exceeded":
        return tr("insight_tier_exceeded", { tier: tr(`tier_${p.tier}`), pct: p.pct });
      case "tier_warning":
        return tr("insight_tier_warning", { tier: tr(`tier_${p.tier}`), pct: p.pct });
      case "balance_below_threshold":
        return tr("insight_balance_below_threshold", { balance: fmt(p.balance), threshold: fmt(p.threshold) });
      case "goal_reached":
        return tr("insight_goal_reached", { name: p.name });
      case "weekly_savings_needed":
        return tr("insight_weekly_savings_needed", { name: p.name, amount: fmt(p.amount), deadline: p.deadline });
      case "goal_deadline_passed":
        return tr("insight_goal_deadline_passed", { name: p.name, remaining: fmt(p.remaining) });
      case "on_track":
      default:
        return tr("insight_on_track");
    }
  }

  function renderInsights(container, insights) {
    container.innerHTML = "";
    insights.forEach((ins) => {
      const div = document.createElement("div");
      div.className = `insight ${ins.level}`;
      div.textContent = insightText(ins);
      container.appendChild(div);
    });
  }

  function renderTxnRow(txn) {
    const sign = txn.type === "income" ? "+" : "-";
    const catLabel = labelOr(`cat_${txn.category}`, txn.category);
    const tierLabel = labelOr(`tier_${txn.tier}`, txn.tier);
    const div = document.createElement("div");
    div.className = "txn-row";
    div.innerHTML = `
      <div class="txn-main">
        <span class="txn-cat">${escapeHtml(catLabel)}</span>
        <span class="txn-meta">${tierLabel} · ${fmtTxnDate(txn.occurred_at)} ${escapeHtml(txn.note || "")}</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <span class="txn-amount ${txn.type}">${sign}${fmt(txn.amount)}</span>
        <button class="txn-del" data-id="${txn.id}" title="Delete">✕</button>
      </div>`;
    div.querySelector(".txn-del").addEventListener("click", async () => {
      await api(`/api/transactions/${txn.id}`, { method: "DELETE" });
      toast(tr("toast_txn_deleted"));
      refreshHome();
      refreshTransactions();
      refreshBudget();
    });
    return div;
  }

  function renderTxnList(container, txns, emptyKey) {
    container.innerHTML = "";
    if (!txns.length) {
      container.innerHTML = `<div class="txn-empty">${tr(emptyKey)}</div>`;
      return;
    }
    txns.forEach((txn) => container.appendChild(renderTxnRow(txn)));
  }

  // ---- HOME ---------------------------------------------------------
  async function refreshHome() {
    const [status, insights, txns] = await Promise.all([
      api(`/api/budget/status?period=${state.budgetPeriod}`),
      api(`/api/insights?period=${state.budgetPeriod}`),
      api("/api/transactions?limit=5"),
    ]);

    const periodLabel = tr(state.budgetPeriod === "weekly" ? "period_week" : "period_month");
    document.getElementById("home-balance-label").textContent = tr("home_balance_label", { period: periodLabel });
    document.getElementById("home-net-balance").textContent = fmt(status.net_balance);
    document.getElementById("home-income").textContent = tr("home_income", { amount: fmt(status.income_total) });
    document.getElementById("home-expense").textContent = tr("home_expense", { amount: fmt(status.expense_total) });
    document.getElementById("home-available").textContent = tr("home_available", {
      locked: fmt(status.locked_amount),
      available: fmt(status.available_balance),
    });

    renderTiers(document.getElementById("home-tiers"), status.tiers);
    renderInsights(document.getElementById("insights-list"), insights);
    renderTxnList(document.getElementById("home-recent"), txns, "txn_empty_recent");
  }

  // ---- TRANSACTIONS ---------------------------------------------------------
  function populateCategoryOptions(type) {
    const select = document.getElementById("txn-category");
    const previous = select.value;
    select.innerHTML = "";
    CATEGORIES[type].forEach((cat) => {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = tr(`cat_${cat}`);
      select.appendChild(opt);
    });
    if (CATEGORIES[type].includes(previous)) select.value = previous;
  }

  function initTxnForm() {
    const segmented = document.getElementById("txn-type-segmented");
    segmented.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        segmented.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        populateCategoryOptions(btn.dataset.value);
      });
    });
    populateCategoryOptions("expense");

    document.getElementById("txn-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const type = segmented.querySelector(".seg-btn.active").dataset.value;
      const amount = parseFloat(document.getElementById("txn-amount").value);
      const category = document.getElementById("txn-category").value;
      const note = document.getElementById("txn-note").value.trim();

      if (!amount || amount <= 0) {
        toast(tr("err_valid_amount"));
        return;
      }

      try {
        await api("/api/ledger/add", {
          method: "POST",
          body: JSON.stringify({ amount, type, category, note }),
        });
        document.getElementById("txn-form").reset();
        populateCategoryOptions("expense");
        segmented.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
        segmented.querySelector('[data-value="expense"]').classList.add("active");
        toast(tr("toast_txn_added"));
        refreshTransactions();
        refreshBudget();
      } catch (err) {
        toastError(err);
      }
    });
  }

  async function refreshTransactions() {
    const txns = await api("/api/transactions?limit=200");
    renderTxnList(document.getElementById("txn-list-full"), txns, "txn_empty_all");
  }

  // ---- BUDGET ---------------------------------------------------------
  function initBudgetControls() {
    const segmented = document.getElementById("budget-period-segmented");
    segmented.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        segmented.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.budgetPeriod = btn.dataset.value;
        refreshBudget();
        refreshHome();
      });
    });

    document.getElementById("budget-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const survival_limit = parseFloat(document.getElementById("limit-survival").value) || 0;
      const buffer_limit = parseFloat(document.getElementById("limit-buffer").value) || 0;
      const learning_limit = parseFloat(document.getElementById("limit-learning").value) || 0;
      try {
        await api("/api/budget", {
          method: "PUT",
          body: JSON.stringify({
            period_type: state.budgetPeriod, survival_limit, buffer_limit, learning_limit,
          }),
        });
        toast(tr("toast_budget_saved"));
        refreshBudget();
        refreshHome();
      } catch (err) {
        toastError(err);
      }
    });
  }

  async function refreshBudget() {
    const status = await api(`/api/budget/status?period=${state.budgetPeriod}`);
    renderTiers(document.getElementById("budget-tiers"), status.tiers);
    document.getElementById("limit-survival").value = status.tiers.survival.limit;
    document.getElementById("limit-buffer").value = status.tiers.buffer.limit;
    document.getElementById("limit-learning").value = status.tiers.learning.limit;
  }

  // ---- GOALS ---------------------------------------------------------
  function renderGoalCard(g) {
    const pct = g.target_amount > 0 ? Math.min((g.current_amount / g.target_amount) * 100, 100) : 0;
    const card = document.createElement("div");
    card.className = "card goal-card";
    card.innerHTML = `
      <div class="goal-head">
        <span class="goal-title">${escapeHtml(g.name)}</span>
        <button class="goal-del" title="Delete">✕</button>
      </div>
      <div class="goal-progress-track">
        <div class="goal-progress-fill" style="width:${pct}%"></div>
      </div>
      <div class="goal-meta">
        <span>${tr("goal_of", { current: fmt(g.current_amount), target: fmt(g.target_amount) })}</span>
        <span>${g.deadline ? tr("goal_by", { date: g.deadline }) : tr("goal_no_deadline")}</span>
      </div>
      <div class="goal-add-row">
        <input type="number" step="0.01" min="0.01" placeholder="${tr("placeholder_add_funds")}">
        <button type="button">${tr("btn_add")}</button>
      </div>`;

    card.querySelector(".goal-del").addEventListener("click", async () => {
      await api(`/api/goals/${g.id}`, { method: "DELETE" });
      toast(tr("toast_goal_deleted"));
      refreshGoals();
    });

    const input = card.querySelector("input");
    card.querySelector(".goal-add-row button").addEventListener("click", async () => {
      const add = parseFloat(input.value);
      if (!add || add <= 0) return;
      await api(`/api/goals/${g.id}`, {
        method: "PUT",
        body: JSON.stringify({ current_amount: g.current_amount + add }),
      });
      toast(tr("toast_goal_progress"));
      refreshGoals();
      refreshHome();
    });

    return card;
  }

  async function refreshGoals() {
    const goals = await api("/api/goals");
    const container = document.getElementById("goals-list");
    container.innerHTML = "";
    if (!goals.length) {
      container.innerHTML = `<div class="card txn-empty">${tr("goals_empty")}</div>`;
      return;
    }
    goals.forEach((g) => container.appendChild(renderGoalCard(g)));
  }

  function initGoalForm() {
    document.getElementById("goal-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = document.getElementById("goal-name").value.trim();
      const target_amount = parseFloat(document.getElementById("goal-target").value);
      const deadline = document.getElementById("goal-deadline").value || null;
      if (!name || !target_amount || target_amount <= 0) {
        toast(tr("err_goal_fields"));
        return;
      }
      try {
        await api("/api/goals", {
          method: "POST",
          body: JSON.stringify({ name, target_amount, deadline }),
        });
        document.getElementById("goal-form").reset();
        toast(tr("toast_goal_created"));
        refreshGoals();
      } catch (err) {
        toastError(err);
      }
    });
  }

  // ---- PROFILE ---------------------------------------------------------
  async function refreshProfile() {
    const profile = await api("/api/profile");
    state.profile = profile;
    document.getElementById("profile-name").value = profile.name;
    document.getElementById("profile-income").value = profile.monthly_income;
    document.getElementById("profile-risk").value = profile.risk_level;
    document.getElementById("profile-threshold").value = profile.security_threshold;
  }

  function initProfileForm() {
    document.getElementById("profile-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = document.getElementById("profile-name").value.trim();
      const monthly_income = parseFloat(document.getElementById("profile-income").value) || 0;
      const risk_level = document.getElementById("profile-risk").value;
      const security_threshold = parseFloat(document.getElementById("profile-threshold").value) || 0;
      // A whitespace-only name passes the input's `required` check but is rejected by the API.
      if (!name) {
        toast(tr("err_name_required"));
        return;
      }
      try {
        const profile = await api("/api/profile", {
          method: "PUT",
          body: JSON.stringify({ name, monthly_income, risk_level, security_threshold }),
        });
        state.profile = profile;
        toast(tr("toast_profile_saved"));
        refreshHome();
      } catch (err) {
        toastError(err);
      }
    });
  }

  // ---- ASSISTANT (chat) ---------------------------------------------------------
  function chatReplyText(m) {
    const p = m.params || {};
    switch (m.key) {
      case "chat_missing_amount":
        return tr("chat_missing_amount");
      case "chat_transaction_added": {
        const sign = p.type === "income" ? "+" : "-";
        return tr("chat_transaction_added", {
          amount: sign + fmt(p.amount),
          category: labelOr(`cat_${p.category}`, p.category),
          tier: labelOr(`tier_${p.tier}`, p.tier),
        });
      }
      case "chat_budget_status":
        return tr("chat_budget_status", {
          balance: fmt(p.net_balance),
          available: fmt(p.available_balance),
          survival: fmt(p.survival_remaining),
          buffer: fmt(p.buffer_remaining),
          learning: fmt(p.learning_remaining),
        });
      case "chat_no_goals":
        return tr("chat_no_goals");
      case "chat_goal_not_found":
        return tr("chat_goal_not_found", { name: p.name });
      case "chat_goal_progress":
        if (p.days_overdue) {
          return tr("chat_goal_overdue", {
            name: p.name, pct: p.pct, current: fmt(p.current), target: fmt(p.target), days: p.days_overdue,
          });
        }
        if (p.days_left != null) {
          return tr("chat_goal_progress_deadline", {
            name: p.name, pct: p.pct, current: fmt(p.current), target: fmt(p.target), days: p.days_left,
          });
        }
        return tr("chat_goal_progress", { name: p.name, pct: p.pct, current: fmt(p.current), target: fmt(p.target) });
      case "chat_goals_summary":
        return tr("chat_goals_summary", { count: p.count, names: p.names });
      case "chat_greeting":
        return tr("chat_greeting");
      case "chat_action_preview":
        return tr("chat_action_preview");
      case "chat_action_confirmed":
        return tr("chat_action_confirmed");
      case "chat_action_cancelled":
        return tr("chat_action_cancelled");
      case "chat_request_failed":
        return tr("chat_request_failed");
      case "chat_help":
      default:
        return tr("chat_help");
    }
  }

  function conversationHistory() {
    const history = [];
    state.chat.forEach((m) => {
      if (m.sender === "user" && typeof m.text === "string" && m.text.trim()) {
        history.push({ role: "user", content: m.text });
      } else if (m.sender === "bot") {
        const text = m.text != null ? m.text : chatReplyText(m);
        if (text) history.push({ role: "assistant", content: text });
      }
    });
    return history.slice(-12);
  }

  function actionLabel(action) {
    if (action.type === "confirm_credit") {
      // Same action type covers income confirmations and safe expenses; the card must not
      // call an expense a "credit".
      return action.payload && action.payload.type === "expense"
        ? tr("chat_confirm_expense")
        : tr("chat_confirm_credit");
    }
    return tr("chat_risk_warning");
  }

  function renderChatAction(message) {
    const action = message.action;
    if (!action) return null;
    const card = document.createElement("div");
    const isRiskWarning = action.type === "risk_warning";
    card.className = `chat-action-card ${isRiskWarning ? "risk-warning" : "credit-confirm"}`;
    if (isRiskWarning) {
      card.setAttribute("role", "alert");
      card.setAttribute("aria-live", "assertive");
      card.setAttribute("aria-label", actionLabel(action));
    }
    const detail = document.createElement("div");
    detail.className = "chat-action-title";
    detail.textContent = actionLabel(action);
    card.appendChild(detail);
    const amount = document.createElement("div");
    amount.className = "chat-action-detail";
    amount.textContent = `${fmt(action.payload.amount)} · ${labelOr(`cat_${action.payload.category}`, action.payload.category)}`;
    card.appendChild(amount);

    const buttons = document.createElement("div");
    buttons.className = "chat-action-buttons";
    const primary = document.createElement("button");
    primary.type = "button";
    primary.className = "chat-action-btn primary";
    primary.dataset.actionId = action.id;
    primary.dataset.decision = "confirm";
    primary.textContent = action.type === "confirm_credit" ? tr("chat_confirm") : tr("chat_continue");
    primary.setAttribute("aria-label", action.type === "confirm_credit" ? tr("chat_confirm") : tr("chat_continue"));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "chat-action-btn secondary";
    cancel.dataset.actionId = action.id;
    cancel.dataset.decision = "cancel";
    cancel.setAttribute("aria-label", tr("chat_cancel"));
    cancel.textContent = tr("chat_cancel");
    buttons.append(primary, cancel);
    card.appendChild(buttons);
    return card;
  }

  function renderChatMessages() {
    const container = document.getElementById("chat-messages");
    if (!container) return;
    container.innerHTML = "";
    state.chat.forEach((m) => {
      const wrapper = document.createElement("div");
      wrapper.className = `chat-entry ${m.sender}`;
      const div = document.createElement("div");
      div.className = `chat-bubble ${m.sender}`;
      div.textContent = m.sender === "user" || m.text != null ? m.text : chatReplyText(m);
      wrapper.appendChild(div);
      const actionCard = renderChatAction(m);
      if (actionCard) wrapper.appendChild(actionCard);
      container.appendChild(wrapper);
    });
    if (state.chatBusy) {
      const wrapper = document.createElement("div");
      wrapper.className = "chat-entry bot";
      wrapper.innerHTML = `<div class="chat-bubble bot chat-typing" role="status" aria-live="polite"><span></span><span></span><span></span></div>`;
      container.appendChild(wrapper);
    }
    container.scrollTop = container.scrollHeight;
  }

  async function handleChatAction(event) {
    const button = event.target.closest("button[data-action-id]");
    if (!button) return;
    const card = button.closest(".chat-action-card");
    if (!card || card.dataset.processing === "true") return;
    card.dataset.processing = "true";
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      const response = await api("/api/chat/action", {
        method: "POST",
        body: JSON.stringify({ id: button.dataset.actionId, decision: button.dataset.decision }),
      });
      const message = state.chat.find((m) => m.action && m.action.id === button.dataset.actionId);
      if (message) message.action = null;
      state.chat.push({ sender: "bot", key: response.status === "cancelled" ? "chat_action_cancelled" : "chat_action_confirmed", params: {} });
      renderChatMessages();
      if (response.status === "confirmed") {
        await Promise.all([refreshHome(), refreshBudget(), refreshTransactions()]);
      }
    } catch (err) {
      card.dataset.processing = "false";
      card.querySelectorAll("button").forEach((b) => { b.disabled = false; });
      toastError(err);
    }
  }

  function initChat() {
    state.chat.push({ sender: "bot", key: "chat_greeting", params: {} });
    const homeCta = document.getElementById("home-chat-cta");
    homeCta.addEventListener("click", () => switchView("assistant"));
    const messages = document.getElementById("chat-messages");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const sendButton = form.querySelector("button[type=submit]");
    messages.addEventListener("click", handleChatAction);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (state.chatBusy) return;
      const text = input.value.trim();
      if (!text) return;

      // Build the context before appending the current turn: the API sends it separately.
      const history = conversationHistory();
      state.chat.push({ sender: "user", text });
      input.value = "";
      state.chatBusy = true;
      input.disabled = true;
      sendButton.disabled = true;
      renderChatMessages();

      try {
        const resp = await api("/api/chat", {
          method: "POST",
          body: JSON.stringify({ message: text, history }),
        });
        if (resp.text != null) {
          state.chat.push({ sender: "bot", text: resp.text, action: resp.action });
        } else {
          state.chat.push({ sender: "bot", key: resp.key, params: resp.params, action: resp.action });
        }
        if (resp.transaction) {
          await Promise.all([refreshHome(), refreshBudget(), refreshTransactions()]);
        }
      } catch (err) {
        state.chat.push({ sender: "bot", key: "chat_request_failed", params: {} });
      } finally {
        state.chatBusy = false;
        input.disabled = false;
        sendButton.disabled = false;
        renderChatMessages();
        input.focus();
      }
    });
  }

  // ---- SETTINGS ---------------------------------------------------------
  function initSettingsControls() {
    const segmented = document.getElementById("language-segmented");
    segmented.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const lang = btn.dataset.value;
        if (lang === state.lang) return;
        try {
          const profile = await api("/api/profile", {
            method: "PUT",
            body: JSON.stringify({ language: lang }),
          });
          state.profile = profile;
          state.lang = lang;
          applyStaticTranslations();
          toast(tr("toast_language_saved"));
          refreshHome();
          refreshBudget();
          refreshGoals();
          refreshTransactions();
        } catch (err) {
          toastError(err);
        }
      });
    });
  }

  // ---- init ---------------------------------------------------------
  document.addEventListener("DOMContentLoaded", async () => {
    initTabs();
    initTxnForm();
    initBudgetControls();
    initGoalForm();
    initProfileForm();
    initSettingsControls();
    initChat();
    tickClock();
    setInterval(tickClock, 30000);

    await Promise.all([loadLanguage(), loadChatEngine()]);
    applyStaticTranslations();
    refreshHome();
  });
})();

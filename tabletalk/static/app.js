(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const state = {
    currentManifest: null,
    manifestSchema: null,
    isStreaming: false,
    currentView: "ask",
    selectedSuite: null,
    evalRunning: false,
    pendingSave: null,
    turnCounter: 0,
    charts: [],
  };

  const STAGES = ["scope", "compose", "execute", "ground"];

  function escapeHTML(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function slug(value) {
    return String(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  function formatMs(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "—";
    return amount >= 1000 ? `${(amount / 1000).toFixed(1)}s` : `${Math.round(amount)}ms`;
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(date);
  }

  function showToast(message, duration = 3600) {
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    $("#toast-region").append(toast);
    window.setTimeout(() => toast.remove(), duration);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const message = typeof payload === "object" ? payload.error : payload;
      throw new Error(message || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  async function streamSSE(url, body, onEvent) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        message = payload.error || message;
      } catch (_error) {
        message = (await response.text()) || message;
      }
      throw new Error(message);
    }
    if (!response.body) throw new Error("Streaming is unavailable in this browser.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const data = block
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");
        if (!data) continue;
        onEvent(JSON.parse(data));
      }
      if (done) break;
    }
    if (buffer.trim()) {
      const line = buffer.split(/\r?\n/).find((item) => item.startsWith("data:"));
      if (line) onEvent(JSON.parse(line.slice(5).trim()));
    }
  }

  function setView(view) {
    if (!["ask", "evals", "library"].includes(view)) return;
    state.currentView = view;
    $(".app-shell").classList.toggle("focus-mode", view !== "ask");
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
    $$(".view").forEach((item) => item.classList.toggle("active", item.id === `${view}-view`));
    $("#view-kicker").textContent = view === "evals" ? "EVAL STUDIO" : view.toUpperCase();
    $("#evidence-toggle").hidden = view !== "ask";
    const params = new URLSearchParams(window.location.search);
    if (view === "ask") params.delete("view");
    else params.set("view", view);
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
    if (view === "evals") loadSuites();
    if (view === "library") loadLibrary();
  }

  function setupTheme() {
    const saved = localStorage.getItem("tabletalk-theme");
    const theme = saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    $("#theme-icon").textContent = theme === "dark" ? "☀" : "◐";
  }

  function toggleTheme() {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("tabletalk-theme", theme);
    $("#theme-icon").textContent = theme === "dark" ? "☀" : "◐";
  }

  async function loadConfig() {
    try {
      const config = await api("/config");
      $("#model-name").textContent = config.model || "Unknown model";
      const runtime =
        String(config.provider).toLowerCase() === "ollama"
          ? (String(config.model).includes("cloud") ? "OLLAMA CLOUD" : "OLLAMA LOCAL")
          : String(config.provider || "unknown").toUpperCase();
      $("#provider-name").textContent = runtime;
      $("#safe-mode-label").textContent = config.safe_mode ? "READ ONLY" : "WRITE ENABLED";
      $("#model-orb").classList.remove("offline");
      $("#model-orb").classList.add("online");
      $("#model-orb").title = "Ollama configuration loaded";
    } catch (error) {
      $("#model-name").textContent = "Ollama unavailable";
      $("#provider-name").textContent = "NO FALLBACK";
      $("#model-orb").classList.remove("online");
      $("#model-orb").classList.add("offline");
      $("#model-orb").title = error.message;
    }
  }

  function manifestLabel(filename) {
    return String(filename || "")
      .replace(/\.txt$/i, "")
      .replaceAll("_", " ")
      .replaceAll("-", " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function parseField(raw) {
    const match = String(raw).match(/^([^:]+):([^\[]+)(?:\[(.+)\])?$/);
    if (!match) return { name: String(raw), type: "", annotations: [] };
    return {
      name: match[1],
      type: match[2],
      annotations: match[3] ? match[3].split(",") : [],
    };
  }

  function parseManifest(content) {
    const parsed = { source: "", context: "", datasets: [], tables: [] };
    let dataset = "";
    for (const sourceLine of String(content || "").split(/\r?\n/)) {
      const line = sourceLine.trim();
      if (line.startsWith("DATA_SOURCE:")) {
        parsed.source = line.slice("DATA_SOURCE:".length).trim();
      } else if (line.startsWith("CONTEXT:")) {
        parsed.context = line.slice("CONTEXT:".length).trim();
      } else if (line.startsWith("DATASET:")) {
        dataset = line.slice("DATASET:".length).split(" - ")[0].trim();
        parsed.datasets.push(dataset);
      } else if (line && !line.endsWith(":") && line.includes("|")) {
        const [fullName, description, ...fields] = line.split("|");
        parsed.tables.push({
          name: fullName.trim(),
          dataset,
          description: (description || "").trim(),
          fields: fields.filter(Boolean).map(parseField),
        });
      }
    }
    return parsed;
  }

  async function loadManifests() {
    const list = $("#manifest-list");
    list.innerHTML = '<div class="rail-loading">Loading agents…</div>';
    try {
      const payload = await api("/manifests");
      const manifests = payload.manifests || [];
      list.innerHTML = "";
      const select = $("#mobile-agent-select");
      select.innerHTML = '<option value="">Choose agent</option>';
      if (!manifests.length) {
        list.innerHTML = '<div class="rail-loading">No agents. Run tabletalk apply.</div>';
        return;
      }
      manifests.forEach((manifest) => {
        const metadata = payload.metadata?.[manifest] || {};
        const button = document.createElement("button");
        button.className = "agent-item";
        button.dataset.manifest = manifest;
        button.innerHTML = `
          <span class="agent-dot"></span>
          <span class="agent-name">${escapeHTML(manifestLabel(manifest))}</span>
          <span class="agent-count">${metadata.dbt_enriched ? "DBT" : "CTX"}</span>
        `;
        button.addEventListener("click", () => selectManifest(manifest));
        list.append(button);

        const option = document.createElement("option");
        option.value = manifest;
        option.textContent = manifestLabel(manifest);
        select.append(option);
      });
      if (state.currentManifest && manifests.includes(state.currentManifest)) {
        await selectManifest(state.currentManifest, false);
      }
    } catch (error) {
      list.innerHTML = `<div class="rail-loading">${escapeHTML(error.message)}</div>`;
    }
  }

  async function selectManifest(manifest, refreshSuggestions = true) {
    try {
      const payload = await api("/select_manifest", {
        method: "POST",
        body: JSON.stringify({ manifest }),
      });
      state.currentManifest = manifest;
      state.manifestSchema = parseManifest(payload.details);
      state.manifestSchema.contextSource = payload.context_source;
      $$(".agent-item").forEach((item) => {
        item.classList.toggle("active", item.dataset.manifest === manifest);
      });
      $("#mobile-agent-select").value = manifest;
      $("#active-agent-label").textContent = manifestLabel(manifest);
      renderSchema();
      setStage(
        "scope",
        "done",
        `${state.manifestSchema.tables.length} ${
          payload.context_source === "dbt_manifest" ? "dbt-enriched" : "context-scoped"
        } tables`,
      );
      updateConfidence();
      if (refreshSuggestions) await loadAISuggestions();
    } catch (error) {
      showToast(`Could not select agent: ${error.message}`);
    }
  }

  function renderSchema(filter = "") {
    const root = $("#schema-tree");
    const tables = state.manifestSchema?.tables || [];
    const term = filter.trim().toLowerCase();
    const visible = tables.filter((table) => {
      if (!term) return true;
      return (
        table.name.toLowerCase().includes(term) ||
        table.description.toLowerCase().includes(term) ||
        table.fields.some((field) => field.name.toLowerCase().includes(term))
      );
    });
    $("#schema-count").textContent = `${tables.length} TABLE${tables.length === 1 ? "" : "S"}`;
    if (!visible.length) {
      root.innerHTML = '<div class="panel-empty compact">No fields match this boundary.</div>';
      return;
    }
    root.innerHTML = "";
    visible.forEach((table, index) => {
      const item = document.createElement("div");
      item.className = `schema-table${term || index === 0 ? " open" : ""}`;
      item.innerHTML = `
        <button class="schema-table-head">
          <span class="schema-chevron">▶</span>
          <span>${escapeHTML(table.name)}</span>
          <span class="schema-field-count">${table.fields.length}</span>
        </button>
        <div class="schema-fields">
          ${table.fields.map((field) => {
            const key = field.annotations.some((value) => value.startsWith("PK"))
              ? "PK"
              : field.annotations.some((value) => value.startsWith("FK"))
                ? "FK"
                : "";
            return `
              <div class="schema-field" data-reference="${escapeHTML(`${table.name}.${field.name}`)}">
                <span class="schema-key">${key}</span>
                <span>${escapeHTML(field.name)}</span>
                <span class="schema-type">${escapeHTML(field.type)}</span>
              </div>
            `;
          }).join("")}
        </div>
      `;
      $(".schema-table-head", item).addEventListener("click", () => item.classList.toggle("open"));
      $$(".schema-field", item).forEach((field) => {
        field.addEventListener("click", () => insertAtCursor($("#question-input"), field.dataset.reference));
      });
      root.append(item);
    });
  }

  function insertAtCursor(input, text) {
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? input.value.length;
    const prefix = start > 0 && !/\s$/.test(input.value.slice(0, start)) ? " " : "";
    input.value = `${input.value.slice(0, start)}${prefix}${text}${input.value.slice(end)}`;
    input.focus();
    input.selectionStart = input.selectionEnd = start + prefix.length + text.length;
    resizeComposer();
  }

  async function loadAISuggestions() {
    const row = $("#suggestion-row");
    const welcome = $("#welcome-prompts");
    row.innerHTML = '<span class="panel-empty compact">Ollama is reading the compiled data context…</span>';
    try {
      const payload = await api("/suggest", {
        method: "POST",
        body: JSON.stringify({ manifest: state.currentManifest }),
      });
      const questions = payload.questions || [];
      renderSuggestions(row, questions);
      if (questions.length) {
        welcome.innerHTML = questions.map((question, index) => `
          <button class="prompt-card" data-question="${escapeHTML(question)}">
            <span class="prompt-index">${String(index + 1).padStart(2, "0")}</span>
            <span class="prompt-text">${escapeHTML(question)}</span>
            <span class="prompt-arrow">↗</span>
          </button>
        `).join("");
        bindPromptCards(welcome);
      }
    } catch (error) {
      row.innerHTML = `
        <span class="ai-error-chip" title="${escapeHTML(error.message)}">
          Ollama could not suggest questions · no local fallback
        </span>
      `;
      showToast(error.message, 5600);
    }
  }

  function renderSuggestions(root, questions) {
    root.innerHTML = "";
    questions.forEach((question) => {
      const button = document.createElement("button");
      button.className = "suggestion-chip";
      button.textContent = question;
      button.addEventListener("click", () => {
        $("#question-input").value = question;
        sendQuestion();
      });
      root.append(button);
    });
  }

  function scopeMeta() {
    const count = state.manifestSchema?.tables.length || 0;
    const source =
      state.manifestSchema?.contextSource === "dbt_manifest"
        ? "dbt-enriched"
        : "context-scoped";
    return `${count} ${source} tables`;
  }

  function bindPromptCards(root = document) {
    $$(".prompt-card", root).forEach((button) => {
      button.addEventListener("click", () => {
        $("#question-input").value = button.dataset.question || "";
        sendQuestion();
      });
    });
  }

  function setStage(stage, status, meta) {
    const item = $(`.pipeline-step[data-stage="${stage}"]`);
    if (!item) return;
    item.classList.remove("active", "done", "failed");
    if (status) item.classList.add(status);
    $(".stage-state", item).textContent = {
      active: "LIVE",
      done: "PROVED",
      failed: "FAILED",
      waiting: "WAITING",
    }[status] || "WAITING";
    if (meta) $(`#stage-${stage}-meta`).textContent = meta;
    updateConfidence();
  }

  function resetEvidence() {
    STAGES.forEach((stage) => {
      setStage(stage, "waiting", {
        scope: state.currentManifest ? scopeMeta() : "Choose an agent",
        compose: "Waiting for Ollama",
        execute: "No database result",
        ground: "No finding yet",
      }[stage]);
    });
    if (state.currentManifest) setStage("scope", "done", scopeMeta());
    ["generation", "execution", "rows", "tokens"].forEach((metric) => {
      $(`#metric-${metric}`).textContent = "—";
    });
    updateConfidence();
  }

  function updateConfidence() {
    const done = STAGES.filter((stage) =>
      $(`.pipeline-step[data-stage="${stage}"]`)?.classList.contains("done")
    ).length;
    const failed = STAGES.some((stage) =>
      $(`.pipeline-step[data-stage="${stage}"]`)?.classList.contains("failed")
    );
    const active = STAGES.some((stage) =>
      $(`.pipeline-step[data-stage="${stage}"]`)?.classList.contains("active")
    );
    const percent = done * 25;
    $("#confidence-fill").style.width = `${percent}%`;
    $("#confidence-percent").textContent = done ? `${percent}%` : "—";
    if (failed) {
      $("#confidence-state").textContent = "RECEIPT INCOMPLETE";
      $("#confidence-copy").textContent =
        "The AI or database returned an error. Nothing local was substituted; inspect the failed stage and retry.";
    } else if (done === 4) {
      $("#confidence-state").textContent = "FULL RECEIPT";
      $("#confidence-copy").textContent =
        "The compiled context scoped the model, SQL executed, and the finding was grounded in returned rows.";
    } else if (active) {
      $("#confidence-state").textContent = "ASSEMBLING EVIDENCE";
      $("#confidence-copy").textContent =
        "Ollama and the database are building this receipt live. No heuristic answer is inserted.";
    } else if (done) {
      $("#confidence-state").textContent = "CONTEXT READY";
      $("#confidence-copy").textContent =
        "The selected agent is bounded by its compiled context manifest.";
    } else {
      $("#confidence-state").textContent = "WAITING FOR A QUESTION";
      $("#confidence-copy").textContent =
        "A receipt will assemble here as the agent scopes, composes, executes, and grounds an answer.";
    }
  }

  function createAnswerTurn(question) {
    state.turnCounter += 1;
    const turn = document.createElement("article");
    turn.className = "conversation-turn";
    turn.innerHTML = `
      <section class="question-turn">
        <span class="turn-label">QUESTION / ${String(state.turnCounter).padStart(2, "0")}</span>
        <h2 class="question-text">${escapeHTML(question)}</h2>
      </section>
      <section class="answer-card">
        <header class="answer-head">
          <span class="answer-run-id">OLLAMA · ${escapeHTML(manifestLabel(state.currentManifest))}</span>
          <span class="answer-state">COMPOSING SQL</span>
        </header>
        <div class="finding-block">
          <div class="finding-title">
            <span>GROUNDED FINDING</span>
            <span class="section-overline">FROM RETURNED ROWS</span>
          </div>
          <p class="finding-text finding-placeholder">Waiting for execution</p>
        </div>
        <div class="result-mount"></div>
        <details class="query-evidence" open>
          <summary><span>GENERATED SQL</span><span>VIEW RECEIPT ↓</span></summary>
          <div class="sql-shell">
            <div class="sql-actions"></div>
            <pre><code class="language-sql"></code><span class="sql-cursor"></span></pre>
          </div>
        </details>
        <div class="error-mount"></div>
        <footer class="answer-receipt">
          <span class="receipt-seal">⌁</span>
          <span class="receipt-copy"><strong>AI REQUEST IN FLIGHT</strong> · no local fallback</span>
          <span class="receipt-metrics">—</span>
        </footer>
      </section>
    `;
    $("#conversation").append(turn);
    return {
      root: turn,
      state: $(".answer-state", turn),
      finding: $(".finding-text", turn),
      result: $(".result-mount", turn),
      code: $("code", turn),
      cursor: $(".sql-cursor", turn),
      sqlActions: $(".sql-actions", turn),
      error: $(".error-mount", turn),
      receipt: $(".receipt-copy", turn),
      receiptMetrics: $(".receipt-metrics", turn),
      sql: "",
      results: null,
      generationMs: null,
      executionMs: null,
    };
  }

  function setAnswerState(view, label, status = "") {
    view.state.textContent = label;
    view.state.classList.remove("done", "failed");
    if (status) view.state.classList.add(status);
  }

  function renderStreamedSQL(view, content) {
    view.sql += content;
    view.code.textContent = view.sql;
  }

  function finalizeSQL(view, sql) {
    view.sql = sql;
    view.code.textContent = sql;
    view.cursor.remove();
    if (window.Prism) window.Prism.highlightElement(view.code);
    view.sqlActions.innerHTML = `
      <button data-action="copy">COPY</button>
      <button data-action="save">SAVE</button>
    `;
    $('[data-action="copy"]', view.sqlActions).addEventListener("click", async () => {
      await navigator.clipboard.writeText(view.sql);
      showToast("SQL copied.");
    });
    $('[data-action="save"]', view.sqlActions).addEventListener("click", () => openSaveModal(view));
  }

  function renderResults(view, payload) {
    view.results = payload;
    const columns = payload.columns || [];
    const rows = payload.rows || [];
    const section = document.createElement("section");
    section.className = "result-section";
    const canChart =
      columns.length >= 2 &&
      rows.length > 0 &&
      rows.length <= 24 &&
      rows.every((row) => Number.isFinite(Number(row[1])));
    section.innerHTML = `
      <div class="result-toolbar">
        <span class="result-count"><strong>${rows.length}</strong> ROW${rows.length === 1 ? "" : "S"} RETURNED${payload.cached ? " · CACHE HIT" : ""}</span>
        <div class="result-actions">
          ${canChart ? '<button class="mini-action" data-action="chart">CHART</button>' : ""}
          <button class="mini-action" data-action="csv">CSV</button>
          <button class="mini-action" data-action="json">JSON</button>
        </div>
      </div>
      <div class="result-body">
        <div class="table-scroll">
          ${columns.length ? `
            <table class="data-table">
              <thead><tr>${columns.map((column) => `<th>${escapeHTML(column)}</th>`).join("")}</tr></thead>
              <tbody>${rows.map((row) => `
                <tr>${row.map((cell) => `<td title="${escapeHTML(cell)}">${escapeHTML(cell)}</td>`).join("")}</tr>
              `).join("")}</tbody>
            </table>
          ` : '<div class="panel-empty">The query executed and returned no rows.</div>'}
        </div>
      </div>
    `;
    view.result.replaceChildren(section);
    $('[data-action="csv"]', section).addEventListener("click", () => exportSQL(view.sql, "csv"));
    $('[data-action="json"]', section).addEventListener("click", () => exportSQL(view.sql, "json"));
    if (canChart) {
      $('[data-action="chart"]', section).addEventListener("click", (event) => {
        const body = $(".result-body", section);
        const existing = $(".chart-panel", body);
        if (existing) {
          existing.remove();
          body.classList.remove("with-chart");
          event.currentTarget.textContent = "CHART";
          return;
        }
        const panel = document.createElement("div");
        panel.className = "chart-panel";
        panel.innerHTML = "<canvas></canvas>";
        body.append(panel);
        body.classList.add("with-chart");
        event.currentTarget.textContent = "TABLE";
        if (window.Chart) {
          state.charts.push(new Chart($("canvas", panel), {
            type: "bar",
            data: {
              labels: rows.map((row) => String(row[0])),
              datasets: [{
                label: columns[1],
                data: rows.map((row) => Number(row[1])),
                backgroundColor: "#c8f25f",
                borderColor: "#5f7b18",
                borderWidth: 1,
              }],
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } },
              scales: {
                x: { ticks: { maxRotation: 45, font: { size: 8 } } },
                y: { ticks: { font: { size: 8 } } },
              },
            },
          }));
        }
      });
    }
  }

  async function exportSQL(sql, format) {
    try {
      const response = await fetch("/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql, format, filename: `tabletalk-${Date.now()}` }),
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.error || "Export failed.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `tabletalk-results.${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      showToast(error.message);
    }
  }

  function renderError(view, message, { fixable = false } = {}) {
    const error = document.createElement("div");
    error.className = "execution-error";
    error.innerHTML = `
      <span><strong>${fixable ? "DATABASE REJECTED THE SQL" : "OLLAMA REQUEST FAILED"}</strong><br>${escapeHTML(message)}</span>
      ${fixable ? '<button class="fix-button">FIX WITH OLLAMA</button>' : ""}
    `;
    view.error.replaceChildren(error);
    if (fixable) {
      $(".fix-button", error).addEventListener("click", () => fixSQL(view, message));
    }
  }

  async function fixSQL(view, errorMessage) {
    const button = $(".fix-button", view.error);
    if (button) {
      button.disabled = true;
      button.textContent = "ASKING OLLAMA…";
    }
    let fixed = "";
    try {
      await streamSSE("/fix/stream", {
        sql: view.sql,
        error: errorMessage,
        manifest: state.currentManifest,
      }, (event) => {
        if (event.type === "sql_chunk") fixed += event.content;
        if (event.type === "sql_done") fixed = event.sql;
        if (event.type === "error") throw new Error(event.error);
      });
      finalizeSQL(view, fixed);
      view.error.innerHTML = `
        <div class="execution-error fixed-sql">
          <span><strong>OLLAMA PROPOSED A FIX</strong><br>Review the revised SQL, then execute it against the database.</span>
          <button class="fix-button run-fixed">RUN FIX</button>
        </div>
      `;
      $(".run-fixed", view.error).addEventListener("click", () => executeFixedSQL(view));
      showToast("Ollama returned revised SQL. Nothing local was substituted.");
    } catch (error) {
      renderError(view, error.message);
      setStage("compose", "failed", "Ollama fix request failed");
    }
  }

  async function executeFixedSQL(view) {
    const button = $(".run-fixed", view.error);
    if (button) {
      button.disabled = true;
      button.textContent = "RUNNING…";
    }
    try {
      const payload = await api("/execute", {
        method: "POST",
        body: JSON.stringify({ sql: view.sql }),
      });
      renderResults(view, payload);
      view.error.innerHTML = "";
      setStage("execute", "done", `${payload.count} rows returned`);
      setAnswerState(view, "FIX EXECUTED", "done");
      view.receipt.innerHTML = "<strong>OLLAMA SQL EXECUTED</strong> · database receipt attached";
    } catch (error) {
      renderError(view, error.message, { fixable: true });
      setStage("execute", "failed", "Fixed query was rejected");
    }
  }

  function updateReceiptMetrics(view) {
    const parts = [];
    if (view.generationMs !== null) parts.push(`GEN ${formatMs(view.generationMs)}`);
    if (view.executionMs !== null) parts.push(`DB ${formatMs(view.executionMs)}`);
    if (view.results) parts.push(`${view.results.count || 0} ROWS`);
    view.receiptMetrics.textContent = parts.join(" · ") || "—";
  }

  async function sendQuestion() {
    if (state.isStreaming) return;
    const input = $("#question-input");
    const question = input.value.trim();
    if (!question) return;
    if (!state.currentManifest) {
      showToast("Choose a context-scoped data agent first.");
      return;
    }

    state.isStreaming = true;
    $("#send-question").disabled = true;
    input.value = "";
    resizeComposer();
    $("#welcome").hidden = true;
    $("#suggestion-row").innerHTML = "";
    resetEvidence();
    setStage("scope", "done", scopeMeta());
    setStage("compose", "active", "Ollama is generating SQL");
    const view = createAnswerTurn(question);
    view.root.scrollIntoView({ behavior: "smooth", block: "start" });

    let receivedResults = false;
    let explanationStarted = false;
    let streamFailed = false;
    try {
      await streamSSE("/chat/stream", {
        question,
        manifest: state.currentManifest,
        auto_execute: $("#toggle-run").checked,
        explain: $("#toggle-explain").checked,
        suggest: true,
      }, (event) => {
        switch (event.type) {
          case "sql_chunk":
            renderStreamedSQL(view, event.content || "");
            break;
          case "sql_done": {
            finalizeSQL(view, event.sql || view.sql);
            view.generationMs = Number(event.generation_ms);
            $("#metric-generation").textContent = formatMs(event.generation_ms);
            const tokens = Number(event.prompt_tokens || 0) + Number(event.completion_tokens || 0);
            $("#metric-tokens").textContent = tokens ? tokens.toLocaleString() : "—";
            setStage("compose", "done", `${formatMs(event.generation_ms)} via Ollama`);
            if ($("#toggle-run").checked) {
              setStage("execute", "active", "Running against the database");
              setAnswerState(view, "EXECUTING QUERY");
            } else {
              setAnswerState(view, "SQL READY", "done");
              view.finding.classList.remove("finding-placeholder");
              view.finding.textContent = "SQL was generated by Ollama but not executed.";
              view.receipt.innerHTML = "<strong>AI-GENERATED SQL</strong> · execution was disabled";
            }
            updateReceiptMetrics(view);
            break;
          }
          case "results":
            receivedResults = true;
            view.executionMs = Number(event.execution_ms);
            renderResults(view, event);
            $("#metric-execution").textContent = formatMs(event.execution_ms);
            $("#metric-rows").textContent = Number(event.count || 0).toLocaleString();
            setStage("execute", "done", `${event.count || 0} rows returned${event.cached ? " from cache" : ""}`);
            if ($("#toggle-explain").checked && Number(event.count) > 0) {
              setStage("ground", "active", "Ollama is reading returned rows");
              setAnswerState(view, "GROUNDING FINDING");
              view.finding.textContent = "";
              view.finding.classList.remove("finding-placeholder");
            } else {
              setStage("ground", "done", Number(event.count) ? "Finding disabled by user" : "Empty result verified");
              view.finding.classList.remove("finding-placeholder");
              view.finding.textContent = Number(event.count)
                ? "The query executed successfully. Grounded finding generation was disabled."
                : "The query executed successfully and returned no rows.";
            }
            view.receipt.innerHTML = "<strong>DATABASE EXECUTED</strong> · generated by Ollama, rows attached";
            updateReceiptMetrics(view);
            break;
          case "execute_error":
            streamFailed = true;
            setStage("execute", "failed", "Database rejected generated SQL");
            setStage("ground", "failed", "No result to ground");
            setAnswerState(view, "EXECUTION FAILED", "failed");
            view.finding.classList.remove("finding-placeholder");
            view.finding.textContent = "The generated SQL did not produce a database result.";
            renderError(view, event.error, { fixable: true });
            view.receipt.innerHTML = "<strong>INCOMPLETE RECEIPT</strong> · database error preserved";
            break;
          case "explain_chunk":
            explanationStarted = true;
            view.finding.textContent += event.content || "";
            break;
          case "explain_done":
            setStage("ground", "done", "Finding grounded by Ollama");
            setAnswerState(view, "RECEIPT COMPLETE", "done");
            break;
          case "explain_error":
            streamFailed = true;
            setStage("ground", "failed", "Ollama explanation failed");
            setAnswerState(view, "GROUNDING FAILED", "failed");
            if (!explanationStarted) {
              view.finding.textContent = "The database result is available, but Ollama did not complete the finding.";
            }
            renderError(view, event.error);
            break;
          case "suggestions":
            renderSuggestions($("#suggestion-row"), event.questions || []);
            break;
          case "suggestion_error":
            showToast(`Ollama suggestions failed: ${event.error}`, 5200);
            break;
          case "error":
            streamFailed = true;
            setStage("compose", "failed", "Ollama SQL request failed");
            setStage("execute", "failed", "Not attempted");
            setStage("ground", "failed", "Not attempted");
            setAnswerState(view, "OLLAMA ERROR", "failed");
            view.finding.classList.remove("finding-placeholder");
            view.finding.textContent =
              "No answer was generated. TableTalk did not replace the failed Ollama request with local logic.";
            renderError(view, event.error);
            view.receipt.innerHTML = "<strong>NO FALLBACK USED</strong> · Ollama error preserved";
            break;
          case "done":
            if (!streamFailed) {
              if (!receivedResults && $("#toggle-run").checked) {
                setAnswerState(view, "SQL READY", "done");
              } else if (!$("#toggle-explain").checked || !view.results?.count) {
                setAnswerState(view, "RECEIPT COMPLETE", "done");
              }
            }
            break;
          default:
            break;
        }
      });
    } catch (error) {
      setStage("compose", "failed", "Ollama request failed");
      setStage("execute", "failed", "Not attempted");
      setStage("ground", "failed", "Not attempted");
      setAnswerState(view, "REQUEST FAILED", "failed");
      view.finding.classList.remove("finding-placeholder");
      view.finding.textContent =
        "No answer was generated. TableTalk did not substitute a local parser or canned response.";
      renderError(view, error.message);
      view.receipt.innerHTML = "<strong>NO FALLBACK USED</strong> · request error preserved";
    } finally {
      updateReceiptMetrics(view);
      state.isStreaming = false;
      $("#send-question").disabled = false;
      updateConfidence();
    }
  }

  function openSaveModal(view) {
    state.pendingSave = {
      question: $(".question-text", view.root)?.textContent || "",
      sql: view.sql,
    };
    $("#save-name").value = "";
    $("#save-overlay").hidden = false;
    window.setTimeout(() => $("#save-name").focus(), 0);
  }

  function closeSaveModal() {
    $("#save-overlay").hidden = true;
    state.pendingSave = null;
  }

  async function confirmSave() {
    const name = $("#save-name").value.trim();
    if (!name || !state.pendingSave) {
      showToast("Give the saved question a name.");
      return;
    }
    try {
      await api("/favorites", {
        method: "POST",
        body: JSON.stringify({
          name,
          manifest: state.currentManifest,
          question: state.pendingSave.question,
          sql: state.pendingSave.sql,
        }),
      });
      closeSaveModal();
      showToast("Saved to the analysis library.");
    } catch (error) {
      showToast(error.message);
    }
  }

  async function resetConversation() {
    if (state.isStreaming) return;
    try {
      await api("/reset", { method: "POST", body: "{}" });
      $$(".conversation-turn", $("#conversation")).forEach((turn) => turn.remove());
      $("#welcome").hidden = false;
      state.turnCounter = 0;
      resetEvidence();
      if (state.currentManifest) await loadAISuggestions();
      showToast("Started a clean thread.");
    } catch (error) {
      showToast(error.message);
    }
  }

  async function loadSuites() {
    const list = $("#suite-list");
    list.innerHTML = '<div class="panel-empty">Discovering versioned eval suites…</div>';
    try {
      const payload = await api("/api/evals");
      const suites = payload.suites || [];
      list.innerHTML = "";
      if (!suites.length) {
        list.innerHTML =
          '<div class="panel-empty">No suites found. Add a YAML suite inside <code>evals/</code>.</div>';
        return;
      }
      suites.forEach((suite) => {
        const button = document.createElement("button");
        button.className = `suite-card${suite.status === "invalid" ? " invalid" : ""}`;
        button.dataset.path = suite.path;
        button.innerHTML = `
          <div class="suite-topline">
            <span class="suite-name">${escapeHTML(suite.name)}</span>
            <span class="suite-cases">${suite.case_count} CASE${suite.case_count === 1 ? "" : "S"}</span>
          </div>
          <div class="suite-description">${escapeHTML(suite.description || "Versioned AI behavior contract.")}</div>
          <div class="tag-row">${(suite.tags || []).map((tag) => `<span class="tag">${escapeHTML(tag)}</span>`).join("")}</div>
        `;
        if (suite.status !== "invalid") {
          button.addEventListener("click", () => selectSuite(suite, button));
        }
        list.append(button);
      });
      if (state.selectedSuite) {
        const match = $(`.suite-card[data-path="${CSS.escape(state.selectedSuite.path)}"]`, list);
        if (match) match.click();
      }
    } catch (error) {
      list.innerHTML = `<div class="panel-empty">${escapeHTML(error.message)}</div>`;
    }
  }

  function selectSuite(suite, button) {
    state.selectedSuite = suite;
    $$(".suite-card").forEach((item) => item.classList.toggle("selected", item === button));
    $("#selected-suite-label").textContent = `${suite.name} · ${suite.case_count} cases`;
    $("#run-suite-btn").disabled = false;
    $("#eval-run-header").querySelector("h2").textContent = "Ready to test the active Ollama model.";
    $("#case-board").innerHTML = `
      <div class="eval-empty-state">
        <div class="empty-glyph">${suite.case_count}</div>
        <h3>${escapeHTML(suite.name)}</h3>
        <p>Each case asks Ollama, executes its SQL, then verifies the resulting trace.</p>
      </div>
    `;
  }

  function renderCaseStarted(event) {
    const board = $("#case-board");
    if (event.index === 1) board.innerHTML = "";
    const row = document.createElement("article");
    row.id = `eval-case-${slug(event.case)}-${event.index}`;
    row.className = "case-row running";
    row.innerHTML = `
      <span class="case-status">↻</span>
      <div class="case-copy">
        <strong>${escapeHTML(event.case)}</strong>
        <span>${(event.tags || []).map(escapeHTML).join(" · ") || "AI DATABASE CONTRACT"}</span>
      </div>
      <span class="case-score">—</span>
    `;
    board.append(row);
    $("#progress-label").textContent = `Ollama is running ${event.case}`;
    $("#progress-count").textContent = `${event.index - 1} / ${event.total}`;
    $("#progress-bar").style.width = `${((event.index - 1) / event.total) * 100}%`;
  }

  function renderCaseComplete(event) {
    const result = event.case;
    const row = $(`#eval-case-${slug(result.case_name)}-${event.index}`);
    if (!row) return;
    row.classList.remove("running");
    row.classList.add(result.passed ? "passed" : "failed");
    $(".case-status", row).textContent = result.passed ? "✓" : "×";
    $(".case-score", row).textContent = `${Math.round(Number(result.score) * 100)}%`;
    const copy = $(".case-copy", row);
    const metrics = document.createElement("div");
    metrics.className = "case-metrics";
    metrics.innerHTML = (result.metrics || []).map((metric) => `
      <span class="metric-pill ${metric.passed ? "pass" : "fail"}" title="${escapeHTML(JSON.stringify(metric.details || {}))}">
        ${escapeHTML(metric.name.replaceAll("_", " "))} ${metric.passed ? "✓" : "×"}
      </span>
    `).join("");
    copy.append(metrics);
    $("#progress-count").textContent = `${event.index} / ${event.total}`;
    $("#progress-bar").style.width = `${(event.index / event.total) * 100}%`;
  }

  async function runSelectedSuite() {
    if (!state.selectedSuite || state.evalRunning) return;
    state.evalRunning = true;
    $("#run-suite-btn").disabled = true;
    $("#run-suite-btn").querySelector("span").textContent = "Running with Ollama…";
    $("#eval-progress").hidden = false;
    $("#progress-bar").style.width = "0%";
    $("#score-orbit").style.setProperty("--score", 0);
    $("#score-orbit strong").textContent = "—";
    try {
      await streamSSE("/api/evals/run/stream", { suite: state.selectedSuite.path }, (event) => {
        if (event.type === "suite_start") {
          $("#eval-run-header").querySelector("h2").textContent = `Testing ${event.suite}`;
          $("#progress-count").textContent = `0 / ${event.case_count}`;
        } else if (event.type === "case_start") {
          renderCaseStarted(event);
        } else if (event.type === "case_complete") {
          renderCaseComplete(event);
        } else if (event.type === "suite_complete") {
          const result = event.result;
          const score = Math.round(Number(result.score) * 100);
          $("#score-orbit").style.setProperty("--score", score);
          $("#score-orbit strong").textContent = `${score}`;
          $("#eval-run-header").querySelector("h2").textContent = result.passed
            ? `${result.passed_count} contracts passed. Ready to ship.`
            : `${result.failed_count} contract${result.failed_count === 1 ? "" : "s"} need attention.`;
          $("#progress-label").textContent = result.passed
            ? `Run complete · ${result.passed_count} passed`
            : `Run complete · ${result.failed_count} failed, ${result.passed_count} passed`;
          $("#progress-count").textContent = `${result.cases.length} cases`;
        } else if (event.type === "error") {
          throw new Error(event.error);
        }
      });
    } catch (error) {
      $("#eval-run-header").querySelector("h2").textContent =
        "Ollama did not complete this run. No local model was substituted.";
      $("#case-board").insertAdjacentHTML(
        "afterbegin",
        `<div class="execution-error"><span><strong>EVAL RUN STOPPED</strong><br>${escapeHTML(error.message)}</span></div>`,
      );
      showToast(error.message, 6000);
    } finally {
      state.evalRunning = false;
      $("#run-suite-btn").disabled = false;
      $("#run-suite-btn").querySelector("span").textContent = "Run selected suite";
    }
  }

  async function loadLibrary() {
    await Promise.all([loadFavorites(), loadHistory()]);
  }

  function renderLibraryItem(item, type) {
    const article = document.createElement("article");
    article.className = "library-item";
    article.innerHTML = `
      ${type === "favorite" ? '<button class="delete-library-item" title="Delete saved question">×</button>' : ""}
      <h3>${escapeHTML(type === "favorite" ? item.name : manifestLabel(item.manifest))}</h3>
      <p class="library-question">${escapeHTML(item.question || "Untitled analysis")}</p>
      <div class="library-meta">
        <span>${escapeHTML(manifestLabel(item.manifest))}</span>
        <span>${escapeHTML(formatDate(item.created_at || item.timestamp))}</span>
        ${item.metrics?.row_count !== undefined ? `<span>${item.metrics.row_count} rows</span>` : ""}
      </div>
    `;
    article.addEventListener("click", async (event) => {
      if (event.target.closest(".delete-library-item")) return;
      if (item.manifest && item.manifest !== state.currentManifest) await selectManifest(item.manifest, false);
      setView("ask");
      $("#question-input").value = item.question || "";
      $("#question-input").focus();
      resizeComposer();
    });
    if (type === "favorite") {
      $(".delete-library-item", article).addEventListener("click", async () => {
        try {
          await api(`/favorites/${encodeURIComponent(item.name)}`, { method: "DELETE" });
          article.remove();
          showToast("Removed saved question.");
        } catch (error) {
          showToast(error.message);
        }
      });
    }
    return article;
  }

  async function loadFavorites() {
    const root = $("#favorites-list");
    root.innerHTML = '<div class="panel-empty">Loading saved questions…</div>';
    try {
      const payload = await api("/favorites");
      root.innerHTML = "";
      if (!(payload.favorites || []).length) {
        root.innerHTML = '<div class="panel-empty">Save generated SQL to build a reusable library.</div>';
        return;
      }
      payload.favorites.forEach((item) => root.append(renderLibraryItem(item, "favorite")));
    } catch (error) {
      root.innerHTML = `<div class="panel-empty">${escapeHTML(error.message)}</div>`;
    }
  }

  async function loadHistory() {
    const root = $("#history-list");
    root.innerHTML = '<div class="panel-empty">Loading recent runs…</div>';
    try {
      const payload = await api("/history?limit=30");
      root.innerHTML = "";
      if (!(payload.history || []).length) {
        root.innerHTML = '<div class="panel-empty">Completed questions appear here with their evidence.</div>';
        return;
      }
      payload.history.forEach((item) => root.append(renderLibraryItem(item, "history")));
    } catch (error) {
      root.innerHTML = `<div class="panel-empty">${escapeHTML(error.message)}</div>`;
    }
  }

  function resizeComposer() {
    const input = $("#question-input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
  }

  function bindEvents() {
    $$(".nav-item").forEach((button) =>
      button.addEventListener("click", () => setView(button.dataset.view))
    );
    $("#theme-toggle").addEventListener("click", toggleTheme);
    $("#refresh-manifests").addEventListener("click", loadManifests);
    $("#mobile-agent-select").addEventListener("change", (event) => {
      if (event.target.value) selectManifest(event.target.value);
    });
    $("#new-thread-btn").addEventListener("click", resetConversation);
    $("#send-question").addEventListener("click", sendQuestion);
    $("#question-input").addEventListener("input", resizeComposer);
    $("#question-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendQuestion();
      }
    });
    $("#schema-search").addEventListener("input", (event) => renderSchema(event.target.value));
    $("#refresh-suites").addEventListener("click", loadSuites);
    $("#run-suite-btn").addEventListener("click", runSelectedSuite);
    $("#refresh-favorites").addEventListener("click", loadFavorites);
    $("#refresh-history").addEventListener("click", loadHistory);
    $("#save-cancel").addEventListener("click", closeSaveModal);
    $("#save-confirm").addEventListener("click", confirmSave);
    $("#save-overlay").addEventListener("click", (event) => {
      if (event.target === $("#save-overlay")) closeSaveModal();
    });
    $("#save-name").addEventListener("keydown", (event) => {
      if (event.key === "Enter") confirmSave();
    });
    $("#evidence-toggle").addEventListener("click", () => $("#evidence-rail").classList.add("open"));
    $("#close-evidence").addEventListener("click", () => $("#evidence-rail").classList.remove("open"));
    bindPromptCards();
    document.addEventListener("keydown", (event) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
      if (event.key === "Escape") {
        closeSaveModal();
        $("#evidence-rail").classList.remove("open");
      }
      if (!typing && !event.metaKey && !event.ctrlKey && ["1", "2", "3"].includes(event.key)) {
        setView({ 1: "ask", 2: "evals", 3: "library" }[event.key]);
      }
    });
  }

  async function init() {
    setupTheme();
    bindEvents();
    resetEvidence();
    const initialView = new URLSearchParams(window.location.search).get("view") || "ask";
    setView(initialView);
    await Promise.all([loadConfig(), loadManifests()]);
  }

  init();
})();

(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const state = { agents: [], selected: null, busy: false };

  async function request(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json();
    if (!response.ok) throw payload.failure || {
      code: "request_failed",
      message: "The request failed.",
      stage: "presentation",
    };
    return payload;
  }

  function text(element, value) {
    element.textContent = value == null ? "" : String(value);
  }

  function addDefinition(list, label, value) {
    if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    text(term, label);
    text(detail, Array.isArray(value) ? value.join(", ") : value);
    list.append(term, detail);
  }

  function selectAgent(agent) {
    state.selected = agent;
    document.querySelectorAll(".agent").forEach((button) => {
      button.classList.toggle("active", button.dataset.name === agent.name);
    });
    text($("#agent-title"), agent.agent_name || agent.name);
    text($("#agent-description"), agent.description || "Governed applied data agent");
    text($("#artifact-chip"), `artifact ${String(agent.artifact_digest).slice(0, 12)}`);
    $("#artifact-chip").hidden = false;
    $("#ask-button").disabled = false;
    $("#question").focus();
  }

  async function loadAgents() {
    const container = $("#agents");
    try {
      const payload = await request("/api/agents");
      state.agents = payload.agents || [];
      container.replaceChildren();
      if (!state.agents.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        text(empty, "No applied agents. Run tabletalk apply.");
        container.append(empty);
        return;
      }
      for (const agent of state.agents) {
        const button = document.createElement("button");
        button.className = "agent";
        button.dataset.name = agent.name;
        const name = document.createElement("strong");
        const meta = document.createElement("span");
        text(name, agent.agent_name || agent.name);
        text(meta, `${agent.relation_count} relations · evaluated`);
        button.append(name, meta);
        button.addEventListener("click", () => selectAgent(agent));
        container.append(button);
      }
      selectAgent(state.agents[0]);
    } catch (failure) {
      text(container, failure.message || "Applied agents could not be loaded.");
    }
  }

  async function loadRuntime() {
    try {
      const config = await request("/api/config");
      text($("#runtime-model"), config.model);
      text($("#runtime-provider"), `${config.provider} · fallback ${config.fallback}`);
      $("#runtime-dot").classList.add("online");
    } catch (failure) {
      text($("#runtime-model"), "Unavailable");
      text($("#runtime-provider"), "No fallback");
      $("#runtime-dot").classList.add("offline");
    }
  }

  function renderFailure(failure) {
    $("#answer").hidden = true;
    $("#failure").hidden = false;
    text($("#failure-title"), "The trusted answer could not be completed.");
    text($("#failure-message"), failure.message || "The request failed explicitly.");
    text($("#failure-code"), `${failure.code || "failure"} · ${failure.stage || "unknown stage"}`);
  }

  function renderInterpretation(answer) {
    const value = answer.interpretation || {};
    const list = $("#interpretation");
    list.replaceChildren();
    addDefinition(list, "Intent", value.intent);
    addDefinition(list, "Metrics", value.metrics);
    addDefinition(list, "Dimensions", value.dimensions);
    addDefinition(list, "Filters", value.filters);
    addDefinition(
      list,
      "Exact range",
      value.start_date || value.end_date
        ? `${value.start_date || "unspecified"} → ${value.end_date || "unspecified"}`
        : null,
    );
    addDefinition(list, "Timezone", value.timezone);
    addDefinition(list, "Assumptions", value.assumptions);
  }

  function renderVerification(answer) {
    const list = $("#verification");
    list.replaceChildren();
    for (const check of answer.verification || []) {
      const item = document.createElement("li");
      item.className = check.passed ? "passed" : "not-passed";
      text(
        item,
        `${check.passed ? "✓" : "—"} ${check.name.replaceAll("_", " ")}`
          + (check.warning ? ` — ${check.warning}` : ""),
      );
      list.append(item);
    }
  }

  function renderClaims(answer) {
    const container = $("#claims");
    container.replaceChildren();
    text($("#evidence-count"), `${answer.evidence?.length || 0} evidence rows`);
    if (!answer.claims?.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      text(empty, "No material claims were verified.");
      container.append(empty);
      return;
    }
    for (const claim of answer.claims) {
      const card = document.createElement("div");
      card.className = `claim ${claim.supported ? "supported" : "unsupported"}`;
      const copy = document.createElement("strong");
      const refs = document.createElement("span");
      text(copy, claim.claim);
      text(refs, claim.supported
        ? `Evidence: ${claim.evidence_ids.join(", ")}`
        : claim.reason || "Unsupported claim withheld from the direct answer");
      card.append(copy, refs);
      container.append(card);
    }
  }

  function renderData(rows) {
    const table = $("#data-table");
    table.replaceChildren();
    text($("#row-count"), `${rows.length} rows`);
    if (!rows.length) return;
    const columns = Object.keys(rows[0]);
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const column of columns) {
      const cell = document.createElement("th");
      text(cell, column);
      headRow.append(cell);
    }
    head.append(headRow);
    const body = document.createElement("tbody");
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const column of columns) {
        const cell = document.createElement("td");
        text(cell, row[column]);
        tr.append(cell);
      }
      body.append(tr);
    }
    table.append(head, body);
  }

  function renderReceipt(answer) {
    const receipt = answer.receipt || {};
    const list = $("#receipt");
    list.replaceChildren();
    addDefinition(list, "Artifact digest", receipt.artifact_digest);
    addDefinition(list, "Eval receipt", receipt.eval_receipt_digest);
    addDefinition(list, "Model", receipt.runtime?.model);
    addDefinition(list, "Endpoint type", receipt.runtime?.provider);
    addDefinition(list, "Database", `${receipt.database_type || ""} · ${receipt.database_identity || ""}`);
    const repairs = $("#repairs");
    repairs.replaceChildren();
    for (const attempt of answer.repairs || []) {
      const block = document.createElement("div");
      block.className = "repair";
      const heading = document.createElement("strong");
      const detail = document.createElement("code");
      text(heading, `Repair ${attempt.attempt}: ${attempt.error_code}`);
      text(detail, `${attempt.failed_sql}\n→ ${attempt.repaired_sql}\n${attempt.error_message}`);
      block.append(heading, detail);
      repairs.append(block);
    }
  }

  function renderAnswer(answer) {
    $("#failure").hidden = true;
    $("#answer").hidden = false;
    text($("#status"), String(answer.status || "unknown").replaceAll("_", " "));
    $("#status").className = `status ${answer.status || ""}`;
    text($("#direct-answer"), answer.direct_answer || "");
    $("#direct-answer").hidden = !answer.direct_answer;
    $("#no-answer").hidden = Boolean(answer.direct_answer);
    $("#repair-warning").hidden = !(answer.repairs?.length);
    renderInterpretation(answer);
    renderVerification(answer);
    renderClaims(answer);
    renderData(answer.data || []);
    text($("#sql"), answer.sql || "No SQL executed.");
    const sources = $("#sources");
    sources.replaceChildren();
    for (const source of answer.sources || []) {
      const chip = document.createElement("span");
      text(chip, `${source.relation}${source.columns?.length ? ` · ${source.columns.join(", ")}` : ""}`);
      sources.append(chip);
    }
    renderReceipt(answer);
  }

  async function ask() {
    if (state.busy || !state.selected) return;
    const question = $("#question").value.trim();
    if (!question) return;
    state.busy = true;
    $("#ask-button").disabled = true;
    $("#progress").hidden = false;
    $("#failure").hidden = true;
    $("#answer").hidden = true;
    try {
      const payload = await request("/api/ask", {
        method: "POST",
        body: JSON.stringify({ agent: state.selected.name, question }),
      });
      renderAnswer(payload.answer);
    } catch (failure) {
      renderFailure(failure);
    } finally {
      state.busy = false;
      $("#ask-button").disabled = false;
      $("#progress").hidden = true;
    }
  }

  $("#ask-button").addEventListener("click", ask);
  $("#question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask();
    }
  });
  loadAgents();
  loadRuntime();
})();

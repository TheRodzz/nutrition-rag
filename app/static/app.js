let activeRestaurantFilter = "";
let providersState = null;
let catalogCurrentPage = 1;
const catalogPageSize = 50;
let catalogDebounceTimer = null;

function esc(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function confidenceBadgeHtml(score, level) {
  const s = Number(score || 0).toFixed(1);
  if (s >= 85) {
    return `<span class="px-2.5 py-0.5 text-xs font-mono font-bold rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">${s}% (${esc(level || 'High')})</span>`;
  } else if (s >= 65) {
    return `<span class="px-2.5 py-0.5 text-xs font-mono font-bold rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40">${s}% (${esc(level || 'Medium')})</span>`;
  }
  return `<span class="px-2.5 py-0.5 text-xs font-mono font-bold rounded-full bg-rose-500/20 text-rose-300 border border-rose-500/40">${s}% (${esc(level || 'Low')})</span>`;
}

function modalityBadgeHtml(modality) {
  const m = String(modality || "").toLowerCase();
  if (m === "tabular") {
    return `<span class="px-2 py-0.5 text-[11px] font-mono font-semibold rounded bg-sky-500/15 text-sky-300 border border-sky-500/30">TABULAR</span>`;
  } else if (m === "hybrid") {
    return `<span class="px-2 py-0.5 text-[11px] font-mono font-semibold rounded bg-purple-500/15 text-purple-300 border border-purple-500/30">HYBRID (TAB+PROSE)</span>`;
  }
  return `<span class="px-2 py-0.5 text-[11px] font-mono font-semibold rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">NON-TABULAR PROSE</span>`;
}

function populateCatalogRestaurantSelect(restaurants, totalItems) {
  const sel = document.getElementById("catalogRestaurantSelect");
  if (!sel) return;
  const currentVal = sel.value;
  sel.innerHTML = `<option value="">All Restaurants (${Number(totalItems || 0).toLocaleString()} items)</option>` +
    (restaurants || []).map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join("");
  if (currentVal && (restaurants || []).includes(currentVal)) {
    sel.value = currentVal;
  }
}

function populateRestaurantFilterPills(restaurants) {
  const container = document.getElementById("restaurantFilterPills");
  if (!container) return;

  function shortName(r) {
    return r
      .replace(" Bangalore", "")
      .replace(" India", "")
      .replace(" Ice Creams", "");
  }

  const allPillsHtml = [
    `<span class="text-slate-400 mr-1 shrink-0">Filter:</span>`,
    `<button data-rest="" class="rest-pill ${activeRestaurantFilter === "" ? "active px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-medium shrink-0" : "px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 shrink-0"}">All</button>`,
    ...(restaurants || []).map((r) => {
      const isActive = activeRestaurantFilter === r;
      const cls = isActive
        ? "rest-pill active px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-medium shrink-0"
        : "rest-pill px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 shrink-0";
      return `<button data-rest="${esc(r)}" class="${cls}" title="${esc(r)}">${esc(shortName(r))}</button>`;
    }),
  ].join("");

  container.innerHTML = allPillsHtml;

  container.querySelectorAll(".rest-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      container.querySelectorAll(".rest-pill").forEach((p) => {
        p.className =
          "rest-pill px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 shrink-0";
      });
      pill.className =
        "rest-pill active px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-medium shrink-0";
      activeRestaurantFilter = pill.dataset.rest || "";
    });
  });
}

async function fetchStatsAndDocuments() {
  try {
    const [statsRes, docsRes] = await Promise.all([
      fetch("/api/stats"),
      fetch("/api/documents"),
    ]);
    const stats = await statsRes.json();
    const docsData = await docsRes.json();

    document.getElementById("indexVersionBadge").textContent = `Vector DB v${stats.index_version}`;
    document.getElementById("statDocs").textContent = stats.total_documents;
    document.getElementById("statChunks").textContent = stats.total_chunks;
    document.getElementById("statItems").textContent = stats.total_structured_items;
    document.getElementById("docTabCount").textContent = stats.total_documents;

    document.getElementById("dbInfoVersion").textContent = `v${stats.index_version}`;
    document.getElementById("dbInfoTimestamp").textContent = stats.last_rebuilt_at
      ? new Date(stats.last_rebuilt_at).toLocaleTimeString()
      : "Never";
    document.getElementById("dbInfoDuration").textContent = `${stats.last_rebuild_duration_ms} ms`;
    document.getElementById("dbInfoModalitySplit").textContent = `${stats.tabular_chunks} tabular / ${stats.non_tabular_chunks} prose`;

    populateCatalogRestaurantSelect(stats.restaurants || [], stats.total_structured_items);
    populateRestaurantFilterPills(stats.restaurants || []);

    renderDocumentsTable(docsData.documents || []);
    renderRebuildHistory(stats.rebuild_history || []);
  } catch (err) {
    console.error("Failed to fetch stats:", err);
  }
}

async function fetchSettings() {
  try {
    const res = await fetch("/api/settings");
    providersState = await res.json();
    const provSel = document.getElementById("providerSelect");
    provSel.value = providersState.active_provider || "agy";
    populateModelDropdown();
  } catch (err) {
    console.error("Failed to load LLM settings:", err);
  }
}

function populateModelDropdown() {
  if (!providersState) return;
  const prov = document.getElementById("providerSelect").value;
  const modelSel = document.getElementById("modelSelect");
  const list = prov === "agy" ? (providersState.agy?.models || []) : (providersState.ollama?.models || []);
  const activeModel = prov === "agy" ? providersState.active_agy_model : providersState.active_ollama_model;

  modelSel.innerHTML = list
    .map((m) => `<option value="${esc(m.id)}" ${m.id === activeModel ? "selected" : ""}>${esc(m.label || m.id)}</option>`)
    .join("");
}

async function saveProviderSetting() {
  const prov = document.getElementById("providerSelect").value;
  const mod = document.getElementById("modelSelect").value;
  const payload = { llm_provider: prov };
  if (prov === "agy") payload.agy_model = mod;
  else payload.ollama_model = mod;

  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  providersState = await res.json();
}

function renderDocumentsTable(docs) {
  const tbody = document.getElementById("documentsTableBody");
  if (!docs.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="py-6 text-center text-slate-500">No documents indexed yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = docs
    .map(
      (d) => `
    <tr class="hover:bg-slate-800/40 transition-colors">
      <td class="py-3 px-4 font-mono text-xs text-white font-medium">${esc(d.filename)}</td>
      <td class="py-3 px-3 text-xs text-slate-300">${esc(d.restaurant_detected)}</td>
      <td class="py-3 px-3">${modalityBadgeHtml(d.detected_modality)}</td>
      <td class="py-3 px-3 font-mono text-xs text-emerald-400">${Math.round((d.tabular_confidence || 0) * 100)}%</td>
      <td class="py-3 px-3 font-mono text-xs text-slate-300">${d.tabular_sections_count} tab / ${d.prose_sections_count} prose</td>
      <td class="py-3 px-3 font-mono text-xs text-sky-400 font-semibold">${d.extracted_items_count}</td>
      <td class="py-3 px-3 font-mono text-xs text-emerald-400 font-semibold">${d.created_chunks_count}</td>
      <td class="py-3 px-4 text-xs text-slate-400 max-w-xs truncate" title="${esc(d.detection_reasoning)}">${esc(d.detection_reasoning)}</td>
      <td class="py-3 px-3 text-right">
        <button onclick="deleteDocument('${esc(d.filename)}')" class="px-2.5 py-1 rounded bg-rose-500/15 hover:bg-rose-500/30 text-rose-300 border border-rose-500/30 text-xs font-medium transition-colors">
          Delete
        </button>
      </td>
    </tr>
  `
    )
    .join("");
}

function renderRebuildHistory(history) {
  const container = document.getElementById("rebuildHistoryList");
  if (!history.length) {
    container.innerHTML = `<div class="text-slate-500">No rebuild events recorded in this session yet.</div>`;
    return;
  }
  container.innerHTML = history
    .map(
      (h) => `
    <div class="flex flex-wrap items-center justify-between gap-2 bg-slate-950/80 border border-slate-800/80 rounded-lg px-3.5 py-2">
      <div class="flex items-center gap-2.5">
        <span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold">v${h.index_version}</span>
        <span class="text-slate-200">${esc(h.trigger_reason)}</span>
      </div>
      <div class="flex items-center gap-4 text-slate-400">
        <span>${h.documents_count} docs / ${h.chunks_count} chunks / ${h.structured_items_count} items</span>
        <span class="text-emerald-400">${h.total_duration_ms} ms</span>
        <span>${new Date(h.timestamp).toLocaleTimeString()}</span>
      </div>
    </div>
  `
    )
    .join("");
}

async function runOrderQuery() {
  const q = document.getElementById("orderQueryInput").value.trim();
  if (!q) return;

  const btn = document.getElementById("askMacrosBtn");
  const loading = document.getElementById("queryLoadingState");
  const resultBox = document.getElementById("queryResultContainer");
  const prov = document.getElementById("providerSelect").value;
  const mod = document.getElementById("modelSelect").value;

  btn.disabled = true;
  document.getElementById("askBtnLabel").textContent = "Calculating...";
  loading.classList.remove("hidden");
  resultBox.classList.add("hidden");

  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: q,
        restaurant_filter: activeRestaurantFilter || null,
        llm_provider: prov,
        llm_model: mod,
        top_k: 12,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Query failed");
    }
    const data = await res.json();
    renderQueryResult(data);
  } catch (err) {
    alert("Error calculating macros: " + err.message);
  } finally {
    btn.disabled = false;
    document.getElementById("askBtnLabel").textContent = "Calculate Macros";
    loading.classList.add("hidden");
  }
}

function renderQueryResult(data) {
  const resultBox = document.getElementById("queryResultContainer");
  resultBox.classList.remove("hidden");

  const tm = data.total_macros || {};
  document.getElementById("totCalories").textContent = `${Number(tm.calories_kcal || 0).toFixed(1)}`;
  document.getElementById("totProtein").textContent = `${Number(tm.protein_g || 0).toFixed(1)}g`;
  document.getElementById("totCarbs").textContent = `${Number(tm.carbs_g || 0).toFixed(1)}g`;
  document.getElementById("totFat").textContent = `${Number(tm.fat_g || 0).toFixed(1)}g`;

  if (tm.fiber_g !== null && tm.fiber_g !== undefined) {
    document.getElementById("totFiber").textContent = `${Number(tm.fiber_g).toFixed(1)}g`;
    document.getElementById("totFiberSub").textContent = "dietary fiber";
  } else {
    document.getElementById("totFiber").textContent = "N/A";
    document.getElementById("totFiberSub").textContent = "not in source table";
  }

  document.getElementById("resultVectorVersion").textContent = `Vector DB v${data.index_version} • ${data.latency_ms}ms`;
  document.getElementById("llmMetaBadge").textContent = `${data.llm_provider_used}: ${data.llm_model_used}`;

  // Calorie split bar
  const pCal = (tm.protein_g || 0) * 4;
  const cCal = (tm.carbs_g || 0) * 4;
  const fCal = (tm.fat_g || 0) * 9;
  const sumCal = Math.max(pCal + cCal + fCal, 1);
  const pPct = Math.round((pCal / sumCal) * 100);
  const cPct = Math.round((cCal / sumCal) * 100);
  const fPct = Math.max(0, 100 - pPct - cPct);

  document.getElementById("barProtein").style.width = `${pPct}%`;
  document.getElementById("barCarbs").style.width = `${cPct}%`;
  document.getElementById("barFat").style.width = `${fPct}%`;
  document.getElementById("macroSplitText").textContent = `Calorie Split: ${pPct}% Protein • ${cPct}% Carbs • ${fPct}% Fat`;

  // Confidence card
  const conf = Number(data.overall_confidence_score || 0).toFixed(1);
  document.getElementById("overallConfidenceScore").textContent = `${conf}%`;
  const bar = document.getElementById("overallConfidenceBar");
  bar.style.width = `${Math.min(100, conf)}%`;
  bar.className = `h-full transition-all ${conf >= 85 ? "bg-emerald-500" : conf >= 65 ? "bg-amber-500" : "bg-rose-500"}`;

  const badge = document.getElementById("overallConfidenceBadge");
  badge.textContent = `${data.overall_confidence_level} Confidence`;
  badge.className = `px-2.5 py-0.5 text-xs font-bold rounded-full ${
    conf >= 85
      ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
      : conf >= 65
      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
      : "bg-rose-500/20 text-rose-300 border border-rose-500/40"
  }`;

  document.getElementById("confidenceExplanation").textContent = data.confidence_explanation || "";

  // Collect all assumptions across overall and per-item
  const allAssumptions = [...(data.assumptions_and_notes || [])];
  (data.items || []).forEach((it) => {
    (it.assumptions_made || []).forEach((a) => {
      if (!allAssumptions.includes(a)) allAssumptions.push(`${it.matched_item_name}: ${a}`);
    });
  });

  const assumpBox = document.getElementById("assumptionsBox");
  const assumpList = document.getElementById("assumptionsList");
  if (allAssumptions.length > 0) {
    assumpBox.classList.remove("hidden");
    assumpList.innerHTML = allAssumptions.map((a) => `<li>${esc(a)}</li>`).join("");
  } else {
    assumpBox.classList.add("hidden");
  }

  document.getElementById("answerSummaryText").textContent = data.answer_summary || "";

  // Per-item table
  const tbody = document.getElementById("resolvedItemsTableBody");
  tbody.innerHTML = (data.items || [])
    .map((it) => {
      const m = it.total_macros || {};
      const fibCell = m.fiber_g !== null && m.fiber_g !== undefined ? `${Number(m.fiber_g).toFixed(1)}g` : "—";
      const reasonsHtml = (it.confidence_reasons || []).map((r) => `<div class="text-[11px] text-slate-400">• ${esc(r)}</div>`).join("");
      return `
      <tr class="hover:bg-slate-800/35 transition-colors align-top">
        <td class="py-3.5 px-4">
          <div class="font-semibold text-white">${esc(it.matched_item_name)}</div>
          <div class="text-xs text-slate-400">${esc(it.restaurant)} ${it.category ? "• " + esc(it.category) : ""} ${m.serving_size ? "• " + esc(m.serving_size) : ""}</div>
          <div class="text-[11px] text-slate-500 mt-0.5">Requested: "${esc(it.requested_item)}"</div>
        </td>
        <td class="py-3.5 px-3 font-mono text-xs text-slate-200">${it.quantity}x</td>
        <td class="py-3.5 px-3 font-mono text-sm font-bold text-amber-300">${Number(m.calories_kcal || 0).toFixed(1)} kcal</td>
        <td class="py-3.5 px-3 font-mono text-sm font-bold text-emerald-400">${Number(m.protein_g || 0).toFixed(1)}g</td>
        <td class="py-3.5 px-3 font-mono text-sm font-bold text-sky-400">${Number(m.carbs_g || 0).toFixed(1)}g</td>
        <td class="py-3.5 px-3 font-mono text-sm font-bold text-rose-400">${Number(m.fat_g || 0).toFixed(1)}g</td>
        <td class="py-3.5 px-3 font-mono text-xs text-purple-300">${fibCell}</td>
        <td class="py-3.5 px-4">
          <div>${confidenceBadgeHtml(it.confidence_score, it.confidence_level)}</div>
          <div class="mt-1.5 space-y-0.5">${reasonsHtml}</div>
        </td>
        <td class="py-3.5 px-4">
          <div class="mb-1">${modalityBadgeHtml(it.doc_modality)}</div>
          <div class="font-mono text-xs text-slate-300">${esc(it.source_file)}</div>
          <div class="text-xs text-slate-400">${esc(it.source_location)}</div>
        </td>
      </tr>
    `;
    })
    .join("");

  // Citations grid
  const citGrid = document.getElementById("citationsGrid");
  citGrid.innerHTML = (data.citations || [])
    .map(
      (c) => `
    <div class="bg-slate-950/90 border border-slate-800 rounded-xl p-3.5 text-xs space-y-2">
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          ${modalityBadgeHtml(c.doc_modality)}
          <span class="font-mono font-semibold text-slate-200 truncate">${esc(c.source_file)}</span>
        </div>
        <span class="font-mono text-emerald-400 shrink-0">${c.retrieval_score}% match</span>
      </div>
      <div class="text-slate-400 font-medium">${esc(c.restaurant)} — ${esc(c.page_or_section)}</div>
      <pre class="text-[11px] font-mono text-slate-300 whitespace-pre-wrap bg-slate-900/90 p-2.5 rounded border border-slate-800/80 max-h-32 overflow-y-auto">${esc(c.snippet)}</pre>
    </div>
  `
    )
    .join("");
}

async function deleteDocument(filename) {
  if (!confirm(`Delete ${filename} and rebuild Vector DB?`)) return;
  const res = await fetch(`/api/documents/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  if (res.ok) {
    await fetchStatsAndDocuments();
  }
}

async function loadCatalogItems(page = 1) {
  catalogCurrentPage = Math.max(1, page);
  const q = document.getElementById("catalogSearchInput").value.trim();
  const rest = document.getElementById("catalogRestaurantSelect").value;
  const params = new URLSearchParams({
    page: String(catalogCurrentPage),
    limit: String(catalogPageSize),
  });
  if (q) params.set("q", q);
  if (rest) params.set("restaurant", rest);

  try {
    const res = await fetch(`/api/items?${params.toString()}`);
    const data = await res.json();
    const tbody = document.getElementById("catalogTableBody");
    const items = data.items || [];
    const total = data.total || 0;
    const totalPages = data.total_pages || 1;

    // Update pagination controls
    const startIdx = total === 0 ? 0 : (catalogCurrentPage - 1) * catalogPageSize + 1;
    const endIdx = Math.min(catalogCurrentPage * catalogPageSize, total);
    const rangeStr = `${startIdx}-${endIdx}`;

    ["catalogRangeText", "catalogRangeTextBottom"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = rangeStr;
    });
    ["catalogTotalCount", "catalogTotalCountBottom"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = total.toLocaleString();
    });
    ["catalogPageNum", "catalogPageNumBottom"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = catalogCurrentPage;
    });
    ["catalogTotalPages", "catalogTotalPagesBottom"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = totalPages;
    });

    const isPrevDisabled = catalogCurrentPage <= 1;
    const isNextDisabled = catalogCurrentPage >= totalPages;

    ["prevCatalogPageBtn", "prevCatalogPageBtnBottom"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.disabled = isPrevDisabled;
    });
    ["nextCatalogPageBtn", "nextCatalogPageBtnBottom"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.disabled = isNextDisabled;
    });

    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="9" class="py-8 text-center text-slate-400">No matching menu items found for "<strong>${esc(q || rest || 'current filters')}</strong>".</td></tr>`;
      return;
    }

    tbody.innerHTML = items
      .map(
        (it) => `
      <tr class="hover:bg-slate-800/40 transition-colors">
        <td class="py-2.5 px-4 font-medium text-white text-xs">
          <div class="font-semibold text-slate-100">${esc(it.item_name)}</div>
          ${it.match_reasons && it.match_reasons.length ? `<div class="text-[10px] text-emerald-400 font-mono mt-0.5">${esc(it.match_reasons[0])}</div>` : ""}
        </td>
        <td class="py-2.5 px-3 text-xs text-slate-300 font-medium">${esc(it.restaurant)}</td>
        <td class="py-2.5 px-3 text-xs text-slate-400">${esc(it.category || "—")} ${it.serving_size ? "(" + esc(it.serving_size) + ")" : ""}</td>
        <td class="py-2.5 px-3 font-mono text-xs text-amber-300 font-semibold">${Number(it.calories_kcal || 0).toFixed(1)} kcal</td>
        <td class="py-2.5 px-3 font-mono text-xs text-emerald-400 font-semibold">${Number(it.protein_g || 0).toFixed(1)}g</td>
        <td class="py-2.5 px-3 font-mono text-xs text-sky-400 font-semibold">${Number(it.carbs_g || 0).toFixed(1)}g</td>
        <td class="py-2.5 px-3 font-mono text-xs text-rose-400 font-semibold">${Number(it.fat_g || 0).toFixed(1)}g</td>
        <td class="py-2.5 px-3 font-mono text-[11px] text-slate-400 max-w-xs truncate" title="${esc(it.source_file)} (${esc(it.source_page_or_section)})">
          ${esc(it.source_file)}
        </td>
        <td class="py-2.5 px-3 text-right">
          <button onclick="addItemToQuery('${esc(it.item_name.replace(/'/g, "\\'"))}', '${esc(it.restaurant.replace(/'/g, "\\'"))}')" class="px-2.5 py-1 rounded bg-emerald-500/15 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/30 text-xs font-medium transition-colors">
            + Add to Order
          </button>
        </td>
      </tr>
    `
      )
      .join("");
  } catch (err) {
    console.error("Failed to load catalog items:", err);
  }
}

function addItemToQuery(itemName, restaurant) {
  const box = document.getElementById("orderQueryInput");
  const entry = `1 ${itemName} (${restaurant})`;
  box.value = box.value.trim() ? `${box.value.trim()}, ${entry}` : entry;
  document.querySelector('[data-tab="calculator"]').click();
}

document.addEventListener("DOMContentLoaded", () => {
  fetchStatsAndDocuments();
  fetchSettings();
  loadCatalogItems();

  // Tab switching
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => {
        b.classList.remove("active", "border-emerald-500", "text-emerald-400");
        b.classList.add("border-transparent", "text-slate-400");
      });
      btn.classList.add("active", "border-emerald-500", "text-emerald-400");
      btn.classList.remove("border-transparent", "text-slate-400");

      const target = btn.dataset.tab;
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
      document.getElementById(`tab-${target}`).classList.remove("hidden");
    });
  });

  // Restaurant filter pills
  document.querySelectorAll(".rest-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".rest-pill").forEach((p) => {
        p.className = "rest-pill px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700";
      });
      pill.className = "rest-pill active px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-medium";
      activeRestaurantFilter = pill.dataset.rest || "";
    });
  });

  // Sample query buttons
  document.querySelectorAll(".sample-query-btn").forEach((b) => {
    b.addEventListener("click", () => {
      document.getElementById("orderQueryInput").value = b.dataset.q;
      runOrderQuery();
    });
  });

  document.getElementById("askMacrosBtn").addEventListener("click", runOrderQuery);
  document.getElementById("orderQueryInput").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      runOrderQuery();
    }
  });

  // LLM Provider / Model selectors
  document.getElementById("providerSelect").addEventListener("change", async () => {
    populateModelDropdown();
    await saveProviderSetting();
  });
  document.getElementById("modelSelect").addEventListener("change", saveProviderSetting);

  // File upload handler
  document.getElementById("fileUploadInput").addEventListener("change", async (e) => {
    const files = e.target.files;
    if (!files || !files.length) return;
    const statusMsg = document.getElementById("uploadStatusMsg");
    statusMsg.classList.remove("hidden");
    statusMsg.textContent = `Uploading ${files.length} file(s) & rebuilding Vector DB...`;

    const formData = new FormData();
    for (const f of files) formData.append("files", f);

    try {
      const res = await fetch("/api/documents/upload", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      statusMsg.textContent = `✓ Uploaded & rebuilt Vector DB v${data.index_stats.index_version} in ${data.index_stats.total_pipeline_ms}ms!`;
      await fetchStatsAndDocuments();
      await loadCatalogItems();
    } catch (err) {
      statusMsg.textContent = `✗ Error: ${err.message}`;
    }
  });

  // Scrape URL button
  document.getElementById("scrapeUrlBtn").addEventListener("click", async () => {
    const url = document.getElementById("scrapeUrlInput").value.trim();
    const customName = document.getElementById("scrapeCustomNameInput").value.trim();
    if (!url) return;
    const btn = document.getElementById("scrapeUrlBtn");
    btn.disabled = true;
    btn.textContent = "Scraping & Rebuilding Vector DB...";
    try {
      const res = await fetch("/api/documents/scrape-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, custom_name: customName }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Scrape failed");
      await fetchStatsAndDocuments();
      await loadCatalogItems();
      document.getElementById("scrapeUrlInput").value = "";
      document.getElementById("scrapeCustomNameInput").value = "";
    } catch (err) {
      alert("Scrape error: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Fetch / Scrape URL & Rebuild Vector DB";
    }
  });

  // Force rebuild button
  document.getElementById("forceRebuildBtn").addEventListener("click", async () => {
    const btn = document.getElementById("forceRebuildBtn");
    btn.disabled = true;
    btn.textContent = "Rebuilding Vector DB...";
    try {
      await fetch("/api/index/rebuild", { method: "POST" });
      await fetchStatsAndDocuments();
      await loadCatalogItems();
    } finally {
      btn.disabled = false;
      btn.textContent = "Force Rebuild Vector DB";
    }
  });

  // Re-seed defaults button
  document.getElementById("reseedDefaultsBtn").addEventListener("click", async () => {
    const btn = document.getElementById("reseedDefaultsBtn");
    btn.disabled = true;
    btn.textContent = "Syncing...";
    try {
      await fetch("/api/documents/seed-defaults?force=true", { method: "POST" });
      await fetchStatsAndDocuments();
      await loadCatalogItems();
    } finally {
      btn.disabled = false;
      btn.textContent = "Re-Sync Default Sources";
    }
  });

  // Catalog pagination and search handlers
  const handlePrevPage = () => {
    if (catalogCurrentPage > 1) {
      loadCatalogItems(catalogCurrentPage - 1);
    }
  };
  const handleNextPage = () => {
    loadCatalogItems(catalogCurrentPage + 1);
  };

  document.getElementById("prevCatalogPageBtn")?.addEventListener("click", handlePrevPage);
  document.getElementById("prevCatalogPageBtnBottom")?.addEventListener("click", handlePrevPage);
  document.getElementById("nextCatalogPageBtn")?.addEventListener("click", handleNextPage);
  document.getElementById("nextCatalogPageBtnBottom")?.addEventListener("click", handleNextPage);

  const searchInput = document.getElementById("catalogSearchInput");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      clearTimeout(catalogDebounceTimer);
      catalogDebounceTimer = setTimeout(() => {
        loadCatalogItems(1);
      }, 200);
    });
  }

  const restSelect = document.getElementById("catalogRestaurantSelect");
  if (restSelect) {
    restSelect.addEventListener("change", () => {
      loadCatalogItems(1);
    });
  }
});

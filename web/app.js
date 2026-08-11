"use strict";

// ---------------------------------------------------------------- state

const state = {
  preset: "30",
  from: null,
  to: null,
  underlier: "",
  fisn: "",
  status: "active,terminated",
  minNotional: "",
  ccy: "",
  metric: "sum_notional",
  aggBy: "underlier_name",
  sortBy: "execution_ts",
  sortDir: "desc",
  page: 0,
  pageSize: 50,
  total: 0,
};

const $ = (id) => document.getElementById(id);
const tooltip = $("tooltip");

// ---------------------------------------------------------------- utils

function fmtCompact(v, prefix = "") {
  if (v == null || isNaN(v)) return "–";
  const abs = Math.abs(v);
  if (abs >= 1e12) return prefix + (v / 1e12).toFixed(2) + "T";
  if (abs >= 1e9) return prefix + (v / 1e9).toFixed(2) + "B";
  if (abs >= 1e6) return prefix + (v / 1e6).toFixed(1) + "M";
  if (abs >= 1e3) return prefix + (v / 1e3).toFixed(1) + "K";
  return prefix + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function fmtNum(v, digits = 2) {
  if (v == null || isNaN(v)) return "";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });
}
function fmtDate(v) {
  if (!v) return "";
  return String(v).slice(0, 10);
}
function fmtTs(v) {
  if (!v) return "";
  return String(v).replace("T", " ").slice(0, 16);
}
function fmtLag(s) {
  if (s == null || isNaN(s) || s < 0) return "";
  if (s < 60) return Math.round(s) + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return (s / 3600).toFixed(1) + "h";
  if (s < 31557600) return Math.round(s / 86400) + "d";
  return (s / 31557600).toFixed(1) + "y";
}
function withUnit(text, unit) {
  const frag = document.createDocumentFragment();
  frag.append(document.createTextNode(text));
  if (unit) {
    const u = document.createElement("span");
    u.className = "unit";
    u.textContent = unit;
    frag.append(u);
  }
  return frag;
}
function isoDaysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function currentDates() {
  if (state.preset === "custom") return { from: state.from, to: state.to };
  if (state.preset === "all") return { from: null, to: null };
  return { from: isoDaysAgo(Number(state.preset)), to: null };
}

function filterParams() {
  const { from, to } = currentDates();
  const p = new URLSearchParams();
  if (state.underlier) p.set("underlier", state.underlier);
  if (state.fisn) p.set("fisn", state.fisn);
  if (state.status) p.set("status", state.status);
  if (from) p.set("date_from", from);
  if (to) p.set("date_to", to);
  if (state.minNotional) p.set("min_notional", state.minNotional);
  if (state.ccy) p.set("ccy", state.ccy);
  return p;
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function showTooltip(html_parts, x, y) {
  tooltip.replaceChildren(...html_parts);
  tooltip.hidden = false;
  const rect = tooltip.getBoundingClientRect();
  const left = Math.min(x + 14, window.innerWidth - rect.width - 8);
  const top = Math.min(y + 14, window.innerHeight - rect.height - 8);
  tooltip.style.left = left + "px";
  tooltip.style.top = top + "px";
}
function hideTooltip() { tooltip.hidden = true; }
function ttEl(cls, text) {
  const d = document.createElement("div");
  d.className = cls;
  d.textContent = text;
  return d;
}

// ---------------------------------------------------------------- tiles

function renderTiles(summary) {
  const tiles = [
    ["Trades", fmtCompact(summary.n)],
    ["Total notional (leg 1)", fmtCompact(summary.notional, "$")],
    ["Median notional", fmtCompact(summary.median, "$")],
    ["Underliers", fmtCompact(summary.underliers)],
  ];
  const root = $("tiles");
  root.replaceChildren(...tiles.map(([label, value]) => {
    const t = document.createElement("div");
    t.className = "tile";
    t.append(ttEl("label", label), ttEl("value", value));
    return t;
  }));
}

// ---------------------------------------------------------------- chart

function renderChart(rows, metric) {
  const root = $("chart");
  const W = root.clientWidth || 1100;
  const H = 260;
  const m = { top: 14, right: 12, bottom: 26, left: 64 };
  const iw = W - m.left - m.right;
  const ih = H - m.top - m.bottom;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.style.padding = "40px 0";
    empty.style.textAlign = "center";
    empty.textContent = "No trades match the current filters.";
    root.replaceChildren(empty);
    return;
  }

  const values = rows.map((r) => Number(r[metric]) || 0);
  const maxV = Math.max(...values, 1);
  // clean tick step: 1/2/5 * 10^k
  const rawStep = maxV / 4;
  const pow = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const step = [1, 2, 5, 10].map((s) => s * pow).find((s) => s >= rawStep);
  const yMax = Math.ceil(maxV / step) * step;

  const css = getComputedStyle(document.documentElement);
  const cGrid = css.getPropertyValue("--grid").trim();
  const cBase = css.getPropertyValue("--baseline").trim();
  const cMuted = css.getPropertyValue("--muted").trim();
  const cSeries = css.getPropertyValue("--series-1").trim();

  // gridlines + y ticks
  for (let v = 0; v <= yMax; v += step) {
    const y = m.top + ih - (v / yMax) * ih;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", m.left); line.setAttribute("x2", m.left + iw);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("stroke", v === 0 ? cBase : cGrid);
    line.setAttribute("stroke-width", "1");
    svg.append(line);
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", m.left - 8); t.setAttribute("y", y + 4);
    t.setAttribute("text-anchor", "end");
    t.setAttribute("fill", cMuted); t.setAttribute("font-size", "11");
    t.textContent = metric === "sum_notional" ? fmtCompact(v, "$") : fmtCompact(v);
    svg.append(t);
  }

  const n = rows.length;
  const band = iw / n;
  const gap = Math.min(2, band * 0.2);
  const barW = Math.min(24, Math.max(1, band - gap));

  rows.forEach((r, i) => {
    const v = Number(r[metric]) || 0;
    const h = (v / yMax) * ih;
    const x = m.left + i * band + (band - barW) / 2;
    const y = m.top + ih - h;
    const rTop = Math.min(4, barW / 2, h);
    // rounded top, square baseline
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d",
      `M ${x} ${m.top + ih} V ${y + rTop} Q ${x} ${y} ${x + rTop} ${y} ` +
      `H ${x + barW - rTop} Q ${x + barW} ${y} ${x + barW} ${y + rTop} V ${m.top + ih} Z`);
    path.setAttribute("fill", cSeries);
    svg.append(path);

    // hit target wider than the mark
    const hit = document.createElementNS(svgNS, "rect");
    hit.setAttribute("x", m.left + i * band); hit.setAttribute("y", m.top);
    hit.setAttribute("width", band); hit.setAttribute("height", ih);
    hit.setAttribute("fill", "transparent");
    hit.addEventListener("pointermove", (e) => {
      path.setAttribute("opacity", "0.75");
      showTooltip([
        ttEl("tt-value", metric === "sum_notional" ? fmtCompact(v, "$") : fmtCompact(v)),
        ttEl("tt-label", `${r.key_label} · ${r.n_trades.toLocaleString()} trades`),
      ], e.clientX, e.clientY);
    });
    hit.addEventListener("pointerleave", () => {
      path.removeAttribute("opacity");
      hideTooltip();
    });
    svg.append(hit);
  });

  // x labels: ~8 evenly spaced
  const every = Math.max(1, Math.round(n / 8));
  rows.forEach((r, i) => {
    if (i % every !== 0 && i !== n - 1) return;
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", m.left + i * band + band / 2);
    t.setAttribute("y", m.top + ih + 18);
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("fill", cMuted); t.setAttribute("font-size", "11");
    t.textContent = r.key_label;
    svg.append(t);
  });

  root.replaceChildren(svg);
}

// ---------------------------------------------------------------- tables

function makeTable(tableEl, headers, rows, onHeaderClick) {
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  headers.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h.label + (h.key === state.sortBy && onHeaderClick ? (state.sortDir === "desc" ? " ↓" : " ↑") : "");
    if (h.sortable && onHeaderClick) {
      th.className = "sortable";
      th.addEventListener("click", () => onHeaderClick(h.key));
    }
    trh.append(th);
  });
  thead.append(trh);
  const tbody = document.createElement("tbody");
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    headers.forEach((h) => {
      const td = document.createElement("td");
      const v = h.render ? h.render(r) : r[h.key];
      if (v instanceof Node) td.append(v);
      else td.textContent = v == null ? "" : String(v);
      if (h.cls) td.className = h.cls;
      tr.append(td);
    });
    tbody.append(tr);
  });
  tableEl.replaceChildren(thead, tbody);
}

function badge(status) {
  const s = document.createElement("span");
  s.className = "badge " + status;
  s.textContent = status;
  return s;
}

const TRADE_COLS = [
  { key: "execution_ts", label: "Executed", sortable: true, render: (r) => fmtTs(r.execution_ts) },
  { key: "underlier_name", label: "Underlier", sortable: true, cls: "name" },
  { key: "upi_fisn", label: "Product", cls: "name" },
  { key: "notional_leg1", label: "Notional", sortable: true, render: (r) => fmtCompact(r.notional_leg1, "") + (r.notional_capped ? "+" : "") },
  { key: "notional_ccy_leg1", label: "Ccy" },
  { key: "total_notional_qty_leg1", label: "Qty", render: (r) => r.total_notional_qty_leg1 == null ? "" : withUnit(fmtNum(r.total_notional_qty_leg1, 0), r.qty_unit_leg1) },
  { key: "per_unit", label: "$/unit", sortable: true, render: (r) => fmtNum(r.per_unit, 2) },
  { key: "price", label: "Price", sortable: true, render: (r) => r.price == null ? "" : withUnit(fmtNum(r.price, 4), r.price_unit) },
  { key: "strike_price", label: "Strike", sortable: true, render: (r) => fmtNum(r.strike_price, 4) },
  { key: "moneyness", label: "Mny", sortable: true, render: (r) => r.moneyness == null ? "" : Number(r.moneyness).toFixed(2) + "x" },
  { key: "option_type", label: "Opt" },
  { key: "option_premium", label: "Premium", sortable: true, render: (r) => r.option_premium == null ? "" : fmtCompact(r.option_premium, "$") },
  { key: "tenor_yrs", label: "Tenor", sortable: true, render: (r) => r.tenor_yrs == null ? "" : Number(r.tenor_yrs).toFixed(1) + "y" },
  { key: "expiration_date", label: "Expiry", sortable: true, render: (r) => fmtDate(r.expiration_date) },
  { key: "lag_seconds", label: "Lag", sortable: true, render: (r) => fmtLag(r.lag_seconds) },
  { key: "platform_id", label: "Venue" },
  { key: "cleared", label: "Clr" },
  { key: "status", label: "Status", render: (r) => badge(r.status) },
];

const COLS_LS_KEY = "otc_visible_cols";
let visibleColKeys = new Set(
  JSON.parse(localStorage.getItem(COLS_LS_KEY) || "null") || TRADE_COLS.map((c) => c.key));
function visibleCols() {
  return TRADE_COLS.filter((c) => visibleColKeys.has(c.key));
}

// ---- structure grouping: adjacent rows sharing UPI + expiry + a 5s execution
// bucket collapse into an expandable package when sorted by execution time.

function groupKey(r) {
  if (!r.execution_ts || !r.upi) return null;
  const t = Date.parse(String(r.execution_ts).replace(" ", "T"));
  if (isNaN(t)) return null;
  return r.upi + "|" + (r.expiration_date || "") + "|" + Math.floor(t / 5000);
}

function groupSummaryValue(col, legs) {
  const first = legs[0];
  const sum = (k) => legs.some((l) => l[k] != null)
    ? legs.reduce((a, l) => a + (Number(l[k]) || 0), 0) : null;
  switch (col.key) {
    case "execution_ts": {
      const frag = document.createDocumentFragment();
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.textContent = "▸";
      frag.append(caret, document.createTextNode(fmtTs(first.execution_ts)));
      return frag;
    }
    case "underlier_name": return first.underlier_name;
    case "upi_fisn": return `${legs.length}-leg structure`;
    case "notional_leg1": return fmtCompact(sum("notional_leg1"), "");
    case "notional_ccy_leg1": return first.notional_ccy_leg1;
    case "total_notional_qty_leg1": {
      const units = new Set(legs.map((l) => l.qty_unit_leg1));
      return units.size === 1 && legs[0].total_notional_qty_leg1 != null
        ? withUnit(fmtNum(sum("total_notional_qty_leg1"), 0), first.qty_unit_leg1) : "";
    }
    case "strike_price": {
      const ks = legs.map((l) => l.strike_price).filter((v) => v != null);
      if (!ks.length) return "";
      const lo = Math.min(...ks), hi = Math.max(...ks);
      return lo === hi ? fmtNum(lo, 0) : `${fmtNum(lo, 0)}–${fmtNum(hi, 0)}`;
    }
    case "option_type": return [...new Set(legs.map((l) => l.option_type).filter(Boolean))].join("/");
    case "option_premium": { const s = sum("option_premium"); return s == null ? "" : fmtCompact(s, "$"); }
    case "tenor_yrs": return first.tenor_yrs == null ? "" : Number(first.tenor_yrs).toFixed(1) + "y";
    case "expiration_date": return fmtDate(first.expiration_date);
    case "lag_seconds": {
      const ls = legs.map((l) => l.lag_seconds).filter((v) => v != null);
      return ls.length ? fmtLag(Math.min(...ls)) : "";
    }
    case "platform_id": return first.platform_id;
    case "cleared": return first.cleared;
    case "status": return badge(first.status);
    default: return "";
  }
}

function buildTradesTable(rows, onHeaderClick) {
  const cols = visibleCols();
  const tableEl = $("trades-table");
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  cols.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h.label + (h.key === state.sortBy ? (state.sortDir === "desc" ? " ↓" : " ↑") : "");
    if (h.sortable) {
      th.className = "sortable";
      th.addEventListener("click", () => onHeaderClick(h.key));
    }
    trh.append(th);
  });
  thead.append(trh);

  const groupingOn = state.sortBy === "execution_ts";
  const blocks = [];
  rows.forEach((r) => {
    const k = groupingOn ? groupKey(r) : null;
    const last = blocks[blocks.length - 1];
    if (k && last && last.key === k) last.legs.push(r);
    else blocks.push({ key: k, legs: [r] });
  });

  const renderRow = (r, cls) => {
    const tr = document.createElement("tr");
    if (cls) tr.className = cls;
    cols.forEach((h) => {
      const td = document.createElement("td");
      const v = h.render ? h.render(r) : r[h.key];
      if (v instanceof Node) td.append(v);
      else td.textContent = v == null ? "" : String(v);
      if (h.cls) td.className = h.cls;
      tr.append(td);
    });
    return tr;
  };

  const tbody = document.createElement("tbody");
  blocks.forEach((b) => {
    if (b.legs.length === 1) {
      tbody.append(renderRow(b.legs[0]));
      return;
    }
    const grp = document.createElement("tr");
    grp.className = "grp";
    cols.forEach((h) => {
      const td = document.createElement("td");
      const v = groupSummaryValue(h, b.legs);
      if (v instanceof Node) td.append(v);
      else td.textContent = v == null ? "" : String(v);
      if (h.cls) td.className = h.cls;
      grp.append(td);
    });
    const legRows = b.legs.map((r) => {
      const tr = renderRow(r, "leg");
      tr.hidden = true;
      return tr;
    });
    grp.addEventListener("click", () => {
      const open = !legRows[0].hidden;
      legRows.forEach((tr) => { tr.hidden = open; });
      const caret = grp.querySelector(".caret");
      if (caret) caret.textContent = open ? "▸" : "▾";
    });
    tbody.append(grp, ...legRows);
  });
  tableEl.replaceChildren(thead, tbody);
}

// ---------------------------------------------------------------- loads

let reqSeq = 0;

async function loadAll() {
  const seq = ++reqSeq;
  document.body.style.cursor = "progress";
  try {
    await Promise.all([loadChartAndTiles(seq), loadAgg(seq), loadTrades(seq)]);
  } finally {
    document.body.style.cursor = "";
  }
}

async function loadChartAndTiles(seq) {
  const { from } = currentDates();
  // pick granularity so marks stay legible
  const days = state.preset === "all" ? 9999 : state.preset === "custom"
    ? (state.from && state.to ? (new Date(state.to) - new Date(state.from)) / 864e5 : 9999)
    : Number(state.preset);
  const gran = days <= 130 ? "execution_date" : days <= 800 ? "week" : "month";
  $("chart-title").textContent =
    (gran === "execution_date" ? "Daily" : gran === "week" ? "Weekly" : "Monthly") +
    (state.metric === "sum_notional" ? " notional (leg 1)" : " trade count");

  const p = filterParams();
  p.set("group_by", gran);
  const rows = await getJSON("/api/aggregate?" + p);
  if (seq !== reqSeq) return;
  rows.forEach((r) => { r.key_label = fmtDate(r.key); });

  renderChart(rows, state.metric);

  const p2 = filterParams();
  p2.set("group_by", "all");
  const overall = await getJSON("/api/aggregate?" + p2);
  if (seq !== reqSeq) return;
  const o = overall[0] || {};
  const summary = {
    n: Number(o.n_trades || 0),
    notional: o.sum_notional != null ? Number(o.sum_notional) : null,
    median: o.median_notional != null ? Number(o.median_notional) : null,
    underliers: null,
  };
  const p3 = filterParams();
  p3.set("group_by", "underlier_name");
  p3.set("limit", "5000");
  const byUnd = await getJSON("/api/aggregate?" + p3);
  if (seq !== reqSeq) return;
  summary.underliers = byUnd.length;
  renderTiles(summary);
}

async function loadAgg(seq) {
  const p = filterParams();
  p.set("group_by", state.aggBy);
  p.set("limit", "200");
  const rows = await getJSON("/api/aggregate?" + p);
  if (seq !== reqSeq) return;
  const isDate = ["execution_date", "week", "month"].includes(state.aggBy);
  makeTable($("agg-table"), [
    { key: "key", label: "Group", cls: "name", render: (r) => isDate ? fmtDate(r.key) : (r.key ?? "(blank)") },
    { key: "n_trades", label: "Trades", render: (r) => fmtNum(r.n_trades, 0) },
    { key: "sum_notional", label: "Σ notional", render: (r) => fmtCompact(r.sum_notional, "$") },
    { key: "median_notional", label: "Median notional", render: (r) => fmtCompact(r.median_notional, "$") },
    { key: "avg_price", label: "Avg price", render: (r) => fmtNum(r.avg_price, 2) },
  ], rows, null);
}

async function loadTrades(seq) {
  const p = filterParams();
  p.set("sort_by", state.sortBy);
  p.set("sort_dir", state.sortDir);
  p.set("limit", String(state.pageSize));
  p.set("offset", String(state.page * state.pageSize));
  const data = await getJSON("/api/trades?" + p);
  if (seq !== reqSeq) return;
  state.total = data.total;
  $("trade-count").textContent = `· ${data.total.toLocaleString()} match`;
  const maxPage = Math.max(0, Math.ceil(data.total / state.pageSize) - 1);
  $("pg-info").textContent = `page ${state.page + 1} / ${maxPage + 1}`;
  $("pg-prev").disabled = state.page === 0;
  $("pg-next").disabled = state.page >= maxPage;
  state.lastRows = data.rows;
  buildTradesTable(data.rows, onSortHeader);
}

function onSortHeader(key) {
  if (state.sortBy === key) state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
  else { state.sortBy = key; state.sortDir = "desc"; }
  state.page = 0;
  loadTrades(++reqSeq);
}

function wireColumnPicker() {
  const btn = $("col-btn");
  const menu = $("col-menu");
  TRADE_COLS.forEach((c) => {
    const row = document.createElement("label");
    row.className = "ac-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = visibleColKeys.has(c.key);
    cb.addEventListener("change", () => {
      if (cb.checked) visibleColKeys.add(c.key);
      else visibleColKeys.delete(c.key);
      localStorage.setItem(COLS_LS_KEY, JSON.stringify([...visibleColKeys]));
      if (state.lastRows) buildTradesTable(state.lastRows, onSortHeader);
    });
    const span = document.createElement("span");
    span.textContent = c.label;
    row.append(cb, span);
    menu.append(row);
  });
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", (e) => {
    if (!menu.contains(e.target) && e.target !== btn) menu.hidden = true;
  });
}

// ---------------------------------------------------------------- autocomplete

let acTimer = null;
function wireAutocomplete() {
  const input = $("f-underlier");
  const box = $("ac");
  input.addEventListener("input", () => {
    clearTimeout(acTimer);
    acTimer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) { box.hidden = true; applyUnderlier(q); return; }
      const rows = await getJSON("/api/underliers?q=" + encodeURIComponent(q));
      box.replaceChildren(...rows.map((r) => {
        const d = document.createElement("div");
        d.className = "ac-item";
        const name = document.createElement("span");
        name.textContent = r.underlier_name;
        const n = document.createElement("span");
        n.className = "n";
        n.textContent = Number(r.n_trades).toLocaleString();
        d.append(name, n);
        d.addEventListener("click", () => {
          input.value = r.underlier_name;
          box.hidden = true;
          applyUnderlier(r.underlier_name);
        });
        return d;
      }));
      box.hidden = rows.length === 0;
      applyUnderlier(q);
    }, 250);
  });
  document.addEventListener("click", (e) => {
    if (!box.contains(e.target) && e.target !== input) box.hidden = true;
  });
}
function applyUnderlier(v) {
  if (state.underlier === v) return;
  state.underlier = v;
  state.page = 0;
  loadAll();
}

// ---------------------------------------------------------------- wiring

async function init() {
  const meta = await getJSON("/api/meta");
  $("freshness").textContent =
    `${Number(meta.n_trades).toLocaleString()} final prints · ${fmtDate(meta.min_date)} → ${fmtDate(meta.max_date)} · latest file ${fmtDate(meta.latest_file)}`;
  const fisn = $("f-fisn");
  meta.fisns.forEach((f) => {
    const o = document.createElement("option");
    o.value = f; o.textContent = f;
    fisn.append(o);
  });
  const ccy = $("f-ccy");
  meta.currencies.forEach((c) => {
    const o = document.createElement("option");
    o.value = c; o.textContent = c;
    ccy.append(o);
  });

  $("f-preset").addEventListener("change", (e) => {
    state.preset = e.target.value;
    $("custom-dates").hidden = state.preset !== "custom";
    state.page = 0;
    if (state.preset !== "custom") loadAll();
  });
  $("f-from").addEventListener("change", (e) => { state.from = e.target.value; state.page = 0; loadAll(); });
  $("f-to").addEventListener("change", (e) => { state.to = e.target.value; state.page = 0; loadAll(); });
  $("f-fisn").addEventListener("change", (e) => { state.fisn = e.target.value; state.page = 0; loadAll(); });
  $("f-status").addEventListener("change", (e) => { state.status = e.target.value; state.page = 0; loadAll(); });
  $("f-minnotional").addEventListener("change", (e) => { state.minNotional = e.target.value; state.page = 0; loadAll(); });
  $("f-ccy").addEventListener("change", (e) => { state.ccy = e.target.value; state.page = 0; loadAll(); });

  document.querySelectorAll(".seg-btn").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.metric = b.dataset.metric;
      loadChartAndTiles(++reqSeq);
    });
  });

  $("agg-by").addEventListener("change", (e) => { state.aggBy = e.target.value; loadAgg(++reqSeq); });
  $("pg-prev").addEventListener("click", () => { state.page--; loadTrades(++reqSeq); });
  $("pg-next").addEventListener("click", () => { state.page++; loadTrades(++reqSeq); });

  wireColumnPicker();
  wireAutocomplete();
  await loadAll();
}

init().catch((e) => {
  document.body.prepend(Object.assign(document.createElement("pre"), { textContent: "Failed to load: " + e.message }));
});

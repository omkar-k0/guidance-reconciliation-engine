// Reads data/results.json and data/eval.json produced by `gre export`.
// If they are missing, the page shows an honest empty state instead of fake numbers.
const $ = (s) => document.querySelector(s);
const usd = (v) => v == null ? "n/a" : (Math.abs(v) >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : `$${(v / 1e6).toFixed(0)}M`);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function load(path) {
  try { const r = await fetch(path, { cache: "no-store" }); return r.ok ? r.json() : null; } catch { return null; }
}

function metric(value, label, measured) {
  return `<div class="metric"><div class="v">${value}</div><div class="k">${label} <span class="tag ${measured ? "measured" : "pending"}">${measured ? "measured" : "pending"}</span></div></div>`;
}

function renderMetrics(results, ev) {
  const n = results.length;
  const llm = results.filter((r) => r.decision.route === "llm").length;
  const html = [
    metric(n || "0", "guidance items processed", n > 0),
    metric(n ? `${Math.round((llm / n) * 100)}%` : "–", "escalated to the LLM", n > 0),
    metric(ev ? `${Math.round(ev.accuracy * 100)}%` : "–", ev ? `accuracy on ${ev.n_labelled} hand-labelled items` : "accuracy vs hand labels", !!ev),
    metric(ev?.by_route?.rule ? `${Math.round(ev.by_route.rule.accuracy * 100)}%` : "–", "accuracy when rules decide", !!ev?.by_route?.rule),
  ];
  $("#metrics").innerHTML = html.join("");
}

function card(r) {
  const g = r.guidance, a = r.actual, d = r.decision;
  const cites = (d.citations || []).filter((c) => c.source === "guidance").map((c) => `<blockquote>“${esc(c.quote)}”</blockquote>`).join("");
  return `<article class="card">
    <div class="top"><span class="lab ${d.label}">${d.label}</span><b>${esc(g.ticker)}</b><span>filed ${esc(g.filed)}</span>
      <span>route: ${d.route}</span><span>confidence ${(d.confidence * 100).toFixed(0)}%</span></div>
    ${cites || `<blockquote>${esc(g.text)}</blockquote>`}
    <div class="reason">Guided ${usd(g.low)} to ${usd(g.high)} · reported ${usd(a.value)} ${a.period_end ? `(quarter ending ${esc(a.period_end)})` : ""}<br>${esc(d.reason)}</div>
    <div class="reason"><a href="${esc(g.url)}" target="_blank" rel="noopener">Open the filing on sec.gov</a></div>
  </article>`;
}

function renderList(results) {
  const t = $("#f-ticker").value, l = $("#f-label").value, rt = $("#f-route").value;
  const rows = results.filter((r) => (!t || r.guidance.ticker === t) && (!l || r.decision.label === l) && (!rt || r.decision.route === rt))
    .sort((a, b) => b.guidance.filed.localeCompare(a.guidance.filed));
  $("#list").innerHTML = rows.map(card).join("") || `<div class="empty">No decisions match these filters.</div>`;
}

(async () => {
  const results = (await load("data/results.json")) || [];
  const ev = await load("data/eval.json");
  renderMetrics(results, ev);
  if (!results.length) { $("#empty").hidden = false; return; }
  const tickers = [...new Set(results.map((r) => r.guidance.ticker))].sort();
  $("#f-ticker").insertAdjacentHTML("beforeend", tickers.map((t) => `<option>${t}</option>`).join(""));
  ["#f-ticker", "#f-label", "#f-route"].forEach((s) => $(s).addEventListener("change", () => renderList(results)));
  renderList(results);
})();

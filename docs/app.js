(() => {
  "use strict";

  const PAGE = 50;
  const state = { data: null, tab: "USA", type: "all", sort: "combined_score", query: "", shown: PAGE };
  const $ = (id) => document.getElementById(id);

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 2) => (v === null || v === undefined || v === "" || isNaN(v)) ? "–" : Number(v).toFixed(d);
  const pct = (v) => {
    if (v === null || v === undefined || isNaN(v)) return '<b>–</b>';
    const cls = v > 0 ? "up" : v < 0 ? "down" : "";
    return `<b class="${cls}">${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%</b>`;
  };
  const money = (v, cur) => {
    if (v === null || v === undefined) return "–";
    const sym = { USD: "$", CAD: "C$", INR: "₹" }[cur] || "";
    return sym + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  };
  const ago = (iso) => {
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const h = Math.round(mins / 60);
    return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
  };

  async function load() {
    $("status").textContent = "Loading…";
    try {
      const res = await fetch(`data/latest.json?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      state.data = await res.json();
    } catch (e) {
      state.data = null;
      $("status").textContent = "No scan results yet. The first scan runs after the US close on weekdays.";
      render();
      return;
    }
    const d = state.data;
    $("status").textContent = `Updated ${ago(d.generated_at)} · cycle started ${d.cycle_start}`;
    const pctDone = d.us_universe ? Math.round((d.us_with_data / d.us_universe) * 100) : 0;
    $("progress").hidden = false;
    $("progress-fill").style.width = `${pctDone}%`;
    $("progress-label").textContent =
      `US scanned ${d.us_with_data}/${d.us_universe} · batch ${d.batch.index}/${d.batch.of}`;

    const notes = d.notes || [];
    $("notes").hidden = notes.length === 0;
    $("notes").innerHTML = notes.length
      ? `<strong>Heads up</strong><ul>${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
    render();
  }

  function filtered() {
    const rows = (state.data?.markets?.[state.tab] || []).slice();
    const q = state.query.trim().toLowerCase();
    let out = rows.filter((r) =>
      (state.type === "all" || r.type === state.type) &&
      (!q || r.ticker.toLowerCase().includes(q) || (r.company || "").toLowerCase().includes(q)));
    const key = state.sort;
    if (key === "pe_low") {
      out.sort((a, b) => (a.pe > 0 ? a.pe : Infinity) - (b.pe > 0 ? b.pe : Infinity));
    } else if (key !== "combined_score") {
      out.sort((a, b) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity));
    }
    return out;
  }

  function card(r) {
    const astro = r.astro_score === null || r.astro_score === undefined ? "–" : num(r.astro_score, 0);
    return `<li class="card" tabindex="0">
      <div class="head">
        <div class="rank">${r.rank}</div>
        <div class="name">
          <div><span class="ticker">${esc(r.ticker)}</span><span class="tag">${esc(r.type)}</span></div>
          <div class="company">${esc(r.company || "")}</div>
        </div>
        <div class="score"><b>${num(r.combined_score, 0)}</b><small>combined</small></div>
      </div>
      <div class="meter"><span style="width:${Math.max(0, Math.min(100, r.combined_score || 0))}%"></span></div>
      <div class="stats">
        <div>Close<b>${money(r.close, r.currency)}</b></div>
        <div>5 sess.${pct(r.chg_5d)}</div>
        <div>1 month${pct(r.chg_1m)}</div>
        <div>P/E<b>${r.pe ? num(r.pe, 1) : "–"}</b></div>
      </div>
      <div class="details"><dl>
        <dt>Fundamental</dt><dd>${num(r.fundamental_score, 1)}</dd>
        <dt>Astro</dt><dd>${astro}${r.astro_reason ? " · " + esc(r.astro_reason) : ""}</dd>
        <dt>Below 100-day high</dt><dd>${num(r.below_100d_high, 1)}%</dd>
        <dt>Close date</dt><dd>${esc(r.close_date)}</dd>
        <dt>Source</dt><dd>${esc(r.price_source)}</dd>
      </dl></div>
    </li>`;
  }

  function renderRanking() {
    const rows = filtered();
    $("list").innerHTML = rows.slice(0, state.shown).map(card).join("");
    $("more").hidden = rows.length <= state.shown;
    $("more").textContent = `Show more (${rows.length - state.shown} left)`;
    const empty = rows.length === 0;
    $("empty").hidden = !empty;
    if (empty) {
      $("empty").textContent = state.data
        ? (state.query || state.type !== "all" ? "No matches." : `No ${state.tab} data in this scan yet.`)
        : "Waiting for the first scan.";
    }
  }

  function renderTracker() {
    const t = state.data?.tracker || { picks: [], summary: [] };
    const rows = (t.summary || []).filter((r) => r.some((c) => String(c).trim()) && !/^SUMMARY/i.test(r[0] || ""));
    const verdict = rows.find((r) => /^Verdict/i.test(r[0] || ""));
    const table = rows.filter((r) => r !== verdict);
    $("tracker-summary").innerHTML = table.length
      ? table.map((r, i) => `<tr>${r.map((c) => i === 0 ? `<th>${esc(c)}</th>` : `<td>${esc(c)}</td>`).join("")}</tr>`).join("")
        + (verdict ? `<tr><th>Verdict</th><td colspan="7"><b>${esc(verdict[1] || "")}</b></td></tr>` : "")
      : `<tr><td>Summary unavailable. Make sure the feed sheet has access to the tracker.</td></tr>`;

    const picks = t.picks || [];
    $("tracker-empty").hidden = picks.length > 0;
    $("tracker-picks").innerHTML = picks.map((p) => {
      const ret = parseFloat(p["Return %"]);
      const ex = parseFloat(p["Excess vs index %"]);
      return `<li class="card"><div class="head">
          <div class="rank">${esc((p.Group || "").slice(0, 1))}</div>
          <div class="name"><div><span class="ticker">${esc(p.Ticker)}</span><span class="tag">${esc(p.Market)} · ${esc(p.Group)}</span></div>
            <div class="company">${esc(p["Astro reason"] || "")}</div></div>
          <div class="score">${pct(isNaN(ret) ? null : ret)}<small>vs index ${isNaN(ex) ? "–" : (ex > 0 ? "+" : "") + ex.toFixed(1) + "%"}</small></div>
        </div></li>`;
    }).join("");
  }

  function render() {
    const tracker = state.tab === "tracker";
    $("ranking-view").hidden = tracker;
    $("tracker-view").hidden = !tracker;
    tracker ? renderTracker() : renderRanking();
  }

  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
    state.tab = b.dataset.tab;
    state.shown = PAGE;
    render();
    window.scrollTo({ top: 0 });
  }));
  document.querySelectorAll("#type-chips button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#type-chips button").forEach((x) => x.classList.toggle("on", x === b));
    state.type = b.dataset.type;
    state.shown = PAGE;
    render();
  }));
  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; render(); });
  $("search").addEventListener("input", (e) => { state.query = e.target.value; state.shown = PAGE; render(); });
  $("more").addEventListener("click", () => { state.shown += PAGE; render(); });
  $("refresh").addEventListener("click", load);
  $("list").addEventListener("click", (e) => e.target.closest(".card")?.classList.toggle("open"));

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  load();
})();

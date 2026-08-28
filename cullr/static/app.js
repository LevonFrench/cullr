"use strict";
/* cullr UI: dense poster triage over the Radarr, Sonarr and Media-Hoarder libraries. */

const $  = s => document.querySelector(s);
const GB = 2 ** 30, TB = 2 ** 40;
const fmt = b => b >= TB ? (b / TB).toFixed(2) + " TB"
              : b >= GB ? (b / GB).toFixed(1) + " GB"
              : (b / 2 ** 20).toFixed(0) + " MB";
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const LS = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("cullr:" + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("cullr:" + k, JSON.stringify(v)); } catch {} },
};

let DATA  = { movie: [], series: [], mh: [], mhseries: [], disks: {} };
let CONF  = { read_only: false, dry_run: false, mh_allow_delete: false, version: "" };
let marks = new Map();
let view  = [];
let drives = new Set();
let cursor = -1;
let rendered = 0;
const PAGE = 300;

const keyOf = it => it.kind + ":" + it.id;
// The visible library is a source (Radarr/Sonarr or Media-Hoarder) crossed with
// a media type, so the two pickers together name one bucket in DATA.
const bucket = () => {
  const mh = $("#source").value === "mh";
  return $("#kind").value === "series" ? (mh ? "mhseries" : "series")
                                       : (mh ? "mh" : "movie");
};
const pool  = () => DATA[bucket()] || [];
const everything = () => [...DATA.movie, ...DATA.series, ...DATA.mh, ...DATA.mhseries];

/* ------------------------------------------------------------ load */

async function boot() {
  try {
    CONF = await (await fetch("/api/config")).json();
  } catch {}
  $("#ver").textContent = CONF.version ? "v" + CONF.version : "";
  const b = [];
  if (CONF.read_only) b.push(`<span class="banner ro">READ-ONLY</span>`);
  if (CONF.dry_run)   b.push(`<span class="banner dry">DRY-RUN</span>`);
  $("#banners").innerHTML = b.join(" ");
  if (CONF.read_only) { $("#commit").disabled = true; $("#commit").title = "server is read-only"; }

  // Offer only the sources this server actually has, and skip the picker
  // entirely when there is nothing to pick between.
  const src = CONF.sources || {};
  const has = { arr: !!(src.radarr || src.sonarr), mh: !!src.mediahoarder };
  const sel = $("#source");
  for (const o of [...sel.options]) if (!has[o.value]) o.remove();
  sel.hidden = sel.options.length < 2;
  if (sel.options.length) sel.value = LS.get("source", sel.options[0].value);
  if (!has[sel.value] && sel.options.length) sel.value = sel.options[0].value;
  sel.addEventListener("change", () => LS.set("source", sel.value));

  const th = LS.get("theme", "");
  if (th) document.documentElement.setAttribute("data-theme", th);
  const cs = LS.get("cardsize", 118);
  $("#cardsize").value = cs;
  document.documentElement.style.setProperty("--card", cs + "px");

  restoreMarks();
  loadPresets();
  await load();
  layout();
}

async function load(force) {
  try {
    const d = await (await fetch("/api/data" + (force ? "?force=1" : ""))).json();
    DATA.movie = d.movies || []; DATA.series = d.series || [];
    DATA.mh = d.mh || []; DATA.mhseries = d.mhseries || [];
    DATA.disks = d.disks || {};
    if (d.errors && Object.keys(d.errors).length)
      console.warn("cullr source errors", d.errors);
    const live = new Set(everything().map(keyOf));
    for (const k of [...marks.keys()]) if (!live.has(k)) marks.delete(k);
    saveMarks();
  } catch (e) {
    $("#main").innerHTML = `<div class="empty">Could not reach the cullr server.<br>${esc(e)}</div>`;
    return;
  }
  fillFacets(); render();
}

/* marks survive a reload so a long triage session is not lost */
function saveMarks() { LS.set("marks", [...marks.keys()]); }
function restoreMarks() {
  const keys = LS.get("marks", []);
  if (Array.isArray(keys)) pendingMarks = new Set(keys);
}
let pendingMarks = new Set();

function fillFacets() {
  const items = pool();
  const studios = new Map(), genres = new Map(), quals = new Map();
  for (const it of items) {
    if (it.studio) studios.set(it.studio, (studios.get(it.studio) || 0) + it.size);
    for (const g of it.genres || []) genres.set(g, (genres.get(g) || 0) + it.size);
    quals.set(it.quality, (quals.get(it.quality) || 0) + it.size);
  }
  const opt = (sel, map, label) => {
    const cur = sel.value;
    sel.innerHTML = `<option value="">${label}</option>` +
      [...map.entries()].sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `<option value="${esc(k)}">${esc(k)} — ${fmt(v)}</option>`).join("");
    if (map.has(cur)) sel.value = cur;
  };
  opt($("#studio"), studios, "All studios");
  opt($("#genre"),  genres,  "All genres");
  opt($("#qual"),   quals,   "All quality");

  // resolve marks restored from localStorage now that data exists
  if (pendingMarks.size) {
    for (const it of everything())
      if (pendingMarks.has(keyOf(it))) marks.set(keyOf(it), it);
    pendingMarks = new Set();
  }
}

/* ------------------------------------------------------------ filter */

function compute() {
  const q  = $("#q").value.trim().toLowerCase();
  const st = $("#studio").value, gn = $("#genre").value, ql = $("#qual").value;
  const y1 = +$("#y1").value || 0, y2 = +$("#y2").value || 9999;
  const lo = (+$("#mingb").value || 0) * GB;
  const hi = (+$("#maxgb").value || 0) * GB || Infinity;
  const unmon = $("#unmon").checked, only = $("#markedonly").checked;

  view = pool().filter(it =>
    (!q || it.title.toLowerCase().includes(q) || (it.studio || "").toLowerCase().includes(q)) &&
    (!st || it.studio === st) &&
    (!gn || (it.genres || []).includes(gn)) &&
    (!ql || it.quality === ql) &&
    it.year >= y1 && it.year <= y2 &&
    it.size >= lo && it.size <= hi &&
    (!unmon || !it.monitored) &&
    (!only || marks.has(keyOf(it))) &&
    (!drives.size || drives.has(it.drive))
  );

  const c = {
    size:     (a, b) => b.size - a.size,
    bloat:    (a, b) => b.bloat - a.bloat || b.size - a.size,
    gph:      (a, b) => b.gph - a.gph || b.size - a.size,
    year:     (a, b) => b.year - a.year || b.size - a.size,
    yearold:  (a, b) => a.year - b.year || b.size - a.size,
    rating:   (a, b) => (a.rating || 10) - (b.rating || 10) || b.size - a.size,
    votes:    (a, b) => (a.votes || 0) - (b.votes || 0) || b.size - a.size,
    added:    (a, b) => (b.added || "").localeCompare(a.added || "") || b.size - a.size,
    addedold: (a, b) => (a.added || "").localeCompare(b.added || "") || b.size - a.size,
    title:    (a, b) => a.title.localeCompare(b.title),
  }[$("#sort").value];
  view.sort(c);
}

const groupKey = it => ({
  studio:  it.studio || "— no studio —",
  genre:   (it.genres || [])[0] || "— no genre —",
  quality: it.quality,
  drive:   it.drive + ":",
  decade:  it.year ? (Math.floor(it.year / 10) * 10) + "s" : "— no year —",
}[$("#groupby").value]);

/* ------------------------------------------------------------ render */

function cardHTML(it, idx) {
  const k = keyOf(it);
  return `<div class="card${marks.has(k) ? " marked" : ""}" data-k="${k}" data-i="${idx}">
    ${it.poster === false
      ? `<div class="noimg">${esc(it.title)}</div>`
      : `<img loading="lazy" src="/poster/${it.kind}/${it.id}" alt=""
         onerror="this.replaceWith(Object.assign(document.createElement('div'),
                  {className:'noimg',textContent:this.getAttribute('data-t')}))"
         data-t="${esc(it.title)}">`}
    <div class="dv">${esc(it.drive)}</div><div class="q">${esc(it.quality)}</div>
    <div class="meta">
      <div class="ttl">${esc(it.title)}</div>
      <div class="sub"><span>${it.year || ""}</span><span class="sz">${fmt(it.size)}</span></div>
    </div></div>`;
}

function render(keepScroll) {
  const top = keepScroll ? $("#main").scrollTop : 0;
  compute();
  rendered = 0;
  const main = $("#main");

  if (!view.length) {
    main.innerHTML = `<div class="empty">Nothing matches those filters.</div>`;
  } else if ($("#groupby").value) {
    const g = new Map();
    view.forEach(it => {
      const n = groupKey(it);
      if (!g.has(n)) g.set(n, []);
      g.get(n).push(it);
    });
    const groups = [...g.entries()]
      .sort((a, b) => b[1].reduce((s, x) => s + x.size, 0) - a[1].reduce((s, x) => s + x.size, 0));
    let i = 0;
    main.innerHTML = groups.map(([name, items]) => {
      const tot = items.reduce((s, x) => s + x.size, 0);
      const body = items.map(it => cardHTML(it, i++)).join("");
      return `<div class="ghead"><h2>${esc(name)}</h2>
        <span class="gs">${items.length} · ${fmt(tot)}</span>
        <button class="btn sm gbtn" data-group="${esc(name)}">mark all</button></div>
        <div class="grid">${body}</div>`;
    }).join("");
    rendered = view.length;
  } else {
    const slice = view.slice(0, PAGE);
    rendered = slice.length;
    main.innerHTML = `<div class="grid" id="grid">${slice.map(cardHTML).join("")}</div>` +
      (view.length > rendered ? `<div class="more"><button class="btn" id="more">
        Show ${Math.min(PAGE, view.length - rendered)} more of ${view.length - rendered}</button></div>` : "");
  }

  const tot = view.reduce((s, x) => s + x.size, 0);
  $("#shown").innerHTML = `showing <b>${view.length}</b> · <b>${fmt(tot)}</b>`;
  main.scrollTop = top;
  renderDisks(); renderMarks();
}

function showMore() {
  const grid = $("#grid"); if (!grid) return;
  const slice = view.slice(rendered, rendered + PAGE);
  grid.insertAdjacentHTML("beforeend", slice.map((it, n) => cardHTML(it, rendered + n)).join(""));
  rendered += slice.length;
  const box = document.querySelector(".more");
  if (rendered >= view.length) box?.remove();
  else box.innerHTML = `<button class="btn" id="more">Show ${Math.min(PAGE, view.length - rendered)} more of ${view.length - rendered}</button>`;
}

function renderDisks() {
  const gain = {};
  for (const it of marks.values()) gain[it.drive] = (gain[it.drive] || 0) + it.size;
  // Only show drives the visible library actually sits on. A Radarr drive is
  // noise while you are culling Media-Hoarder, because nothing you mark here
  // can ever change it.
  const inUse = new Set(pool().map(it => it.drive));
  $("#disks").innerHTML = Object.entries(DATA.disks)
    .filter(([lt]) => inUse.has(lt)).sort().map(([lt, d]) => {
    const crit = d.free < 50 * GB ? " crit" : "";
    const on = drives.has(lt) ? " on" : "";
    const pct = d.total ? Math.round(100 * (d.total - d.free) / d.total) : 0;
    const g = gain[lt] ? `<span class="gain">+${fmt(gain[lt])}</span>` : "";
    return `<div class="disk${crit}${on}" data-lt="${lt}" title="${pct}% used">
      <span class="lt">${lt}</span><span class="bar"><i style="width:${pct}%"></i></span>
      <span class="fr">${fmt(d.free)}</span>${g}</div>`;
  }).join("");
}

function renderMarks() {
  const n = marks.size, tot = [...marks.values()].reduce((s, x) => s + x.size, 0);
  $("#nmark").textContent = n;
  $("#gmark").textContent = fmt(tot);
  $("#commit").disabled = !n || CONF.read_only;
  const per = {};
  for (const it of marks.values()) per[it.drive] = (per[it.drive] || 0) + it.size;
  $("#perdrive").innerHTML = Object.entries(per).sort()
    .map(([d, v]) => `<b>${d}:</b> ${fmt(v)}`).join(" &nbsp; ");
  saveMarks();
}

function layout() {
  const h = $("#hdr").offsetHeight;
  $("#main").style.top = h + "px";
}
addEventListener("resize", layout);

/* ------------------------------------------------------------ interaction */

const findItem = k => {
  const [kind, id] = k.split(":");
  return (DATA[kind] || []).find(x => String(x.id) === id);
};

function toggle(k, el) {
  if (marks.has(k)) { marks.delete(k); el?.classList.remove("marked"); }
  else { marks.set(k, findItem(k)); el?.classList.add("marked"); }
  renderMarks(); renderDisks();
}

$("#main").addEventListener("click", e => {
  if (e.target.id === "more") return showMore();
  const g = e.target.closest("[data-group]");
  if (g) {
    const name = g.getAttribute("data-group");
    for (const it of view) if (groupKey(it) === name) marks.set(keyOf(it), it);
    return render(true);
  }
  const c = e.target.closest(".card");
  if (!c) return;
  cursor = +c.dataset.i;
  // shift-click looks for a smaller copy instead of marking for deletion
  if (e.shiftKey) { peek.style.display = "none"; return downsize(findItem(c.dataset.k)); }
  toggle(c.dataset.k, c);
});

/* right-click a poster for the same thing */
$("#main").addEventListener("contextmenu", e => {
  const c = e.target.closest(".card");
  if (!c) return;
  e.preventDefault();
  peek.style.display = "none";
  downsize(findItem(c.dataset.k));
});

/* hover synopsis — the whole point: no navigation to read a plot */
const peek = $("#peek");
let peekTimer = null;
$("#main").addEventListener("mouseover", e => {
  const c = e.target.closest(".card"); if (!c) return;
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => showPeek(findItem(c.dataset.k), c), 90);
});
$("#main").addEventListener("mouseout", e => {
  if (e.target.closest(".card")) { clearTimeout(peekTimer); peek.style.display = "none"; }
});
$("#main").addEventListener("scroll", () => { peek.style.display = "none"; }, { passive: true });

function showPeek(it, el) {
  if (!it) return;
  const tags = [];
  if (it.rating) tags.push(`★ ${it.rating}${it.votes ? ` (${it.votes > 999 ? (it.votes / 1000).toFixed(0) + "k" : it.votes})` : ""}`);
  if (it.cert) tags.push(it.cert);
  if (it.lang) tags.push(it.lang);
  (it.genres || []).slice(0, 4).forEach(g => tags.push(g));
  peek.innerHTML = `
    <h3>${esc(it.title)} <span class="yr">${it.year || ""}</span></h3>
    ${it.studio ? `<div class="studio">${esc(it.studio)}</div>` : ""}
    <div class="tags">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>
    <div class="ov">${esc(it.overview) || "<i>No synopsis available.</i>"}</div>
    <div class="kv">
      <span>size</span><span><b>${fmt(it.size)}</b>${it.bloat ? ` · ${it.bloat}× tier median` : ""}</span>
      <span>quality</span><span>${esc(it.quality)}${it.codec ? " · " + esc(it.codec) : ""}${it.hdr ? " · " + esc(it.hdr) : ""}</span>
      ${it.runtime ? `<span>runtime</span><span>${it.runtime} min${it.gph ? ` · ${it.gph} GB/hr` : ""}</span>` : ""}
      ${it.episodes ? `<span>episodes</span><span>${it.episodes} across ${it.seasons} seasons</span>` : ""}
      <span>drive</span><span>${esc(it.drive)} · ${it.monitored ? "monitored" : "unmonitored"}</span>
      <span>added</span><span>${esc(it.added) || "—"}</span>
    </div>
    <div class="pth">${esc(it.path)}</div>`;
  peek.style.display = "block";
  const r = el.getBoundingClientRect(), pr = peek.getBoundingClientRect();
  let left = r.right + 12;
  if (left + pr.width > innerWidth - 8) left = r.left - pr.width - 12;
  peek.style.left = Math.max(8, left) + "px";
  peek.style.top = Math.min(Math.max($("#hdr").offsetHeight + 4, r.top),
                            innerHeight - pr.height - 8) + "px";
}

$("#disks").addEventListener("click", e => {
  const d = e.target.closest(".disk"); if (!d) return;
  const lt = d.dataset.lt;
  drives.has(lt) ? drives.delete(lt) : drives.add(lt);
  render();
});

for (const id of ["#q", "#studio", "#genre", "#qual", "#y1", "#y2", "#mingb", "#maxgb",
                  "#sort", "#groupby", "#unmon", "#markedonly"])
  $(id).addEventListener("input", () => render());

for (const sel of ["#source", "#kind"])
  $(sel).addEventListener("change", () => { cursor = -1; fillFacets(); render(); });
$("#clear").addEventListener("click", resetFilters);
$("#unmarkall").addEventListener("click", () => { marks.clear(); render(true); });
$("#selall").addEventListener("click", () => { view.forEach(it => marks.set(keyOf(it), it)); render(true); });
$("#invert").addEventListener("click", () => {
  view.forEach(it => { const k = keyOf(it); marks.has(k) ? marks.delete(k) : marks.set(k, it); });
  render(true);
});
$("#refresh").addEventListener("click", async () => {
  $("#refresh").disabled = true;
  try {
    await fetch("/api/refresh", { method: "POST" });
    await load(true);
  } finally {
    $("#refresh").disabled = false;
  }
});
$("#cardsize").addEventListener("input", e => {
  document.documentElement.style.setProperty("--card", e.target.value + "px");
  LS.set("cardsize", +e.target.value);
});
$("#theme").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "light" ? "" : "light";
  next ? document.documentElement.setAttribute("data-theme", next)
       : document.documentElement.removeAttribute("data-theme");
  LS.set("theme", next);
});
$("#export").addEventListener("click", () => {
  const rows = view;
  const cols = ["title", "year", "drive", "size", "quality", "rating", "votes", "studio",
                "monitored", "added", "path"];
  const csv = [cols.join(",")].concat(rows.map(r =>
    cols.map(c => `"${String(r[c] ?? "").replace(/"/g, '""')}"`).join(","))).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `cullr-${bucket()}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
});

function resetFilters() {
  for (const id of ["#q", "#y1", "#y2", "#mingb", "#maxgb"]) $(id).value = "";
  for (const id of ["#studio", "#genre", "#qual", "#groupby"]) $(id).value = "";
  $("#unmon").checked = $("#markedonly").checked = false;
  drives.clear(); render();
}

/* ------------------------------------------------------------ presets */

const filterState = () => ({
  q: $("#q").value, studio: $("#studio").value, genre: $("#genre").value, qual: $("#qual").value,
  y1: $("#y1").value, y2: $("#y2").value, mingb: $("#mingb").value, maxgb: $("#maxgb").value,
  sort: $("#sort").value, groupby: $("#groupby").value,
  unmon: $("#unmon").checked, drives: [...drives],
  source: $("#source").value, kind: $("#kind").value,
});

function loadPresets() {
  const p = LS.get("presets", {});
  $("#preset").innerHTML = `<option value="">Presets…</option>` +
    Object.keys(p).map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
}
$("#savepreset").addEventListener("click", () => {
  const name = prompt("Save current filters as:");
  if (!name) return;
  const p = LS.get("presets", {}); p[name] = filterState(); LS.set("presets", p);
  loadPresets(); $("#preset").value = name;
});
$("#delpreset").addEventListener("click", () => {
  const n = $("#preset").value; if (!n) return;
  const p = LS.get("presets", {}); delete p[n]; LS.set("presets", p); loadPresets();
});
$("#preset").addEventListener("change", () => {
  const s = LS.get("presets", {})[$("#preset").value]; if (!s) return;
  $("#source").value = s.source || "arr";
  $("#kind").value = s.kind || "movie"; fillFacets();
  for (const [k, sel] of Object.entries({ q: "#q", studio: "#studio", genre: "#genre",
      qual: "#qual", y1: "#y1", y2: "#y2", mingb: "#mingb", maxgb: "#maxgb",
      sort: "#sort", groupby: "#groupby" }))
    if (s[k] !== undefined) $(sel).value = s[k];
  $("#unmon").checked = !!s.unmon;
  drives = new Set(s.drives || []);
  render();
});

/* ------------------------------------------------------------ keyboard */

const HELP = [
  ["/", "focus search"], ["Space", "mark / unmark the item under the cursor"],
  ["← → ↑ ↓", "move the cursor"], ["Enter", "open the delete confirmation"],
  ["a", "mark everything shown"], ["i", "invert marks"], ["c", "clear marks"],
  ["s", "find a smaller version of the item under the cursor"],
  ["r", "reload from Radarr/Sonarr"], ["g", "cycle grouping"], ["t", "toggle theme"],
  ["Esc", "close panel / blur field"], ["?", "this list"],
];

addEventListener("keydown", e => {
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName);
  if (e.key === "Escape") {
    if ($("#modal").classList.contains("on")) $("#modal").classList.remove("on");
    else if (typing) e.target.blur();
    peek.style.display = "none";
    return;
  }
  if (typing) return;
  if (e.key === "/") { e.preventDefault(); $("#q").focus(); return; }
  if (e.key === "?") { e.preventDefault(); showKeys(); return; }
  if (e.key === "a") { $("#selall").click(); return; }
  if (e.key === "i") { $("#invert").click(); return; }
  if (e.key === "c") { $("#unmarkall").click(); return; }
  if (e.key === "r") { $("#refresh").click(); return; }
  if (e.key === "t") { $("#theme").click(); return; }
  if (e.key === "s") {
    const el = document.querySelectorAll(".card")[cursor];
    if (el) { peek.style.display = "none"; downsize(findItem(el.dataset.k)); }
    return;
  }
  if (e.key === "g") {
    const s = $("#groupby"); s.selectedIndex = (s.selectedIndex + 1) % s.options.length;
    render(); return;
  }
  if (e.key === "Enter" && marks.size) { $("#commit").click(); return; }

  const cards = [...document.querySelectorAll(".card")];
  if (!cards.length) return;
  if (cursor >= cards.length) cursor = cards.length - 1;
  const perRow = Math.max(1, Math.floor($("#main").clientWidth /
    (parseInt(getComputedStyle(document.documentElement).getPropertyValue("--card")) + 10)));
  let next = cursor;
  if (e.key === "ArrowRight") next = Math.min(cards.length - 1, cursor + 1);
  else if (e.key === "ArrowLeft") next = Math.max(0, cursor - 1);
  else if (e.key === "ArrowDown") next = Math.min(cards.length - 1, cursor + perRow);
  else if (e.key === "ArrowUp") next = Math.max(0, cursor - perRow);
  else if (e.key === " ") {
    e.preventDefault();
    const el = cards[cursor]; if (el) toggle(el.dataset.k, el);
    return;
  } else return;

  e.preventDefault();
  cards[cursor]?.classList.remove("cursor");
  cursor = next;
  const el = cards[cursor];
  el.classList.add("cursor");
  el.scrollIntoView({ block: "nearest" });
  showPeek(findItem(el.dataset.k), el);
});

/* ------------------------------------------------------------ downsize
   Keep the title, drop the 60 GB copy. Radarr will not import a downgrade
   while the current profile says the existing file meets cutoff, so we move
   the movie onto a smaller-target profile first, then grab a chosen release. */

let PROFILES = [];

async function profiles() {
  if (PROFILES.length) return PROFILES;
  try {
    PROFILES = (await (await fetch("/api/profiles")).json()).profiles || [];
  } catch { PROFILES = []; }
  return PROFILES;
}

async function downsize(it) {
  if (it.kind !== "movie") return alert("Downsizing needs Radarr, so it is Radarr movies only.");
  const profs = await profiles();

  $("#mtitle").textContent = `Find a smaller ${it.title}`;
  $("#mbody").innerHTML = `
    <div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;margin-bottom:10px">
      <span>currently <b>${fmt(it.size)}</b> · ${esc(it.quality)}</span>
      <label class="chk">target profile
        <select id="dsprof">${profs.map(p =>
          `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
      <label class="chk">max GB <input type="number" id="dsmax" value="15" min="1" style="width:64px"></label>
      <label class="chk"><input type="checkbox" id="dsdel"> delete current file first</label>
    </div>
    <div class="note">Changing the profile is what lets Radarr accept a smaller file.
      Releases the current profile rejects are shown too — the reason is listed.</div>
    <div id="dsrel" class="empty">searching indexers…</div>`;
  $("#mfoot").innerHTML = `<button class="btn ghost" id="mcancel">Close</button>`;
  $("#mcancel").onclick = () => $("#modal").classList.remove("on");
  $("#modal").classList.add("on");

  // default to the largest profile that is still smaller than what we have
  const guess = profs.find(p => /1080/.test(p.name)) || profs[0];
  if (guess) $("#dsprof").value = guess.id;

  let releases = [];
  try {
    const r = await fetch(`/api/releases?id=${it.id}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    releases = d.releases || [];
  } catch (e) {
    $("#dsrel").innerHTML = `<div class="empty">Search failed: ${esc(e.message || e)}</div>`;
    return;
  }

  const paint = () => {
    const cap = (+$("#dsmax").value || 0) * GB;
    const rows = releases.filter(r => r.size > 0 && (!cap || r.size <= cap));
    if (!rows.length) {
      $("#dsrel").innerHTML = `<div class="empty">No releases under that size.
        ${releases.length} found overall — raise the cap.</div>`;
      return;
    }
    $("#dsrel").innerHTML = `
      <div style="color:var(--dim);margin-bottom:6px">${rows.length} of ${releases.length}
        releases under ${fmt(cap)} — smallest first</div>
      <div class="dlist" style="max-height:42vh">
        ${rows.map((r, i) => {
          const save = it.size - r.size;
          const bad = r.rejections.length && !r.approved;
          return `<div>
            <span class="n" title="${esc(r.title)}">
              ${esc(r.title.slice(0, 70))}
              <span style="color:var(--faint)">${esc(r.indexer)}${r.age ? " · " + r.age + "d" : ""}</span>
              ${bad ? `<span style="color:var(--warn)"> · ${esc(r.rejections[0].slice(0, 44))}</span>` : ""}
            </span>
            <span class="s">${esc(r.quality)}</span>
            <span class="s"><b>${fmt(r.size)}</b></span>
            <span class="s" style="color:var(--keep)">${save > 0 ? "−" + fmt(save) : ""}</span>
            <button class="btn sm ${r.downloadAllowed ? "" : "ghost"}" data-i="${i}"
              ${r.downloadAllowed ? "" : "disabled title='Radarr will not allow this grab'"}>grab</button>
          </div>`;
        }).join("")}
      </div>`;
    $("#dsrel").querySelectorAll("button[data-i]").forEach(b => {
      b.onclick = () => doGrab(it, rows[+b.dataset.i], b);
    });
  };

  $("#dsmax").oninput = paint;
  paint();
}

async function doGrab(it, rel, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  const body = {
    id: it.id, profileId: +$("#dsprof").value,
    guid: rel.guid, indexerId: rel.indexerId,
    size: rel.size, release: rel.title,
    deleteFirst: $("#dsdel").checked,
  };
  try {
    const r = await fetch("/api/downsize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || r.status);
    btn.textContent = d.dryRun ? "rehearsed" : "sent ✓";
    btn.style.color = "var(--keep)";
    const saved = it.size - rel.size;
    $("#mtitle").textContent =
      `${it.title} — queued${saved > 0 ? `, ${fmt(saved)} once it imports` : ""}`;
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "retry";
    btn.style.color = "var(--mark)";
    $("#mtitle").textContent = `Grab failed: ${e.message || e}`;
  }
}

function showKeys() {
  $("#mtitle").textContent = "Keyboard shortcuts";
  $("#mbody").innerHTML = `<div class="keys">` +
    HELP.map(([k, d]) => `<kbd>${esc(k)}</kbd><span>${esc(d)}</span>`).join("") + `</div>`;
  $("#mfoot").innerHTML = `<button class="btn" onclick="document.querySelector('#modal').classList.remove('on')">Close</button>`;
  $("#modal").classList.add("on");
}
$("#keys").addEventListener("click", showKeys);

/* ------------------------------------------------------------ delete */

$("#commit").addEventListener("click", () => {
  if (CONF.read_only || !marks.size) return;
  const items = [...marks.values()].sort((a, b) => b.size - a.size);
  const tot = items.reduce((s, x) => s + x.size, 0);
  const per = {};
  for (const it of items) per[it.drive] = (per[it.drive] || 0) + it.size;
  const excl = $("#exclude").checked;

  // Media-Hoarder items have no *arr behind them: cullr removes those files
  // from disk itself, so they are called out separately here.
  const mh = items.filter(x => x.kind === "mh" || x.kind === "mhseries");
  const mhFiles = mh.reduce((s, x) => s + (x.episodes || 1), 0);

  $("#mtitle").textContent = "Confirm deletion";
  $("#mbody").innerHTML = `
    ${CONF.dry_run ? `<div class="note"><b>Dry-run mode.</b> Nothing will actually be deleted —
      the server logs the request and returns success so you can rehearse a sweep.</div>` : ""}
    ${mh.length ? `<div class="warn"><b>${mh.length} of these are Media-Hoarder items
      (${mhFiles} file${mhFiles > 1 ? "s" : ""}).</b><br>
      Media-Hoarder has no API, so cullr deletes those files off disk directly. There is no
      *arr bookkeeping behind them and no recycle bin on a network share. Media-Hoarder will
      keep listing them until you rescan.
      ${CONF.mh_allow_delete ? "" : "<br><b>The server was started without --mh-allow-delete, "
        + "so it will refuse them.</b>"}</div>` : ""}
    <div class="warn"><b>This permanently deletes ${items.length} item${items.length > 1 ? "s" : ""}
      and their files from disk.</b><br>
      Reclaims <b>${fmt(tot)}</b> — ${Object.entries(per).sort().map(([d, v]) => `${d}: ${fmt(v)}`).join(", ")}.<br>
      ${excl ? "They will also be excluded from future imports so nothing re-grabs them."
             : "They stay eligible for re-download if something still monitors them."}
      <br>This cannot be undone from here.</div>
    <div class="dlist">${items.map(it =>
      `<div><span class="n">${esc(it.title)} <span style="color:var(--faint)">${it.year || ""}</span></span>
       <span class="s">${esc(it.drive)} · ${fmt(it.size)}</span></div>`).join("")}</div>`;
  $("#mfoot").innerHTML = `
    <button class="btn ghost" id="mcancel">Cancel</button>
    <button class="btn danger" id="mgo">${CONF.dry_run ? "Rehearse" : "Delete"} ${items.length} · ${fmt(tot)}</button>`;
  $("#modal").classList.add("on");
  $("#mcancel").onclick = () => $("#modal").classList.remove("on");
  $("#mgo").onclick = () => doDelete(items, excl);
});

async function doDelete(items, exclude) {
  $("#mtitle").textContent = CONF.dry_run ? "Rehearsing…" : "Deleting…";
  $("#mbody").innerHTML = `<div id="log"></div>`;
  $("#mfoot").innerHTML = "";
  const log = $("#log");
  const say = s => { log.textContent += s; log.scrollTop = log.scrollHeight; };

  let ok = 0, fail = 0, freed = 0;
  const CH = 20;
  for (let i = 0; i < items.length; i += CH) {
    const chunk = items.slice(i, i + CH);
    say(`${i + 1}–${Math.min(i + CH, items.length)} of ${items.length}…\n`);
    let d;
    try {
      const r = await fetch("/api/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: chunk.map(x => ({ id: x.id, kind: x.kind, title: x.title, size: x.size, path: x.path })),
          exclude,
        }),
      });
      d = await r.json();
      if (!r.ok) { say(`  server refused: ${d.error || r.status}\n`); fail += chunk.length; continue; }
    } catch (e) { say(`  request failed: ${e}\n`); fail += chunk.length; continue; }

    d.results.forEach((res, j) => {
      const it = chunk[j];
      if (res.ok) { ok++; freed += it.size; marks.delete(keyOf(it)); }
      else { fail++; say(`  FAILED ${it.title}: ${res.error || res.code}\n`); }
    });
  }
  say(`\n${ok} ${CONF.dry_run ? "would be deleted" : "deleted"}, ${fail} failed, ${fmt(freed)} reclaimed.\n`);
  if (CONF.audit && !CONF.dry_run) say(`logged to cullr-deletions.jsonl\n`);
  say(`refreshing library…\n`);
  $("#mfoot").innerHTML = `<button class="btn" id="mclose">Close</button>`;
  $("#mclose").onclick = () => $("#modal").classList.remove("on");
  await load(true);
  say("done.\n");
}

boot();

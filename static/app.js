const GROUPS = [
  ["active", "Downloading"],
  ["queued", "Queued"],
  ["paused", "Paused"],
  ["failed", "Failed"],
  ["done", "Finished"],
];

const ICONS = {
  pause: '<path d="M8.5 5.5v9M15.5 5.5v9"/>',
  resume: '<path d="M8 5.5l8 4.5-8 4.5z"/>',
  cancel: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  delete: '<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/>',
};

const STATE_ICON = {
  active: '<circle cx="12" cy="12" r="9"/><path d="M12 8v8m0 0 3.5-3.5M12 16l-3.5-3.5"/>',
  queued: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
  paused: '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>',
  done: '<circle cx="12" cy="12" r="9"/><path d="M8.2 12.4l2.6 2.6 5-5.2"/>',
  failed: '<circle cx="12" cy="12" r="9"/><path d="M12 7.8v5.2"/><path d="M12 16.2v.2"/>',
  idle: '<circle cx="12" cy="12" r="9"/>',
};

const STATE_WORD = {
  active: "Downloading",
  queued: "Queued",
  paused: "Paused",
  done: "Complete",
  failed: "Failed",
  idle: "Idle",
};

const LABEL = {
  pause: "Pause",
  resume: "Resume",
  cancel: "Stop",
  delete: "Delete",
};

const HINT = "Magnets, Torrents and Direct Links Supported";

const $ = (id) => document.getElementById(id);
const groupsEl = $("groups");
const emptyEl = $("empty");
const hintEl = $("hint");
const composerEl = document.querySelector(".composer");
const rows = new Map();
const groupEls = new Map();
let hintTimer = null;


function bytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

const speed = (n) => `${bytes(n)}/s`;

function etaFrom(seconds) {
  const s = Math.round(seconds);
  if (!isFinite(s) || s <= 0) return "";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function eta(item) {
  const left = item.total - item.done;
  if (item.bucket !== "active" || !item.down || left <= 0) return "";
  const s = Math.round(left / item.down);
  const narrow = matchMedia("(max-width: 640px)").matches;
  if (narrow) {
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.round(s / 60)}m`;
    return `${Math.round(s / 3600)}h`;
  }
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function metaText(item) {
  if (item.bucket === "done") return `${bytes(item.total)} · Complete`;
  if (item.bucket === "failed") return item.error || `${bytes(item.done)} / ${bytes(item.total)} · Stopped`;
  const parts = [`${bytes(item.done)} / ${bytes(item.total)}`];
  if (item.isTorrent) parts.push(`${item.seeds} Seeds · ${item.peers} Peers`);
  if (item.files > 1) parts.push(`${item.files} Files`);
  const t = eta(item);
  if (t) parts.push(`${t} Left`);
  return parts.join(" · ");
}

function actionsFor(item) {
  if (item.bucket === "active" || item.bucket === "queued") return ["pause", "cancel"];
  if (item.bucket === "paused") return ["resume", "cancel"];
  return ["delete"];
}

function stateIcon(bucket, word) {
  return {
    html: `<span class="state" data-state="${bucket}" title="${word}"><svg viewBox="0 0 24 24" aria-hidden="true">${STATE_ICON[bucket] || STATE_ICON.idle}</svg></span>`,
  };
}

function buildRow(item) {
  const row = document.createElement("article");
  row.className = "row";
  row.dataset.gid = item.gid;
  row.dataset.bucket = item.bucket;
  row.innerHTML = `
    <span class="state" data-state="${item.bucket}" title="${STATE_WORD[item.bucket] || "Idle"}">
      <svg viewBox="0 0 24 24" aria-hidden="true">${STATE_ICON[item.bucket] || STATE_ICON.idle}</svg>
    </span>
    <div class="body">
      <div class="line1"><span class="name"></span><span class="pct mono"></span></div>
      <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i></i></div>
      <div class="line2"><span class="meta"></span><span class="src"></span><span class="rate mono"></span></div>
    </div>
    <div class="acts"></div>`;
  return row;
}

function syncActions(row, item) {
  const acts = row.querySelector(".acts");
  const wanted = actionsFor(item).join("|");
  if (acts.dataset.kind !== wanted) {
    acts.dataset.kind = wanted;
    acts.replaceChildren();
    for (const kind of actionsFor(item)) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "icon-btn";
      b.dataset.kind = kind;
      b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[kind]}</svg><span class="label">${LABEL[kind]}</span>`;
      acts.append(b);
    }
  }
  for (const b of acts.children) {
    b.setAttribute("aria-label", `${LABEL[b.dataset.kind]} ${item.name}`);
    b.dataset.gid = item.gid;
  }
}

function patch(row, item, showSource) {
  row.dataset.bucket = item.bucket;
  row.querySelector(".name").textContent = item.name;
  row.querySelector(".name").title = item.name;

  const stateEl = row.querySelector(".state");
  if (stateEl.dataset.state !== item.bucket) {
    stateEl.dataset.state = item.bucket;
    stateEl.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${STATE_ICON[item.bucket] || STATE_ICON.idle}</svg>`;
  }
  stateEl.title = STATE_WORD[item.bucket] || "Idle";

  const pct = row.querySelector(".pct");
  pct.textContent = item.total ? `${item.percent.toFixed(1)}%` : "";
  pct.hidden = item.bucket === "done" || item.bucket === "failed";

  const bar = row.querySelector(".bar");
  bar.setAttribute("aria-valuenow", String(Math.round(item.percent)));
  bar.setAttribute("aria-label", `${item.name} progress`);
  row.querySelector(".bar i").style.transform = `scaleX(${item.total ? item.percent / 100 : 0})`;
  row.querySelector(".meta").textContent = metaText(item);
  const src = row.querySelector(".src");
  src.textContent = showSource ? item.source || "" : "";
  src.hidden = !src.textContent;
  row.querySelector(".rate").textContent = item.bucket === "active" ? speed(item.down) : "";
  syncActions(row, item);
}

function groupShell(key, title) {
  let el = groupEls.get(key);
  if (!el) {
    el = document.createElement("section");
    el.className = "group";
    el.dataset.group = key;
    el.innerHTML = `<div class="group-head"><span class="group-title">${title}</span><span class="group-count mono"></span></div>`;
    groupEls.set(key, el);
    groupsEl.append(el);
  }
  return el;
}

function render(state) {
  const items = state.items || [];
  // the qbt block exists whenever the bridge is configured, even with no torrents
  const showSource = Boolean(state.qbt);
  const byGroup = new Map(GROUPS.map(([k]) => [k, []]));
  for (const it of items) {
    const list = byGroup.get(it.bucket);
    if (list) list.push(it);
  }

  const present = new Set(items.map((i) => i.gid));
  for (const [gid, row] of rows) {
    if (!present.has(gid)) { row.remove(); rows.delete(gid); }
  }

  for (const [key, title] of GROUPS) {
    const list = byGroup.get(key);
    const group = groupShell(key, title);
    group.hidden = list.length === 0;
    if (!list.length) continue;
    group.querySelector(".group-count").textContent = String(list.length).padStart(2, "0");
    for (const item of list) {
      let row = rows.get(item.gid);
      if (!row) { row = buildRow(item); rows.set(item.gid, row); }
      patch(row, item, showSource);
      if (row.parentElement !== group) group.append(row);
    }
  }

  const s = state.summary || {};
  emptyEl.hidden = items.length > 0;

  // the live cluster only exists while bytes are moving
  const running = s.active > 0;
  const anyQueue = (s.active || 0) + (s.queued || 0) > 0;
  document.querySelector(".top-actions").hidden = !anyQueue;
  const live = $("live");
  live.hidden = !running;
  if (running) {
    const remaining = items
      .filter((i) => i.bucket === "active")
      .reduce((sum, i) => sum + Math.max(i.total - i.done, 0), 0);
    const rate = state.speed || 0;
    $("total-speed").textContent = speed(rate);
    $("total-left").textContent = rate > 0 ? `${etaFrom(remaining / rate)} Left` : "";
    $("live-dot").dataset.state = "active";
  }
}

function say(html, tone) {
  hintEl.innerHTML = html;
  hintEl.dataset.tone = tone || "";
  clearTimeout(hintTimer);
  if (tone) {
    hintTimer = setTimeout(() => {
      hintEl.innerHTML = HINT;
      hintEl.dataset.tone = "";
    }, 5000);
  }
}

async function act(gid, action) {
  try {
    const r = await fetch("/api/action", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ gid, action }),
    });
    if (!r.ok) say((await r.json()).detail || "Action Failed", "error");
  } catch {
    say("Cannot Reach the Server", "error");
  }
}

groupsEl.addEventListener("click", (event) => {
  const btn = event.target.closest(".icon-btn");
  if (!btn) return;
  const kind = btn.dataset.kind;
  const row = btn.closest(".row");
  const name = row ? row.querySelector(".name").textContent : "this download";
  if (kind === "delete" && !confirm(`Delete "${name}"?\n\nThe file is removed from the shared folder. This cannot be undone.`)) return;
  btn.disabled = true;
  act(btn.dataset.gid, kind).finally(() => { btn.disabled = false; });
});

$("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("url");
  const url = input.value.trim();
  if (!url) return;
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    const r = await fetch("/api/add", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      say(body.detail || "aria2 Rejected That Link", "error");
      return;
    }
    input.value = "";
    say("Added to the Queue", "ok");
  } catch {
    say("Cannot Reach the Server", "error");
  } finally {
    button.disabled = false;
    input.focus();
  }
});

for (const [id, verb] of [["pause-all", "pause"], ["resume-all", "resume"]]) {
  $(id).addEventListener("click", async () => {
    await fetch(`/api/all/${verb}`, { method: "POST" }).catch(() => {});
  });
}

let ticking = false;
addEventListener("scroll", () => {
  if (ticking || !composerEl) return;
  ticking = true;
  requestAnimationFrame(() => {
    composerEl.dataset.stuck = String(composerEl.getBoundingClientRect().top < 70);
    ticking = false;
  });
}, { passive: true });

const source = new EventSource("/api/events");
source.onmessage = (event) => {
  try { render(JSON.parse(event.data)); } catch {}
};
source.onerror = () => say("Reconnecting to the Pi", "error");

const input = $("url");
const narrowQuery = matchMedia("(max-width: 640px)");
function syncPlaceholder() {
  input.placeholder = narrowQuery.matches ? "Magnet or Direct Link" : "Paste a Magnet or a Direct Link";
}
syncPlaceholder();
narrowQuery.addEventListener("change", syncPlaceholder);

fetch("/api/state").then((r) => r.json()).then(render).catch(() => {});

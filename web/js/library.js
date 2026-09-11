import { app } from "../../../scripts/app.js";

/**
 * The media library.
 *
 * A rail of categories on the left, the contents on the right. Everything here
 * exists so a clip is one click from the timeline.
 *
 * Ported from the Electron app it replaces, with the two things a browser
 * cannot do handled differently: files arrive by upload rather than a native
 * picker, and media is served by the pack's own routes rather than a custom
 * protocol. The layout is deliberately the same — this is the same tool.
 */

const API = "/secondunit/library";

let styled = false;
let overlay = null;
let audio = null;
let playingId = null;

const state = {
  tree: null,
  category: null,
  group: null,
  query: "",
  items: [],
  ytdlp: false,
};

/* ------------------------------------------------------------------ *
 * Chrome
 * ------------------------------------------------------------------ */

const ICONS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  download: '<path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 12h18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 18 5-5 4 4 3-3 4 4"/>',
  audio: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  play: '<path d="M7 4.5v15l13-7.5z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  trash: '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>',
  pencil: '<path d="M4 20h4l10-10a2.83 2.83 0 1 0-4-4L4 16v4z"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  graph: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="10" r="2.5"/><circle cx="9" cy="18" r="2.5"/><path d="M8.2 7.1 15.8 9M7.4 8.2 8.3 15.6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  spinner: '<path d="M12 3a9 9 0 1 0 9 9"/>',
};

function icon(name, size = 15) {
  return (
    `<svg class="su-i" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" ` +
    `stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${
      ICONS[name] || ""
    }</svg>`
  );
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
}

function ensureStyle() {
  if (styled) return;
  styled = true;
  const style = document.createElement("style");
  style.textContent = `
.su-lib{position:fixed;inset:0;z-index:1200;display:flex;align-items:center;justify-content:center;padding:22px;
  background:rgba(4,4,8,.62);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --su-accent:#3ddc97;--su-accent-soft:rgba(61,220,151,.13);--su-accent-line:rgba(61,220,151,.4);
  --su-bg:#0b0a10;--su-panel:rgba(255,255,255,.035);--su-line:rgba(255,255,255,.08);
  --su-text:#ece9f5;--su-dim:#a09bb5;--su-faint:#6b6683;--su-danger:#f87171;}
.su-lib *{box-sizing:border-box;}
.su-dialog{width:min(1440px,97vw);height:min(940px,94vh);display:flex;flex-direction:column;overflow:hidden;
  background:var(--su-bg);border:1px solid var(--su-line);border-radius:16px;
  box-shadow:0 30px 90px rgba(0,0,0,.65);color:var(--su-text);}

.su-head{display:flex;align-items:flex-start;gap:16px;padding:22px 26px 16px;}
.su-title{margin:0;font-size:26px;font-weight:700;letter-spacing:-.02em;}
.su-sub{margin:4px 0 0;font-size:13px;color:var(--su-dim);}
.su-head-actions{margin-left:auto;display:flex;align-items:center;gap:8px;}

.su-search{position:relative;}
.su-search .su-i{position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--su-faint);pointer-events:none;}
.su-input{height:36px;padding:0 12px 0 32px;width:230px;border-radius:9px;border:1px solid var(--su-line);
  background:var(--su-panel);color:var(--su-text);font-size:13px;outline:none;}
.su-input:focus{border-color:var(--su-accent-line);}

.su-btn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 13px;border-radius:9px;
  border:1px solid var(--su-line);background:var(--su-panel);color:var(--su-text);font-size:12.5px;font-weight:520;
  cursor:pointer;transition:background .12s,border-color .12s,color .12s;white-space:nowrap;}
.su-btn:hover{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.16);}
.su-btn:disabled{opacity:.5;cursor:default;}
.su-btn--sm{height:30px;padding:0 10px;font-size:12px;border-radius:8px;}
.su-btn--icon{width:30px;padding:0;justify-content:center;color:var(--su-dim);}
.su-btn--icon:hover{color:var(--su-text);}
.su-btn--danger:hover{color:var(--su-danger);border-color:rgba(248,113,113,.4);}
.su-btn--armed{color:var(--su-danger);border-color:var(--su-danger);}

.su-body{flex:1;display:flex;min-height:0;gap:18px;padding:0 26px 8px;}

.su-rail{width:196px;flex:0 0 auto;display:flex;flex-direction:column;gap:2px;overflow-y:auto;padding-bottom:14px;}
.su-rail-label{margin:14px 0 5px;font-size:10.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--su-faint);}
.su-rail-label:first-child{margin-top:0;}
.su-cat{display:flex;align-items:center;gap:8px;padding:8px 10px;border:0;border-radius:9px;color:var(--su-dim);
  background:transparent;text-align:left;cursor:pointer;font-size:13px;font-weight:520;font-family:inherit;}
.su-cat:hover{background:rgba(255,255,255,.05);color:var(--su-text);}
.su-cat.is-active{color:var(--su-accent);background:var(--su-accent-soft);}
.su-cat-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.su-cat-count{font-size:11px;color:var(--su-faint);font-variant-numeric:tabular-nums;}
.su-cat-del{display:grid;place-items:center;width:16px;height:16px;border-radius:4px;opacity:0;color:var(--su-faint);}
.su-cat:hover .su-cat-del{opacity:.55;}
.su-cat-del:hover{opacity:1;color:var(--su-danger);}
.su-cat-del.is-armed{opacity:1;color:var(--su-danger);}
.su-new-cat{display:flex;align-items:center;gap:7px;margin-top:10px;padding:8px 10px;border:1px dashed var(--su-line);
  border-radius:9px;font-size:12.5px;color:var(--su-faint);background:transparent;cursor:pointer;font-family:inherit;}
.su-new-cat:hover{color:var(--su-accent);border-color:var(--su-accent-line);}

.su-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:12px;overflow:hidden;}

.su-chips{display:flex;flex-wrap:wrap;gap:7px;}
.su-chip{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 12px;border-radius:999px;
  border:1px solid var(--su-line);background:transparent;color:var(--su-dim);font-size:12.5px;cursor:pointer;font-family:inherit;}
.su-chip:hover{color:var(--su-text);border-color:rgba(255,255,255,.18);}
.su-chip.is-active{background:var(--su-accent);border-color:var(--su-accent);color:#08130d;font-weight:600;}
.su-chip-count{font-size:11px;opacity:.7;font-variant-numeric:tabular-nums;}
.su-chip-del{opacity:.55;display:grid;place-items:center;}
.su-chip-del:hover{opacity:1;color:var(--su-danger);}
.su-chip-del.is-armed{opacity:1;color:var(--su-danger);}

.su-drop{display:flex;align-items:center;justify-content:center;gap:9px;padding:13px;border-radius:11px;
  border:1px dashed var(--su-line);color:var(--su-faint);font-size:12.5px;cursor:pointer;transition:all .12s;}
.su-drop:hover,.su-drop.is-over{border-color:var(--su-accent-line);color:var(--su-accent);background:var(--su-accent-soft);}

.su-items{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px;padding-right:4px;padding-bottom:8px;}

.su-item{display:flex;align-items:center;gap:14px;padding:10px 12px;border-radius:12px;
  border:1px solid var(--su-line);background:var(--su-panel);}
.su-item:hover{border-color:rgba(255,255,255,.14);}
.su-preview{position:relative;width:76px;height:44px;flex:0 0 auto;border-radius:7px;overflow:hidden;
  background:rgba(0,0,0,.45);display:grid;place-items:center;color:var(--su-faint);}
.su-preview img{width:100%;height:100%;object-fit:cover;display:block;}
.su-play{position:absolute;inset:0;display:grid;place-items:center;border:0;background:rgba(0,0,0,.35);
  color:#fff;cursor:pointer;opacity:0;transition:opacity .12s;}
.su-item:hover .su-play{opacity:1;}
.su-item-body{flex:1;min-width:0;}
.su-item-name{font-size:13.5px;font-weight:560;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.su-item-meta{margin-top:3px;font-size:11.5px;color:var(--su-faint);display:flex;gap:6px;flex-wrap:wrap;}
.su-item-actions{display:flex;align-items:center;gap:6px;flex:0 0 auto;}
.su-select{height:30px;border-radius:8px;border:1px solid var(--su-line);background:var(--su-panel);
  color:var(--su-dim);font-size:12px;padding:0 6px;font-family:inherit;cursor:pointer;max-width:120px;}

.su-player{display:flex;align-items:center;gap:9px;margin-top:7px;}
.su-player input[type=range]{flex:1;accent-color:var(--su-accent);height:3px;}
.su-time{font-size:11px;color:var(--su-faint);font-variant-numeric:tabular-nums;min-width:78px;text-align:right;}

.su-empty{display:grid;place-items:center;gap:8px;padding:56px 0;color:var(--su-faint);text-align:center;}
.su-empty-title{font-size:14px;color:var(--su-dim);font-weight:560;}

.su-foot{display:flex;align-items:center;gap:10px;padding:10px 26px 16px;border-top:1px solid var(--su-line);
  font-size:11.5px;color:var(--su-faint);}
.su-foot code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--su-dim);}
.su-foot-actions{margin-left:auto;display:flex;gap:8px;}

.su-toasts{position:fixed;right:26px;bottom:26px;z-index:1300;display:flex;flex-direction:column;gap:8px;align-items:flex-end;}
.su-toast{padding:9px 14px;border-radius:9px;font-size:12.5px;max-width:420px;
  background:#15141d;border:1px solid var(--su-line);color:var(--su-text);box-shadow:0 10px 30px rgba(0,0,0,.5);}
.su-toast.is-ok{border-color:var(--su-accent-line);color:var(--su-accent);}
.su-toast.is-bad{border-color:rgba(248,113,113,.45);color:var(--su-danger);}

.su-viewer{position:fixed;inset:0;z-index:1400;display:grid;place-items:center;background:rgba(0,0,0,.85);padding:40px;}
.su-viewer img,.su-viewer video{max-width:92vw;max-height:82vh;border-radius:10px;display:block;}
.su-viewer-close{position:absolute;top:22px;right:26px;width:36px;height:36px;border-radius:10px;display:grid;
  place-items:center;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#fff;cursor:pointer;}
.su-viewer-name{margin-top:12px;text-align:center;color:#cfcbdd;font-size:13px;}

@keyframes su-spin{to{transform:rotate(360deg);}}
.su-spin{animation:su-spin .8s linear infinite;}
`;
  document.head.appendChild(style);
}

/* ------------------------------------------------------------------ *
 * Talking to the pack
 * ------------------------------------------------------------------ */

async function get(path) {
  const response = await fetch(API + path);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function post(path, body) {
  const response = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function fileUrl(rel) {
  return API + "/file?path=" + encodeURIComponent(rel);
}

function thumbUrl(rel) {
  return API + "/thumb?path=" + encodeURIComponent(rel);
}

/* ------------------------------------------------------------------ *
 * Toasts
 * ------------------------------------------------------------------ */

function toast(message, tone = "") {
  let host = document.querySelector(".su-toasts");
  if (!host) {
    host = document.createElement("div");
    host.className = "su-toasts";
    document.body.appendChild(host);
  }
  const node = document.createElement("div");
  node.className = "su-toast" + (tone ? " is-" + tone : "");
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), tone === "bad" ? 6000 : 3200);
}

const ok = (m) => toast(m, "ok");
const bad = (m) => toast(m, "bad");

/** Two-step confirmation on the button itself, rather than a modal. */
function armed(button, question) {
  if (button.dataset.armed === "1") return true;
  button.dataset.armed = "1";
  button.classList.add("is-armed", "su-btn--armed");
  toast(question + " Click again to confirm.");
  setTimeout(() => {
    if (!button.isConnected) return;
    button.dataset.armed = "";
    button.classList.remove("is-armed", "su-btn--armed");
  }, 4000);
  return false;
}

/** Show a button as working, and put it back whatever happens. */
async function busy(button, label, work) {
  const before = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="su-spin" style="display:inline-flex">${icon("spinner", 14)}</span>${
    label ? " " + esc(label) : ""
  }`;
  try {
    return await work();
  } finally {
    button.disabled = false;
    button.innerHTML = before;
  }
}

/* ------------------------------------------------------------------ *
 * Opening and closing
 * ------------------------------------------------------------------ */

export async function open() {
  if (overlay) return;
  ensureStyle();

  overlay = document.createElement("div");
  overlay.className = "su-lib";
  overlay.innerHTML = `<div class="su-dialog"><div class="su-empty">${icon(
    "folder",
    24
  )}<div class="su-empty-title">Opening your library…</div></div></div>`;
  document.body.appendChild(overlay);

  overlay.addEventListener("mousedown", (event) => {
    if (event.target === overlay) close();
  });
  // ComfyUI listens for single-key shortcuts on the document. Without this,
  // typing a clip name into the search box fires them.
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
    event.stopPropagation();
  });
  document.addEventListener("keydown", onEscape, true);

  await load();
}

function onEscape(event) {
  if (event.key === "Escape" && overlay && !document.querySelector(".su-viewer")) close();
}

export function close() {
  stopAudio();
  document.removeEventListener("keydown", onEscape, true);
  overlay?.remove();
  overlay = null;
}

async function load() {
  try {
    const tree = await get("");
    state.tree = tree;
    state.ytdlp = !!tree.ytdlp;
    if (!state.category || !tree.categories.some((c) => c.name === state.category)) {
      state.category = tree.categories[0]?.name || null;
      state.group = null;
    }
    render();
    await loadItems();
  } catch (err) {
    if (overlay) {
      overlay.querySelector(".su-dialog").innerHTML = `<div class="su-empty">${icon("folder", 24)}
        <div class="su-empty-title">The library would not open</div>
        <div>${esc(err.message)}</div></div>`;
    }
  }
}

async function loadItems() {
  const host = overlay?.querySelector(".su-items");
  if (!host) return;
  host.innerHTML = `<div class="su-empty">${icon("spinner", 20)}</div>`;

  try {
    const params = state.query
      ? "?q=" + encodeURIComponent(state.query)
      : "?category=" + encodeURIComponent(state.category || "") + "&group=" + encodeURIComponent(state.group || "");
    const data = await get("/items" + params);
    state.items = data.items || [];
  } catch (err) {
    state.items = [];
    bad(err.message);
  }
  renderItems();
}

/* ------------------------------------------------------------------ *
 * Drawing
 * ------------------------------------------------------------------ */

function render() {
  const { categories, root } = state.tree;
  const video = categories.filter((c) => c.type === "video");
  const sound = categories.filter((c) => c.type === "audio");

  overlay.querySelector(".su-dialog").innerHTML = `
    <div class="su-head">
      <div>
        <h1 class="su-title">Media library</h1>
        <p class="su-sub">Clips, music and voice takes, ready to drop into your edit.</p>
      </div>
      <div class="su-head-actions">
        <label class="su-search">${icon("search")}
          <input class="su-input" data-search placeholder="Search everything…" value="${esc(state.query)}" />
        </label>
        <button class="su-btn" data-add>${icon("plus")}Add files</button>
        <button class="su-btn" data-link>${icon("download")}From a link</button>
        <button class="su-btn su-btn--icon" data-close title="Close">${icon("close")}</button>
      </div>
    </div>

    <div class="su-body">
      <aside class="su-rail">
        ${railGroup("Video", video)}
        ${railGroup("Sound", sound)}
        <button class="su-new-cat" data-new-cat>${icon("plus")}New category</button>
      </aside>
      <section class="su-main">
        <div class="su-chips" data-chips></div>
        <div class="su-drop" data-drop>${icon("download")}<span data-drop-label></span></div>
        <div class="su-items"></div>
      </section>
    </div>

    <div class="su-foot">
      ${icon("folder", 13)}<code>${esc(root)}</code>
      <div class="su-foot-actions">
        <button class="su-btn su-btn--sm" data-change-root>Change folder</button>
      </div>
    </div>

    <input type="file" multiple hidden data-file-input />
  `;

  wireChrome();
  renderChips();
}

function railGroup(label, categories) {
  if (!categories.length) return "";
  return (
    `<div class="su-rail-label">${esc(label)}</div>` +
    categories
      .map(
        (category) => `
      <button class="su-cat${category.name === state.category ? " is-active" : ""}" data-cat="${esc(category.name)}">
        <span class="su-cat-name">${esc(category.name)}</span>
        <span class="su-cat-count">${category.count}</span>
        <span class="su-cat-del" data-del-cat="${esc(category.name)}" title="Delete this category">${icon(
          "close",
          11
        )}</span>
      </button>`
      )
      .join("")
  );
}

function renderChips() {
  const host = overlay.querySelector("[data-chips]");
  const category = state.tree.categories.find((c) => c.name === state.category);

  if (state.query) {
    host.innerHTML = `
      <button class="su-chip is-active">Results for “${esc(state.query)}”</button>
      <button class="su-chip" data-clear>Clear</button>`;
    host.querySelector("[data-clear]").addEventListener("click", () => {
      state.query = "";
      overlay.querySelector("[data-search]").value = "";
      renderChips();
      loadItems();
    });
    overlay.querySelector("[data-drop]").style.display = "none";
    return;
  }

  overlay.querySelector("[data-drop]").style.display = "";
  overlay.querySelector("[data-drop-label]").textContent = category
    ? "Drop files here to add them to " + (state.group || category.name)
    : "Pick a category first";

  if (!category) {
    host.innerHTML = "";
    return;
  }

  host.innerHTML =
    `<button class="su-chip${!state.group ? " is-active" : ""}" data-group="">All in ${esc(
      category.name
    )}</button>` +
    category.groups
      .map(
        (group) => `
      <button class="su-chip${group.name === state.group ? " is-active" : ""}" data-group="${esc(group.name)}">
        ${esc(group.name)}<span class="su-chip-count">${group.count}</span>
        <span class="su-chip-del" data-del-group="${esc(group.name)}" title="Delete">${icon("close", 11)}</span>
      </button>`
      )
      .join("") +
    `<button class="su-chip" data-new-group>${icon("plus", 12)}Group</button>`;

  for (const chip of host.querySelectorAll("[data-group]")) {
    chip.addEventListener("click", (event) => {
      if (event.target.closest("[data-del-group]")) return;
      state.group = chip.dataset.group || null;
      stopAudio();
      renderChips();
      loadItems();
    });
  }

  for (const button of host.querySelectorAll("[data-del-group]")) {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!armed(button, `Delete “${button.dataset.delGroup}” and its files?`)) return;
      try {
        await post("/delete_folder", { category: state.category, group: button.dataset.delGroup });
        state.group = null;
        ok("Deleted.");
        await load();
      } catch (err) {
        bad(err.message);
      }
    });
  }

  host.querySelector("[data-new-group]")?.addEventListener("click", async () => {
    const name = prompt("What should the group be called?");
    if (!name) return;
    try {
      const result = await post("/group", { category: state.category, name });
      state.group = result.name;
      await load();
      await loadItems();
    } catch (err) {
      bad(err.message);
    }
  });
}

function renderItems() {
  const host = overlay?.querySelector(".su-items");
  if (!host) return;

  if (!state.items.length) {
    host.innerHTML = `<div class="su-empty">${icon("folder", 24)}
      <div class="su-empty-title">${state.query ? "Nothing matched" : "Nothing here yet"}</div>
      ${state.query ? "" : "<div>Drop files in, or pull one from a link.</div>"}</div>`;
    return;
  }

  host.innerHTML = state.items.map(itemRow).join("");
  for (const item of state.items) wireItem(host, item);
}

function itemRow(item) {
  const isAudio = item.type === "audio";
  const glyph = isAudio ? "audio" : item.type === "image" ? "image" : "film";
  const where = state.query && item.category ? esc(item.category) + (item.group ? " › " + esc(item.group) : "") : "";

  return `
  <div class="su-item" data-item="${esc(item.id)}">
    <div class="su-preview">
      ${
        isAudio
          ? icon(glyph, 20)
          : `<img loading="lazy" alt="" src="${esc(thumbUrl(item.id))}"
                  onerror="this.style.display='none';this.nextElementSibling.style.display='grid'" />
             <span style="display:none;position:absolute;inset:0;place-items:center">${icon(glyph, 20)}</span>
             <button class="su-play" data-view title="Play">${icon("play", 16)}</button>`
      }
    </div>

    <div class="su-item-body">
      <div class="su-item-name">${esc(item.name)}</div>
      <div class="su-item-meta">
        <span>${esc(item.type)}</span>
        ${where ? `<span>· ${where}</span>` : ""}
        <span>· ${esc(new Date(item.added).toLocaleDateString())}</span>
        ${item.size ? `<span>· ${esc(size(item.size))}</span>` : ""}
      </div>
      ${
        isAudio
          ? `<div class="su-player" data-player hidden>
               <button class="su-btn su-btn--icon su-btn--sm" data-toggle>${icon("play", 13)}</button>
               <input type="range" min="0" max="1000" value="0" data-seek />
               <span class="su-time" data-time>0:00</span>
             </div>`
          : ""
      }
    </div>

    <div class="su-item-actions">
      ${isAudio ? `<button class="su-btn su-btn--sm" data-listen>${icon("play", 13)}Listen</button>` : ""}
      <button class="su-btn su-btn--sm" data-timeline title="Add at the playhead">${icon("film", 13)}Timeline</button>
      <button class="su-btn su-btn--sm" data-pool title="Send to the media pool">${icon(
        "download",
        13
      )}Pool</button>
      <button class="su-btn su-btn--sm" data-graph title="Add a loader node for this file">${icon(
        "graph",
        13
      )}Graph</button>
      <button class="su-btn su-btn--sm su-btn--icon" data-rename title="Rename">${icon("pencil", 13)}</button>
      <select class="su-select" data-move title="Move somewhere else">
        <option value="">Move…</option>
        ${moveOptions(item)}
      </select>
      <button class="su-btn su-btn--sm su-btn--icon su-btn--danger" data-delete title="Delete">${icon(
        "trash",
        13
      )}</button>
    </div>
  </div>`;
}

function moveOptions(item) {
  const options = [];
  for (const category of state.tree.categories) {
    options.push(`<option value="${esc(category.name)}|">${esc(category.name)}</option>`);
    for (const group of category.groups) {
      options.push(
        `<option value="${esc(category.name)}|${esc(group.name)}">${esc(category.name)} › ${esc(
          group.name
        )}</option>`
      );
    }
  }
  return options.join("");
}

function size(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit];
}

/* ------------------------------------------------------------------ *
 * Wiring
 * ------------------------------------------------------------------ */

function wireChrome() {
  overlay.querySelector("[data-close]").addEventListener("click", close);

  const search = overlay.querySelector("[data-search]");
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.query = search.value.trim();
      renderChips();
      loadItems();
    }, 220);
  });

  for (const button of overlay.querySelectorAll("[data-cat]")) {
    button.addEventListener("click", (event) => {
      if (event.target.closest("[data-del-cat]")) return;
      state.category = button.dataset.cat;
      state.group = null;
      state.query = "";
      search.value = "";
      stopAudio();
      render();
      loadItems();
    });
  }

  for (const button of overlay.querySelectorAll("[data-del-cat]")) {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const name = button.dataset.delCat;
      if (!armed(button, `Delete “${name}” and everything in it?`)) return;
      try {
        await post("/delete_folder", { category: name });
        if (state.category === name) state.category = null;
        ok("Deleted.");
        await load();
      } catch (err) {
        bad(err.message);
      }
    });
  }

  overlay.querySelector("[data-new-cat]").addEventListener("click", async () => {
    const name = prompt("What should the category be called?");
    if (!name) return;
    const type = confirm("Is this for sound?\n\nOK for sound, Cancel for video.") ? "audio" : "video";
    try {
      const result = await post("/category", { name, type });
      state.category = result.name;
      state.group = null;
      await load();
      await loadItems();
    } catch (err) {
      bad(err.message);
    }
  });

  overlay.querySelector("[data-change-root]").addEventListener("click", async () => {
    const chosen = prompt("Where should the library live?", state.tree.root);
    if (!chosen || chosen === state.tree.root) return;
    try {
      await post("/root", { root: chosen });
      state.category = null;
      ok("Library folder changed.");
      await load();
    } catch (err) {
      bad(err.message);
    }
  });

  const input = overlay.querySelector("[data-file-input]");
  input.addEventListener("change", () => {
    if (input.files?.length) upload([...input.files]);
    input.value = "";
  });
  overlay.querySelector("[data-add]").addEventListener("click", () => input.click());

  overlay.querySelector("[data-link]").addEventListener("click", fromLink);

  const drop = overlay.querySelector("[data-drop]");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    drop.classList.add("is-over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("is-over");
    const files = [...(event.dataTransfer?.files || [])];
    if (files.length) upload(files);
  });
}

function wireItem(host, item) {
  const row = host.querySelector(`[data-item="${CSS.escape(item.id)}"]`);
  if (!row) return;

  const send = (button, label, place) =>
    button.addEventListener("click", () =>
      busy(button, label, async () => {
        try {
          const result = await post("/send", { path: item.id, place });
          ok(result.message || "Sent to Resolve.");
        } catch (err) {
          bad(err.message);
        }
      })
    );

  send(row.querySelector("[data-timeline]"), "Adding…", "at the playhead");
  send(row.querySelector("[data-pool]"), "Sending…", "media pool only");

  const graph = row.querySelector("[data-graph]");
  graph.addEventListener("click", () => busy(graph, "…", () => toGraph(item)));

  row.querySelector("[data-rename]").addEventListener("click", async () => {
    const name = prompt("New name", item.name);
    if (!name || name === item.name) return;
    try {
      await post("/rename", { path: item.id, name });
      await loadItems();
    } catch (err) {
      bad(err.message);
    }
  });

  row.querySelector("[data-move]").addEventListener("change", async (event) => {
    const value = event.target.value;
    if (!value) return;
    const [category, group] = value.split("|");
    try {
      await post("/move", { path: item.id, category, group: group || null });
      ok("Moved.");
      await load();
      await loadItems();
    } catch (err) {
      bad(err.message);
      event.target.value = "";
    }
  });

  const remove = row.querySelector("[data-delete]");
  remove.addEventListener("click", async () => {
    if (!armed(remove, "Delete this file?")) return;
    try {
      await post("/delete", { path: item.id });
      await load();
      await loadItems();
    } catch (err) {
      bad(err.message);
    }
  });

  row.querySelector("[data-view]")?.addEventListener("click", () => viewer(item));
  row.querySelector("[data-listen]")?.addEventListener("click", () => toggleAudio(row, item));
}

/* ------------------------------------------------------------------ *
 * Adding
 * ------------------------------------------------------------------ */

async function upload(files) {
  if (!state.category) return bad("Pick a category first.");

  const form = new FormData();
  form.append("category", state.category);
  form.append("group", state.group || "");
  for (const file of files) form.append("files", file, file.name);

  toast(files.length === 1 ? "Adding…" : `Adding ${files.length} files…`);

  try {
    const response = await fetch(API + "/upload", { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);

    if (data.failed?.length) bad(data.failed[0].name + ": " + data.failed[0].error);
    if (data.added?.length) ok(data.added.length === 1 ? "Added." : "Added " + data.added.length + " files.");
    await load();
    await loadItems();
  } catch (err) {
    bad(err.message);
  }
}

async function fromLink() {
  if (!state.ytdlp) {
    bad("yt-dlp is not installed, so links cannot be downloaded.");
    return;
  }
  if (!state.category) return bad("Pick a category first.");

  const url = prompt("Paste the link");
  if (!url) return;

  const category = state.tree.categories.find((c) => c.name === state.category);
  toast("Downloading in the background…");

  try {
    await post("/link", {
      url,
      mode: category?.type === "audio" ? "audio" : "video",
      category: state.category,
      group: state.group,
    });
    ok("Added.");
    await load();
    await loadItems();
  } catch (err) {
    bad(err.message);
  }
}

/* ------------------------------------------------------------------ *
 * Out to the graph
 * ------------------------------------------------------------------ */

const LOADERS = {
  image: { node: "LoadImage", widget: "image" },
  video: { node: "LoadVideo", widget: "file" },
  audio: { node: "LoadAudio", widget: "audio" },
};

async function toGraph(item) {
  const loader = LOADERS[item.type];
  if (!loader) return bad("Second Unit does not know which node loads that.");

  try {
    const result = await post("/to_input", { path: item.id });

    // A ComfyUI without that loader is not a broken one — say where the file
    // went and let them wire it up themselves.
    const LG = window.LiteGraph;
    if (!LG?.registered_node_types?.[loader.node]) {
      ok("Copied to ComfyUI's input folder as " + result.name + ".");
      close();
      return;
    }

    const node = LG.createNode(loader.node);
    app.graph.add(node);

    // Drop it in the middle of what the user is looking at, not at the origin.
    const area = app.canvas?.ds?.visible_area;
    node.pos = area
      ? [area[0] + area[2] / 2 - 110, area[1] + area[3] / 2 - 70]
      : app.canvas?.graph_mouse?.slice?.() || [100, 100];

    const widget = node.widgets?.find((w) => w.name === loader.widget);
    if (widget) {
      // The combo was built when ComfyUI started and will not list a file added
      // since. Push it in, or the value shows as invalid until a refresh.
      if (Array.isArray(widget.options?.values) && !widget.options.values.includes(result.name)) {
        widget.options.values.push(result.name);
      }
      widget.value = result.name;
      widget.callback?.(result.name);
    }

    node.setDirtyCanvas(true, true);
    app.graph.setDirtyCanvas(true, true);
    ok(item.name + " is on the graph.");
    close();
  } catch (err) {
    bad(err.message);
  }
}

/* ------------------------------------------------------------------ *
 * Audio — one element, shared
 * ------------------------------------------------------------------ */

function element() {
  if (audio) return audio;
  audio = new Audio();
  audio.addEventListener("timeupdate", paintTime);
  audio.addEventListener("loadedmetadata", paintTime);
  audio.addEventListener("ended", stopAudio);
  audio.addEventListener("play", paintToggle);
  audio.addEventListener("pause", paintToggle);
  return audio;
}

function toggleAudio(row, item) {
  const player = element();

  if (playingId === item.id) {
    if (player.paused) player.play().catch(() => {});
    else player.pause();
    return;
  }

  stopAudio();
  playingId = item.id;

  const bar = row.querySelector("[data-player]");
  if (bar) bar.hidden = false;

  player.src = fileUrl(item.id);
  player.play().catch(() => bad("That file will not play."));

  row.querySelector("[data-seek]")?.addEventListener("input", (event) => {
    if (player.duration) player.currentTime = (Number(event.target.value) / 1000) * player.duration;
  });
  row.querySelector("[data-toggle]")?.addEventListener("click", () => {
    if (player.paused) player.play().catch(() => {});
    else player.pause();
  });
  paintToggle();
}

function stopAudio() {
  if (!audio) return;
  audio.pause();
  const bar = playingId ? overlay?.querySelector(`[data-item="${CSS.escape(playingId)}"] [data-player]`) : null;
  if (bar) bar.hidden = true;
  playingId = null;
}

function currentRow() {
  return playingId ? overlay?.querySelector(`[data-item="${CSS.escape(playingId)}"]`) : null;
}

function paintTime() {
  const row = currentRow();
  if (!row || !audio) return;

  const seek = row.querySelector("[data-seek]");
  const time = row.querySelector("[data-time]");
  const fraction = audio.duration ? audio.currentTime / audio.duration : 0;

  // Do not fight the user while they are dragging the slider.
  if (seek && document.activeElement !== seek) seek.value = String(Math.round(fraction * 1000));
  if (time) time.textContent = clock(audio.currentTime) + " / " + clock(audio.duration);
}

function paintToggle() {
  const toggle = currentRow()?.querySelector("[data-toggle]");
  if (toggle && audio) toggle.innerHTML = icon(audio.paused ? "play" : "pause", 13);
}

function clock(seconds) {
  if (!Number.isFinite(seconds)) return "0:00";
  const total = Math.floor(seconds);
  return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
}

/* ------------------------------------------------------------------ *
 * Viewer
 * ------------------------------------------------------------------ */

function viewer(item) {
  stopAudio();

  const host = document.createElement("div");
  host.className = "su-viewer";
  host.innerHTML = `
    <button class="su-viewer-close">${icon("close", 18)}</button>
    <div>
      ${
        item.type === "image"
          ? `<img src="${esc(fileUrl(item.id))}" alt="${esc(item.name)}" />`
          : `<video src="${esc(fileUrl(item.id))}" controls autoplay playsinline></video>`
      }
      <div class="su-viewer-name">${esc(item.name)}</div>
    </div>`;

  const shut = () => {
    host.querySelector("video")?.pause();
    host.remove();
    document.removeEventListener("keydown", onKey, true);
  };
  const onKey = (event) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    shut();
  };

  host.addEventListener("click", (event) => {
    if (event.target === host || event.target.closest(".su-viewer-close")) shut();
  });
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(host);
}

/* ------------------------------------------------------------------ *
 * Registration
 * ------------------------------------------------------------------ */

app.registerExtension({
  name: "SecondUnit.Library",

  actionBarButtons: [
    {
      icon: "pi pi-folder-open",
      label: "Second Unit",
      tooltip: "Media library — clips, music and voice takes",
      onClick: () => (overlay ? close() : open()),
    },
  ],

  // The same library behind the menu the top bar's hamburger opens. Two ways in
  // on purpose: the action bar can be docked away or simply not render on some
  // layout, and one missing surface should not read as "the library is gone".
  // (The frontend only wires menuCommands whose ids it finds in `commands`
  // above, so the two entries must name the same command.)
  menuCommands: [
    {
      path: ["Second Unit"],
      commands: ["secondunit.library.open"],
    },
  ],

  commands: [
    {
      id: "secondunit.library.open",
      label: "Second Unit: Media Library",
      icon: "pi pi-folder-open",
      function: () => (overlay ? close() : open()),
    },
  ],
});

// One line in the console, so "did the library load?" is answerable anywhere —
// no line, and the file itself never made it to the browser.
console.info("[Second Unit] media library loaded — top bar button + hamburger menu");

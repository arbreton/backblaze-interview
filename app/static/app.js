// B2 Describe — vanilla JS UI. All user/API text goes through textContent (no innerHTML injection).

const $ = (sel, root = document) => root.querySelector(sel);
const state = { maxBytes: 50 * 1024 * 1024, b2Ready: false, queue: [], active: 0 };
const CONCURRENCY = 2;

const fmtBytes = (n) => {
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB"]; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`;
};
const ext = (name) => (name.includes(".") ? name.split(".").pop().slice(0, 4) : "file");
const announce = (msg) => { const el = $("#sr-status"); el.textContent = ""; setTimeout(() => (el.textContent = msg), 50); };

// ---------------------------------------------------------------- health
async function loadHealth() {
  const [b2Pill, claudePill] = document.querySelectorAll("#health .pill");
  try {
    const h = await (await fetch("/api/health")).json();
    state.maxBytes = h.max_upload_mb * 1024 * 1024;
    state.b2Ready = h.b2.configured;
    $("#drop-hint").textContent =
      `Images get a caption. PDFs and text files get a summary. Other files are stored without a description. Max ${h.max_upload_mb} MB each.`;
    $("#path-root").textContent = h.local_path_root;

    b2Pill.dataset.state = h.b2.configured ? "ok" : "off";
    b2Pill.title = h.b2.configured ? `Bucket: ${h.b2.bucket}` : "Not configured";
    b2Pill.lastChild.textContent = h.b2.configured ? `B2 · ${h.b2.bucket}` : "B2 · not configured";
    claudePill.dataset.state = h.claude.configured ? "ok" : "off";
    claudePill.lastChild.textContent = h.claude.configured ? `Claude · ${h.claude.model}` : "Claude · no key";

    const banner = $("#config-banner");
    if (!h.b2.configured) {
      banner.hidden = false;
      banner.replaceChildren(
        Object.assign(document.createElement("strong"), { textContent: "Backblaze B2 isn't connected yet" }),
        document.createTextNode("Add "),
        ...h.b2.missing.flatMap((m, i) => [Object.assign(document.createElement("code"), { textContent: m }), document.createTextNode(i < h.b2.missing.length - 1 ? ", " : "")]),
        document.createTextNode(" to the .env file and restart the server. Step-by-step setup: docs/B2_SETUP.md."),
      );
    } else banner.hidden = true;
  } catch {
    b2Pill.dataset.state = claudePill.dataset.state = "off";
  }
}

// ---------------------------------------------------------------- tabs (WAI-ARIA pattern)
const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab) {
  tabs.forEach((t) => {
    const on = t === tab;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
    $(`#${t.getAttribute("aria-controls")}`).hidden = !on;
  });
  tab.focus();
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => selectTab(t));
  t.addEventListener("keydown", (e) => {
    const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (d) { e.preventDefault(); selectTab(tabs[(i + d + tabs.length) % tabs.length]); }
  });
});

// ---------------------------------------------------------------- result cards
function createCard(name, size, file) {
  $("#results-section").hidden = false;
  const li = $("#result-tpl").content.firstElementChild.cloneNode(true);
  $(".name", li).textContent = name;
  $(".name", li).title = name;
  $(".meta", li).textContent = size != null ? fmtBytes(size) : "Local file";
  $(".badge", li).textContent = "Working";
  const thumb = $(".thumb", li);
  if (file && file.type.startsWith("image/")) {
    const url = URL.createObjectURL(file);
    thumb.style.backgroundImage = `url("${url}")`;
    thumb.classList.add("has-image");
  } else thumb.textContent = ext(name);
  $("#results").prepend(li);
  return li;
}

function setStage(li, text, pct) {
  $(".stage", li).textContent = text;
  const bar = $(".progress", li);
  bar.classList.toggle("indeterminate", pct == null);
  if (pct == null) bar.removeAttribute("aria-valuenow");
  else { bar.setAttribute("aria-valuenow", Math.round(pct)); $("span", bar).style.width = `${pct}%`; }
  bar.setAttribute("aria-label", text);
}

const BADGE = { described: "Described", stored: "Stored", failed: "Failed" };

function renderResult(li, r) {
  li.dataset.status = r.status;
  $(".badge", li).textContent = BADGE[r.status] ?? r.status;
  $(".name", li).textContent = r.filename;
  const kind = r.description_kind === "caption" ? "Caption" : r.description_kind === "summary" ? "Summary" : null;
  const parts = [fmtBytes(r.size), r.content_type.split(";")[0]];
  if (r.timings_ms?.total) parts.push(`${(r.timings_ms.total / 1000).toFixed(1)} s`);
  $(".meta", li).textContent = parts.join(" · ");

  if (r.description) {
    const q = $(".desc", li);
    q.hidden = false;
    q.textContent = r.description;
    if (kind) q.setAttribute("aria-label", kind);
  }
  if (r.tags?.length) {
    const ul = $(".tags", li);
    ul.hidden = false;
    ul.replaceChildren(...r.tags.map((t) => Object.assign(document.createElement("li"), { textContent: t })));
  }
  const notes = [];
  if (r.error) notes.push(r.error);
  if (r.truncated_input) notes.push("The file was long, so only its beginning was summarized.");
  if (notes.length) { const n = $(".note", li); n.hidden = false; n.textContent = notes.join(" "); }

  const actions = $(".actions", li);
  if (r.description || r.url) actions.hidden = false;
  const copy = $(".copy", li);
  copy.hidden = !r.description;
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(r.description); copy.textContent = "Copied ✓"; announce("Description copied"); }
    catch { copy.textContent = "Copy failed"; }
    setTimeout(() => (copy.textContent = "Copy description"), 1600);
  };
  const open = $(".open", li);
  open.hidden = !r.url;
  if (r.url) open.href = r.url;

  announce(`${r.filename}: ${BADGE[r.status] ?? r.status}${r.description ? `. ${r.description}` : ""}`);
}

function renderError(li, message) {
  li.dataset.status = "failed";
  $(".badge", li).textContent = "Failed";
  const n = $(".note", li);
  n.hidden = false;
  n.textContent = message;
  announce(`Failed: ${message}`);
}

async function errorMessage(status, bodyText) {
  try {
    const j = JSON.parse(bodyText);
    if (typeof j.detail === "string") return j.detail;
    if (j.error) return j.error;
  } catch {}
  return { 413: "File is too large.", 503: "Service not configured." }[status] ?? `Request failed (HTTP ${status}).`;
}

// ---------------------------------------------------------------- streamed server stages (NDJSON)
const STAGES = {
  storing: ["Saving to Backblaze B2…", null],
  stored: ["Saved to B2 ✓ — asking Claude for a description…", null],
  describing: ["Saved to B2 ✓ — Claude is writing a description…", null],
  attaching: ["Description ready ✓ — attaching it to the file in B2…", 92],
};

// Feed it the full response text as it grows; it handles each complete line exactly once.
function ndjsonReader(li) {
  let seen = 0, done = false;
  return (text, final = false) => {
    const lines = text.split("\n");
    const complete = final ? lines : lines.slice(0, -1);
    for (; seen < complete.length; seen++) {
      const line = complete[seen].trim();
      if (!line) continue;
      let ev; try { ev = JSON.parse(line); } catch { continue; }
      if (ev.event === "stage" && STAGES[ev.stage]) setStage(li, ...STAGES[ev.stage]);
      else if (ev.event === "result") { renderResult(li, ev.result); done = true; }
      else if (ev.event === "error") { renderError(li, ev.error); done = true; }
    }
    return done;
  };
}

// ---------------------------------------------------------------- upload (XHR for real progress)
function uploadFile(file) {
  const li = createCard(file.name, file.size, file);
  if (file.size > state.maxBytes) return renderError(li, `File is larger than ${fmtBytes(state.maxBytes)}.`);
  if (file.size === 0) return renderError(li, "File is empty.");
  setStage(li, "Waiting…", 0);
  state.queue.push(() => new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    const read = ndjsonReader(li);
    xhr.open("POST", "/api/files?stream=true");
    // Upload is the first 40% of the bar; server stages take it from there.
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = (e.loaded / e.total) * 100;
      setStage(li, `Uploading… ${Math.round(pct)}%`, pct * 0.4);
    };
    xhr.upload.onload = () => setStage(li, ...STAGES.storing);
    xhr.onprogress = () => { if (xhr.status === 200) read(xhr.responseText); };
    xhr.onload = async () => {
      if (xhr.status === 200) {
        if (!read(xhr.responseText, true)) renderError(li, "The server closed the connection before finishing.");
      } else renderError(li, await errorMessage(xhr.status, xhr.responseText));
      resolve();
    };
    xhr.onerror = () => { renderError(li, "Network error — is the server running?"); resolve(); };
    const fd = new FormData();
    fd.append("file", file, file.name);
    setStage(li, "Uploading… 0%", 0);
    xhr.send(fd);
  }));
  pump();
}

function pump() {
  while (state.active < CONCURRENCY && state.queue.length) {
    const job = state.queue.shift();
    state.active++;
    job().finally(() => { state.active--; pump(); if (!state.active && !state.queue.length) loadRecent(); });
  }
}

const handleFiles = (files) => [...files].forEach(uploadFile);

// dropzone
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
drop.addEventListener("drop", (e) => handleFiles(e.dataTransfer.files));
// prevent the browser from opening files dropped outside the zone
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());
$("#file-input").addEventListener("change", (e) => { handleFiles(e.target.files); e.target.value = ""; });
document.addEventListener("paste", (e) => {
  if (e.target.closest("input, textarea")) return;
  const files = [...(e.clipboardData?.files ?? [])];
  if (files.length) handleFiles(files);
});

// ---------------------------------------------------------------- local path
$("#path-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#path-input");
  const path = input.value.trim();
  if (!path) { input.setAttribute("aria-invalid", "true"); input.focus(); return; }
  input.removeAttribute("aria-invalid");
  const btn = $('button[type="submit"]', e.target);
  btn.disabled = true;
  const li = createCard(path.split("/").pop(), null, null);
  setStage(li, "Reading file…", null);
  try {
    const res = await fetch("/api/files/from-path?stream=true", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }),
    });
    if (res.ok && res.body) {
      const read = ndjsonReader(li), reader = res.body.getReader(), dec = new TextDecoder();
      let text = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        text += dec.decode(value, { stream: true });
        read(text);
      }
      if (!read(text, true)) renderError(li, "The server closed the connection before finishing.");
      input.value = "";
      loadRecent();
    } else renderError(li, await errorMessage(res.status, await res.text()));
  } catch { renderError(li, "Network error — is the server running?"); }
  finally { btn.disabled = false; }
});

$("#clear-results").addEventListener("click", () => {
  $("#results").replaceChildren();
  $("#results-section").hidden = true;
  $("#file-input").focus();
});

// ---------------------------------------------------------------- recent files
async function loadRecent() {
  const box = $("#recent");
  const empty = (msg) => box.replaceChildren(Object.assign(document.createElement("p"), { className: "empty", textContent: msg }));
  box.setAttribute("aria-busy", "true");
  try {
    const res = await fetch("/api/files?limit=12");
    if (res.status === 503) return empty("Connect Backblaze B2 to see stored files.");
    if (!res.ok) return empty(await errorMessage(res.status, await res.text()));
    const items = await res.json();
    if (!items.length) return empty("No files yet. Upload one above.");
    box.replaceChildren(...items.map((it) => {
      const row = document.createElement("article");
      row.className = "recent-item";
      const th = document.createElement("div");
      th.className = "thumb";
      th.setAttribute("aria-hidden", "true");
      th.textContent = ext(it.filename); // fallback label while loading / if the preview fails
      if (it.content_type.startsWith("image/")) {
        const img = Object.assign(document.createElement("img"), { src: it.url, alt: "", loading: "lazy", decoding: "async" });
        img.onerror = () => img.remove();
        th.append(img);
      }
      const body = document.createElement("div");
      body.style.minWidth = "0";
      const h = Object.assign(document.createElement("h3"), { textContent: it.filename });
      const when = it.uploaded_at ? new Date(it.uploaded_at).toLocaleString() : "";
      const meta = Object.assign(document.createElement("p"), { textContent: [fmtBytes(it.size), when].filter(Boolean).join(" · ") });
      const desc = Object.assign(document.createElement("p"), {
        textContent: it.description ?? ({ skipped: "No description (unsupported type)", failed: "Description failed", pending: "Description pending" }[it.description_status] ?? "No description"),
      });
      desc.style.color = "var(--text)";
      body.append(h, meta, desc);
      if (it.tags.length) {
        const ul = document.createElement("ul");
        ul.className = "tags";
        ul.setAttribute("aria-label", "Tags");
        ul.append(...it.tags.map((t) => Object.assign(document.createElement("li"), { textContent: t })));
        body.append(ul);
      }
      const open = Object.assign(document.createElement("a"), { className: "btn small ghost open", href: it.url, target: "_blank", rel: "noopener", textContent: "Open" });
      open.setAttribute("aria-label", `Open ${it.filename}`);
      row.append(th, body, open);
      return row;
    }));
  } catch { empty("Couldn't load files."); }
  finally { box.setAttribute("aria-busy", "false"); }
}
$("#refresh-recent").addEventListener("click", loadRecent);

// ---------------------------------------------------------------- curl snippet
$("#curl-snippet").textContent =
`# Upload a file
curl -F "file=@photo.jpg" ${location.origin}/api/files

# Store a file that's already on this machine
curl -H "Content-Type: application/json" \\
     -d '{"path": "~/Desktop/notes.txt"}' ${location.origin}/api/files/from-path

# List recent files with their AI metadata
curl ${location.origin}/api/files`;

loadHealth().then(loadRecent);

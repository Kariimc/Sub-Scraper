/* Sub-Scraper Web UI — vanilla JS, no dependencies */
"use strict";

// ===== State =====
let _source = "spotify";
let _tracks = [];
let _sse = null;
let _demoMode = false; // true while the no-credentials sample library is shown
let _envLocked = new Set(); // field names pre-configured via server env vars
let _dlStats = { done: 0, failed: 0, queue: 0, active: 0 };
let _activeDl = {}; // track_id -> {name, fraction, speed, eta}

// ===== Init =====
document.addEventListener("DOMContentLoaded", () => {
  loadConfig();
  checkHealth();
  connectSSE();
  showSection("library");
});

// ===== Health =====
async function checkHealth() {
  const dot = document.getElementById("status-dot");
  const lbl = document.getElementById("status-label");
  try {
    const r = await fetch("/api/health");
    if (r.ok) {
      dot.className = "status-dot ok";
      lbl.textContent = "Connected";
    } else {
      throw new Error("not ok");
    }
  } catch {
    dot.className = "status-dot err";
    lbl.textContent = "Offline";
    setTimeout(checkHealth, 5000);
  }
}

// ===== Navigation =====
function showSection(name) {
  document.querySelectorAll(".section").forEach(s => s.classList.remove("active"));
  document.getElementById("section-" + name).classList.add("active");
  document.querySelectorAll(".nav-item").forEach(b => {
    b.classList.toggle("active", b.dataset.section === name);
  });
}

// ===== Source selection =====
function setSource(src) {
  _source = src;
  document.getElementById("pill-spotify").classList.toggle("active", src === "spotify");
  document.getElementById("pill-soundcloud").classList.toggle("active", src === "soundcloud");
}

// ===== Library =====
async function loadLibrary() {
  const btn = document.getElementById("btn-load");
  const spin = document.getElementById("load-spinner");
  const status = document.getElementById("library-status");

  btn.disabled = true;
  spin.classList.remove("hidden");
  status.textContent = "Loading library…";
  document.getElementById("track-controls").style.display = "none";
  document.getElementById("track-list").innerHTML = "";

  try {
    const r = await fetch("/api/library/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: _source }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: "Unknown error" }));
      status.textContent = "Error: " + (err.detail || r.statusText);
      return;
    }
    const data = await r.json();
    _tracks = data.tracks || [];
    _demoMode = false;
    document.getElementById("demo-banner").classList.add("hidden");
    renderTracks(_tracks);
    const pending = _tracks.filter(t => t.status !== "complete").length;
    const done = _tracks.length - pending;
    status.textContent = `${_tracks.length} tracks loaded — ${done} already downloaded, ${pending} to grab`;
    document.getElementById("track-controls").style.display = _tracks.length ? "flex" : "none";
  } catch (e) {
    status.textContent = "Network error: " + e.message;
  } finally {
    btn.disabled = false;
    spin.classList.add("hidden");
  }
}

// Load a no-setup sample library so first-time visitors can explore the UI.
async function loadDemo() {
  const btn = document.getElementById("btn-demo");
  const status = document.getElementById("library-status");

  btn.disabled = true;
  status.textContent = "Loading demo…";
  document.getElementById("track-list").innerHTML = "";

  try {
    const r = await fetch("/api/library/demo", { method: "POST" });
    if (!r.ok) {
      status.textContent = "Couldn't load the demo right now.";
      return;
    }
    const data = await r.json();
    _tracks = data.tracks || [];
    _demoMode = true;
    renderTracks(_tracks);
    document.getElementById("demo-banner").classList.remove("hidden");
    document.getElementById("track-controls").style.display = _tracks.length ? "flex" : "none";
    status.textContent = `${_tracks.length} sample tracks — explore freely, then add your keys to load your real library.`;
  } catch (e) {
    status.textContent = "Network error: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

function renderTracks(tracks) {
  const list = document.getElementById("track-list");
  list.innerHTML = "";
  if (!tracks.length) {
    list.innerHTML = '<p style="color:var(--text-muted);padding:24px 0;">No tracks found.</p>';
    return;
  }
  tracks.forEach(t => {
    const row = document.createElement("div");
    row.className = "track-row" + (t.status === "complete" ? " selected" : "");
    row.id = "track-row-" + t.id;
    row.dataset.id = t.id;

    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.id = "chk-" + t.id;
    chk.checked = false;
    chk.addEventListener("change", () => {
      row.classList.toggle("selected", chk.checked);
    });

    const info = document.createElement("div");
    info.className = "track-info";
    info.innerHTML = `<div class="track-title">${esc(t.title)}</div>
                      <div class="track-artist">${esc(t.artist)}</div>`;

    const dur = document.createElement("div");
    dur.className = "track-duration";
    dur.textContent = t.duration_str || "--:--";

    const badge = document.createElement("span");
    badge.className = "track-status status-" + (t.status || "pending");
    badge.id = "badge-" + t.id;
    badge.textContent = statusLabel(t.status);

    const prog = document.createElement("div");
    prog.className = "track-progress";
    prog.id = "prog-" + t.id;
    prog.style.display = "none";
    prog.innerHTML = `<progress value="0" max="1"></progress>`;

    row.append(chk, info, dur, prog, badge);
    row.addEventListener("click", e => {
      if (e.target === chk) return;
      chk.checked = !chk.checked;
      row.classList.toggle("selected", chk.checked);
    });
    list.appendChild(row);
  });
}

function statusLabel(s) {
  if (s === "complete") return "✓ Downloaded";
  if (s === "failed") return "✗ Failed";
  if (s === "downloading") return "↓ Downloading";
  return "○ Pending";
}

function esc(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ===== Selection =====
function toggleSelectAll(checked) {
  document.querySelectorAll("#track-list .track-row input[type=checkbox]").forEach(c => {
    c.checked = checked;
    c.closest(".track-row").classList.toggle("selected", checked);
  });
  document.getElementById("chk-all").checked = checked;
}

// ===== Downloads =====
function getCheckedIds() {
  const ids = [];
  document.querySelectorAll("#track-list .track-row input[type=checkbox]:checked").forEach(c => {
    ids.push(c.closest(".track-row").dataset.id);
  });
  return ids;
}

async function downloadSelected() {
  const ids = getCheckedIds();
  if (!ids.length) { alert("Select some tracks first."); return; }
  await submitDownload(ids);
}

async function downloadAll() {
  const ids = _tracks.filter(t => t.status !== "complete").map(t => t.id);
  if (!ids.length) { alert("Nothing to download — all tracks are already on disk."); return; }
  await submitDownload(ids);
}

async function submitDownload(ids) {
  if (_demoMode) {
    alert(
      "This is the demo library 🎧\n\n" +
      "To download for real, open Settings and add your own free Spotify or " +
      "SoundCloud credentials — it takes about 2 minutes."
    );
    showSection("settings");
    return;
  }
  showSection("downloads");
  try {
    const r = await fetch("/api/downloads/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_ids: ids, source: _source }),
    });
    const data = await r.json();
    if (!r.ok) { appendLog("Error: " + (data.detail || r.statusText)); return; }
    _dlStats.queue += data.queued;
    updateStats();
    appendLog(`Queued ${data.queued} track(s) for download.`);
  } catch (e) {
    appendLog("Network error: " + e.message);
  }
}

async function cancelAll() {
  await fetch("/api/downloads/cancel", { method: "POST" });
  appendLog("Cancel requested.");
}

// ===== SSE =====
function connectSSE() {
  if (_sse) { _sse.close(); }
  _sse = new EventSource("/api/downloads/stream");

  _sse.onmessage = e => {
    try {
      const ev = JSON.parse(e.data);
      if (ev.type === "timeout") { reconnectSSE(); return; }
      if (ev.type === "log") { appendLog(ev.message); return; }
      if (ev.type === "progress") { handleProgress(ev); return; }
      if (ev.type === "status") { handleStatus(ev); return; }
    } catch {}
  };

  _sse.onerror = () => { reconnectSSE(); };
}

function reconnectSSE() {
  if (_sse) { _sse.close(); _sse = null; }
  setTimeout(connectSSE, 3000);
}

function handleProgress(ev) {
  const { track_id, fraction, speed, eta, status } = ev;

  // Update in-library badge and progress bar
  const badge = document.getElementById("badge-" + track_id);
  const progEl = document.getElementById("prog-" + track_id);
  if (badge) {
    badge.className = "track-status status-" + (status || "downloading");
    badge.textContent = statusLabel(status);
  }
  if (progEl && status === "downloading") {
    progEl.style.display = "";
    const p = progEl.querySelector("progress");
    if (p) p.value = fraction || 0;
  }

  // Update active downloads panel
  if (status === "downloading") {
    const name = (() => {
      const t = _tracks.find(x => x.id === track_id);
      return t ? `${t.artist} - ${t.title}` : track_id;
    })();
    _activeDl[track_id] = { name, fraction: fraction || 0, speed: speed || "", eta: eta || "" };
  } else {
    delete _activeDl[track_id];
  }

  renderActiveDl();
  updateStats();
}

function handleStatus(ev) {
  const { track_id, status } = ev;
  if (status === "complete") {
    _dlStats.done++;
    _dlStats.queue = Math.max(0, _dlStats.queue - 1);
  } else if (status === "failed") {
    _dlStats.failed++;
    _dlStats.queue = Math.max(0, _dlStats.queue - 1);
  }

  const badge = document.getElementById("badge-" + track_id);
  if (badge) {
    badge.className = "track-status status-" + status;
    badge.textContent = statusLabel(status);
  }
  const progEl = document.getElementById("prog-" + track_id);
  if (progEl) progEl.style.display = "none";

  delete _activeDl[track_id];
  renderActiveDl();
  updateStats();
}

function renderActiveDl() {
  const list = document.getElementById("dl-track-list");
  list.innerHTML = "";
  Object.entries(_activeDl).forEach(([id, dl]) => {
    const pct = Math.round((dl.fraction || 0) * 100);
    const meta = [dl.speed, dl.eta].filter(Boolean).join("  ·  ");
    list.innerHTML += `
      <div class="dl-track">
        <div class="dl-track-header">
          <span class="dl-track-name">${esc(dl.name)}</span>
          <span class="dl-track-meta">${esc(meta || pct + "%")}</span>
        </div>
        <div class="dl-progress-bar">
          <div class="dl-progress-fill" style="width:${pct}%"></div>
        </div>
      </div>`;
  });

  // Update nav badge
  const active = Object.keys(_activeDl).length;
  const badge = document.getElementById("nav-badge-downloads");
  if (active > 0) {
    badge.textContent = active;
    badge.style.display = "";
  } else {
    badge.style.display = "none";
  }
}

function updateStats() {
  _dlStats.active = Object.keys(_activeDl).length;
  document.getElementById("stat-done").textContent   = _dlStats.done;
  document.getElementById("stat-failed").textContent = _dlStats.failed;
  document.getElementById("stat-queue").textContent  = _dlStats.queue;
  document.getElementById("stat-active").textContent = _dlStats.active;
}

// ===== Log =====
function appendLog(msg) {
  const box = document.getElementById("log-console");
  const line = document.createElement("div");
  const isError = /error|fail/i.test(msg);
  line.className = isError ? "log-line-error" : "";
  line.textContent = new Date().toLocaleTimeString() + "  " + msg;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function clearLog() {
  document.getElementById("log-console").innerHTML = "";
}

// ===== Config =====
async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    if (!r.ok) return;
    const cfg = await r.json();

    // Populate settings fields
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el && val !== undefined) el.value = val === "••••••••" ? "" : val;
    };
    set("f-spotify-id",     cfg.spotify_client_id);
    set("f-spotify-secret", cfg.spotify_client_secret);
    set("f-sc-user",        cfg.soundcloud_username);
    set("f-sc-token",       cfg.soundcloud_auth_token);
    set("f-dl-path",        cfg.download_path);
    set("f-format",         cfg.output_format);
    set("f-quality",        cfg.audio_quality);
    set("f-concurrent",     cfg.max_concurrent);

    // Mark fields that are pre-configured via server environment variables.
    _envLocked = new Set(cfg.env_locked || []);
    const fieldToInputId = {
      spotify_client_id:     "f-spotify-id",
      spotify_client_secret: "f-spotify-secret",
      soundcloud_username:   "f-sc-user",
      soundcloud_auth_token: "f-sc-token",
    };
    for (const [fieldName, inputId] of Object.entries(fieldToInputId)) {
      const el = document.getElementById(inputId);
      if (!el) continue;
      if (_envLocked.has(fieldName)) {
        el.disabled = true;
        el.placeholder = "Pre-configured via environment variable";
        el.title = "Locked — set via server environment variable";
        const row = el.closest(".form-row");
        if (row && !row.querySelector(".lock-badge")) {
          const badge = document.createElement("span");
          badge.className = "lock-badge";
          badge.textContent = "locked";
          row.querySelector("label").appendChild(badge);
        }
        const revealBtn = el.parentElement.querySelector(".reveal-btn");
        if (revealBtn) revealBtn.style.display = "none";
      } else {
        el.disabled = false;
        const row = el.closest(".form-row");
        if (row) row.querySelector(".lock-badge")?.remove();
        const revealBtn = el.parentElement.querySelector(".reveal-btn");
        if (revealBtn) revealBtn.style.display = "";
      }
    }

    // Show first-run notice if no credentials
    const hasCredentials = cfg.spotify_client_id || cfg.soundcloud_username;
    const notice = document.getElementById("first-run-notice");
    if (notice) notice.classList.toggle("hidden", !!hasCredentials);
  } catch {}
}

async function saveConfig(silent) {
  const form = document.getElementById("settings-form");
  const status = document.getElementById("save-status");

  // Secret fields load blank (they're masked). If left blank, send the mask
  // placeholder so the server KEEPS the stored secret instead of wiping it.
  const keepIfBlank = v => (v && v.trim()) ? v : "••••••••";
  // Env-locked fields are read-only on the server — omit them from the payload.
  const skip = f => _envLocked.has(f);

  const body = {
    ...(!skip("spotify_client_id")     && { spotify_client_id:     form.spotify_client_id.value }),
    ...(!skip("spotify_client_secret") && { spotify_client_secret: keepIfBlank(form.spotify_client_secret.value) }),
    ...(!skip("soundcloud_username")   && { soundcloud_username:   form.soundcloud_username.value }),
    ...(!skip("soundcloud_auth_token") && { soundcloud_auth_token: keepIfBlank(form.soundcloud_auth_token.value) }),
    download_path:  form.download_path.value,
    output_format:  form.output_format.value,
    audio_quality:  form.audio_quality.value,
    max_concurrent: parseInt(form.max_concurrent.value) || 6,
  };

  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      if (!silent) {
        status.textContent = "✓ Saved";
        setTimeout(() => { status.textContent = ""; }, 3000);
      }
    } else if (!silent) {
      status.textContent = "Save failed.";
    }
  } catch {
    if (!silent) status.textContent = "Network error.";
  }
}

async function testConnection(source, btn) {
  const status = document.getElementById("test-" + source);
  // Save current form values first so the test uses what's typed right now.
  await saveConfig(true);

  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Testing…";
  status.textContent = "";
  status.className = "test-status";

  try {
    const r = await fetch("/api/config/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    const data = await r.json();
    status.textContent = (data.ok ? "✓ " : "✗ ") + (data.message || "");
    status.className = "test-status " + (data.ok ? "test-ok" : "test-err");
  } catch (e) {
    status.textContent = "✗ Network error: " + e.message;
    status.className = "test-status test-err";
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// ===== Helpers =====
function toggleReveal(inputId, btn) {
  const el = document.getElementById(inputId);
  if (!el) return;
  const isPassword = el.type === "password";
  el.type = isPassword ? "text" : "password";
  btn.textContent = isPassword ? "Hide" : "Show";
}

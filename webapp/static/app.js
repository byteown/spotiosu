"use strict";

const $ = (id) => document.getElementById(id);
const DEFAULT_VOLUME = 0.20; // preview always starts at 20%

const S = {
  user: null,
  onboarding: false,
  target: 10,
  rated: 0,
  allGenres: [],
  picked: new Set(),
  queue: [],
  idx: 0,
  pending: null,   // in-flight prefetch promise
  busy: false,
  filters: { auto: true, min: 3, max: 6, mods: "" },
};

// ============================ boot ============================
async function init() {
  showLoginError();
  let state;
  try {
    state = await (await fetch("/api/state")).json();
  } catch (_) {
    state = { user: null };
  }
  if (!state.user) { $("login").classList.remove("hidden"); return; }

  S.user = state.user;
  S.allGenres = state.all_genres || [];
  S.target = state.onboarding_target || 10;
  S.rated = state.rated_count || 0;
  renderProfile();
  $("shell").classList.remove("hidden");

  if (state.onboarded) {
    startMain();
  } else if ((state.genres || []).length >= 2) {
    startOnboardingRating();
  } else {
    showGenrePicker();
  }
}

function showLoginError() {
  const err = new URLSearchParams(location.search).get("error");
  if (!err) return;
  const map = {
    denied: "Sign-in was cancelled.",
    state: "Session expired, please try again.",
    oauth: "Could not complete osu! sign-in. Check the app's callback URL.",
  };
  const el = $("login-error");
  el.textContent = map[err] || "Sign-in failed.";
  el.classList.remove("hidden");
  history.replaceState({}, "", "/");
}

function renderProfile() {
  const u = S.user;
  $("avatar").src = u.avatar_url || "";
  $("username").textContent = u.username;
  const bits = [];
  if (u.pp) bits.push(`${Math.round(u.pp).toLocaleString()}pp`);
  if (u.global_rank) bits.push(`#${u.global_rank.toLocaleString()}`);
  bits.push(u.mode);
  $("stats").textContent = bits.join(" · ");
}

// ============================ step 1: genres ============================
function showGenrePicker() {
  $("stage-view").classList.add("hidden");
  $("genres-view").classList.remove("hidden");
  const grid = $("genre-grid");
  grid.innerHTML = "";
  for (const g of S.allGenres) {
    const b = document.createElement("button");
    b.className = "genre-chip";
    b.textContent = g.name;
    b.onclick = () => {
      if (S.picked.has(g.id)) { S.picked.delete(g.id); b.classList.remove("on"); }
      else { S.picked.add(g.id); b.classList.add("on"); }
      $("genres-continue").disabled = S.picked.size < 2;
    };
    grid.appendChild(b);
  }
}

$("genres-continue").onclick = async () => {
  const btn = $("genres-continue");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Loading songs…';
  const res = await fetch("/api/onboarding/genres", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ genres: [...S.picked] }),
  });
  btn.textContent = "Continue";
  if (!res.ok) { btn.disabled = false; return; }
  startOnboardingRating();
};

// ============================ step 2: rate songs ============================
async function startOnboardingRating() {
  S.onboarding = true;
  S.queue = []; S.idx = 0; S.pending = null;
  $("genres-view").classList.add("hidden");
  $("stage-view").classList.remove("hidden");
  $("onboard-bar").classList.remove("hidden");
  $("filters").classList.add("hidden");
  updateProgress();
  await loadQueue();
  render();
}

function updateProgress() {
  const pct = Math.min(100, (S.rated / S.target) * 100);
  $("ob-fill").style.width = pct + "%";
  $("ob-count").textContent = `${Math.min(S.rated, S.target)} / ${S.target}`;
}

async function finishOnboarding() {
  await fetch("/api/onboarding/complete", { method: "POST" });
  startMain();
}

// ============================ main: recommendations ============================
async function startMain() {
  S.onboarding = false;
  S.queue = []; S.idx = 0; S.pending = null;
  $("genres-view").classList.add("hidden");
  $("stage-view").classList.remove("hidden");
  $("onboard-bar").classList.add("hidden");
  $("filters").classList.remove("hidden");
  await loadQueue();
  render();
}

// ============================ queue ============================
function feedUrl() {
  const p = new URLSearchParams({ count: "10" });
  if (S.filters.mods) p.set("mods", S.filters.mods);
  if (!S.filters.auto) {
    p.set("min_stars", S.filters.min);
    p.set("max_stars", S.filters.max);
  }
  return "/api/feed?" + p.toString();
}

async function fetchBatch() {
  const url = S.onboarding ? "/api/onboarding/tracks?count=12" : feedUrl();
  const res = await fetch(url);
  if (res.status === 401) { location.reload(); return []; }
  if (!res.ok) return [];
  const data = await res.json();
  if (data.suggested && S.filters.auto) applySuggested(data.suggested);
  return data.items || [];
}

function applySuggested(sug) {
  const lo = Math.max(1, Math.min(10, sug.min));
  const hi = Math.max(1, Math.min(10, sug.max));
  S.filters.min = Math.round(lo * 10) / 10;
  S.filters.max = Math.round(hi * 10) / 10;
  $("min-stars").value = S.filters.min;
  $("max-stars").value = S.filters.max;
  updateRangeLabel();
}

async function loadQueue() {
  showLoading(true);
  let items = [];
  try {
    items = S.pending ? await S.pending : await fetchBatch();
  } catch (_) { items = []; }
  S.pending = null;
  S.queue = items;
  S.idx = 0;
  showLoading(false);
  if (!items.length) showEmpty();
}

function maybePrefetch() {
  if (S.pending) return;
  if (S.queue.length - S.idx <= 2) S.pending = fetchBatch().catch(() => []);
}

function current() { return S.queue[S.idx] || null; }

// ============================ rendering ============================
function showLoading(on) {
  $("stage-loading").classList.toggle("hidden", !on);
  if (on) { $("stage").classList.add("hidden"); $("stage-empty").classList.add("hidden"); }
}

function showEmpty() {
  $("stage").classList.add("hidden");
  const el = $("stage-empty");
  el.textContent = S.onboarding
    ? "Couldn't load songs for those genres. Try picking different ones."
    : "No fresh maps match these filters. Widen the difficulty range or reset history.";
  el.classList.remove("hidden");
}

function render() {
  const rec = current();
  if (!rec) { showEmpty(); return; }
  $("stage-empty").classList.add("hidden");
  $("stage").classList.remove("hidden");

  $("art").src = rec.cover_url || "";
  $("stage-bg").style.backgroundImage = rec.cover_url ? `url("${rec.cover_url}")` : "none";
  $("t-title").textContent = rec.title;
  $("t-artist").textContent = rec.artist;
  const sub = [`[${rec.version}]`, `mapped by ${rec.creator}`];
  $("t-sub").textContent = sub.join(" · ");

  const b = [`<span class="badge stars">★ ${rec.stars.toFixed(2)}</span>`];
  if (rec.genre_name) b.push(`<span class="badge genre">${esc(rec.genre_name)}</span>`);
  if (rec.bpm) b.push(`<span class="badge">${Math.round(rec.bpm)} BPM</span>`);
  if (rec.length_str) b.push(`<span class="badge">${rec.length_str}</span>`);
  if (rec.mods && rec.mods.length) b.push(`<span class="badge">+${rec.mods.join("")}</span>`);
  if (rec.pp && rec.pp["100"] != null)
    b.push(`<span class="badge pp">${Math.round(rec.pp["100"])}pp @100%</span>`);
  $("t-badges").innerHTML = b.join("");

  $("open-osu").href = rec.url;
  $("open-osu").onclick = (e) => openInOsu(e, rec);
  loadPreview(rec);
  maybePrefetch();
}

// Try to hand the beatmap straight to osu!lazer via its `osu://` URL scheme.
// If no handler picks it up (osu! not installed / scheme unregistered), the
// browser stays focused and we fall back to the beatmap page on the website.
function openInOsu(e, rec) {
  e.preventDefault();
  let handled = false;
  const markHandled = () => { handled = true; };
  window.addEventListener("blur", markHandled, { once: true });
  document.addEventListener("visibilitychange", markHandled, { once: true });

  try {
    window.location.href = `osu://dl/${rec.set_id}`;
  } catch (_) {
    handled = false;
  }

  setTimeout(() => {
    window.removeEventListener("blur", markHandled);
    document.removeEventListener("visibilitychange", markHandled);
    if (!handled && !document.hidden) window.open(rec.url, "_blank", "noopener");
  }, 1200);
}

// ============================ audio ============================
const audio = () => $("audio");

// Load the preview but never start it on its own - the user presses play.
function loadPreview(rec) {
  const a = audio();
  a.pause();
  a.src = rec.preview_url || "";
  a.currentTime = 0;
  a.volume = currentVolume();
  setPlayIcon(false);
  $("seek").value = 0;
  $("t-cur").textContent = "0:00";
  $("t-dur").textContent = "0:00";
}

function currentVolume() { return ($("vol").value || 0) / 100; }
function setPlayIcon(playing) { $("btn-play").textContent = playing ? "⏸" : "▶"; }
function fmtTime(s) {
  if (!isFinite(s)) return "0:00";
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

function wireAudio() {
  const a = audio();
  a.volume = DEFAULT_VOLUME;
  $("vol").value = Math.round(DEFAULT_VOLUME * 100);
  $("vol-label").textContent = Math.round(DEFAULT_VOLUME * 100) + "%";

  $("vol").oninput = (e) => {
    a.volume = e.target.value / 100;
    $("vol-label").textContent = e.target.value + "%";
  };
  $("btn-play").onclick = () => {
    if (!a.src) return;
    if (a.paused) { a.play(); setPlayIcon(true); } else { a.pause(); setPlayIcon(false); }
  };
  a.ontimeupdate = () => {
    if (a.duration) {
      $("seek").value = (a.currentTime / a.duration) * 100;
      $("t-cur").textContent = fmtTime(a.currentTime);
      $("t-dur").textContent = fmtTime(a.duration);
    }
  };
  a.onended = () => setPlayIcon(false);
  $("seek").oninput = (e) => {
    if (a.duration) a.currentTime = (e.target.value / 100) * a.duration;
  };
}

// ============================ rating (mandatory) ============================
async function rate(action) {
  const rec = current();
  if (S.busy || !rec) return;
  S.busy = true;
  const stage = $("stage");
  stage.classList.add("swipe-out");

  try {
    const res = await fetch("/api/rate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        set_id: rec.set_id, beatmap_id: rec.beatmap_id,
        genre_id: rec.genre_id, stars: rec.stars,
        bpm: rec.bpm, creator: rec.creator,
      }),
    });
    if (res.ok) {
      const data = await res.json();
      S.rated = data.rated_count ?? S.rated + 1;
    }
  } catch (_) {}

  if (S.onboarding) {
    updateProgress();
    if (S.rated >= S.target) {
      stage.classList.remove("swipe-out");
      S.busy = false;
      await finishOnboarding();
      return;
    }
  }

  S.idx++;
  if (S.idx >= S.queue.length) await loadQueue();
  stage.classList.remove("swipe-out");
  render();
  S.busy = false;
}

$("btn-like").onclick = () => rate("like");
$("btn-dislike").onclick = () => rate("dislike");

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if ($("stage").classList.contains("hidden")) return;
  if (e.key === "ArrowRight") { e.preventDefault(); rate("like"); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); rate("dislike"); }
  else if (e.code === "Space") { e.preventDefault(); $("btn-play").click(); }
});

// ============================ filters ============================
function updateRangeLabel() {
  $("range-label").textContent = S.filters.auto
    ? "auto" : `★ ${(+S.filters.min).toFixed(1)} – ${(+S.filters.max).toFixed(1)}`;
}

function readSliders() {
  let lo = parseFloat($("min-stars").value);
  let hi = parseFloat($("max-stars").value);
  if (lo > hi) [lo, hi] = [hi, lo];
  S.filters.min = lo; S.filters.max = hi;
  updateRangeLabel();
}

for (const id of ["min-stars", "max-stars"]) {
  $(id).oninput = () => {
    if ($("auto-diff").checked) { $("auto-diff").checked = false; S.filters.auto = false; }
    readSliders();
  };
}
$("auto-diff").onchange = (e) => {
  S.filters.auto = e.target.checked;
  updateRangeLabel();
};
$("apply-filters").onclick = async () => {
  S.filters.mods = $("mods").value.trim();
  S.pending = null;
  await loadQueue();
  render();
};
$("reset-history").onclick = async () => {
  await fetch("/api/reset", { method: "POST" });
  S.pending = null;
  await loadQueue();
  render();
};
$("reonboard").onclick = async () => {
  if (!confirm("Retake the taste quiz? This clears your genres and ratings.")) return;
  await fetch("/api/reonboard", { method: "POST" });
  location.reload();
};
$("logout").onclick = async () => {
  await fetch("/logout", { method: "POST" });
  location.reload();
};

// ============================ misc ============================
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

wireAudio();
updateRangeLabel();
init();

"use strict";

const $ = (id) => document.getElementById(id);
const DEFAULT_VOLUME = 0.20;          // preview always starts at 20%
const AUTOPLAY_KEY = "spotiosu.autoplay";  // off unless the user opts in
const FILTERS_KEY = "spotiosu.filtersOpen";
const UNRANKED_KEY = "spotiosu.unranked";   // off unless the user opts in

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
  autoplay: false, // off by default; toggled by the checkbox under the player
  filtersOpen: false,
  filters: { auto: true, min: 3, max: 6, mods: "", tiers: new Set(), unranked: false },
};

/* osu!'s own difficulty spectrum. Naming the bands is the whole point: a player
   knows what "Insane" means and does not know what 4.0-5.3 means. Selecting
   several chips spans them, so one control expresses what two handles did. */
const TIERS = [
  { id: "easy", min: 0, max: 2.0 },
  { id: "normal", min: 2.0, max: 2.7 },
  { id: "hard", min: 2.7, max: 4.0 },
  { id: "insane", min: 4.0, max: 5.3 },
  { id: "expert", min: 5.3, max: 6.5 },
  { id: "expertplus", min: 6.5, max: 10 },
];

try {
  S.filtersOpen = localStorage.getItem(FILTERS_KEY) === "1";
  S.filters.unranked = localStorage.getItem(UNRANKED_KEY) === "1";
} catch (_) {}

// ============================ boot ============================
async function init() {
  applyLang(detectLang());
  // Chip labels come from t(), so they have to be drawn after the language is
  // resolved - drawing them at module load left them in English.
  renderTiers();
  updateRangeLabel();
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
  renderProfileHeader();
  applyDonateLink(state.donate_url);
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
  const key = { denied: "err_denied", state: "err_state", oauth: "err_oauth" }[err];
  const el = $("login-error");
  el.textContent = t(key || "err_generic");
  el.classList.remove("hidden");
  history.replaceState({}, "", "/");
}

// Switching language re-renders the static labels, then whatever the current
// view drew dynamically (genre chips, track badges, charts).
function switchLang() {
  applyLang(LANG === "ru" ? "en" : "ru");
  renderTiers();          // chip labels are drawn by JS, not by data-i18n
  updateRangeLabel();
  if (!$("genres-view").classList.contains("hidden")) {
    const chosen = new Set(S.picked);
    showGenrePicker();
    S.picked = chosen;
    document.querySelectorAll(".genre-chip").forEach((b) => {
      if (chosen.has(Number(b.dataset.id))) b.classList.add("on");
    });
    $("genres-continue").disabled = chosen.size < 2;
  }
  if (!$("profile-view").classList.contains("hidden")) showProfile();
  else if (current()) render();
}

document.querySelectorAll(".lang-btn").forEach((b) => (b.onclick = switchLang));

function renderProfileHeader() {
  const u = S.user;
  $("avatar").src = u.avatar_url || "";
  $("username").textContent = u.username;
  const bits = [];
  if (u.pp) bits.push(`${Math.round(u.pp).toLocaleString()}pp`);
  if (u.global_rank) bits.push(`#${u.global_rank.toLocaleString()}`);
  bits.push(u.mode);
  $("stats").textContent = bits.join(" · ");
}

// The donate button only exists when a link is configured server-side, so the
// payment platform can be swapped without touching the code.
function applyDonateLink(url) {
  if (!url) return;
  const el = $("donate");
  el.href = url;
  el.classList.remove("hidden");
}

// ============================ step 1: genres ============================
function showGenrePicker() {
  $("stage-view").classList.add("hidden");
  $("profile-view").classList.add("hidden");
  $("nav").classList.add("hidden");
  $("genres-view").classList.remove("hidden");
  const grid = $("genre-grid");
  grid.innerHTML = "";
  for (const g of S.allGenres) {
    const b = document.createElement("button");
    b.className = "genre-chip";
    b.dataset.id = g.id;
    b.textContent = genreLabel(g.id, g.name);
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
  btn.innerHTML = `<span class="spinner"></span> ${t("loading_songs")}`;
  const res = await fetch("/api/onboarding/genres", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ genres: [...S.picked] }),
  });
  btn.textContent = t("continue");
  if (!res.ok) { btn.disabled = false; return; }
  startOnboardingRating();
};

// ============================ step 2: rate songs ============================
async function startOnboardingRating() {
  S.onboarding = true;
  S.queue = []; S.idx = 0; S.pending = null;
  $("genres-view").classList.add("hidden");
  $("profile-view").classList.add("hidden");
  $("nav").classList.add("hidden");   // no wandering off mid-questionnaire
  $("stage-view").classList.remove("hidden");
  $("onboard-bar").classList.remove("hidden");
  $("filters").classList.add("hidden");
  $("filters-toggle").classList.add("hidden");  // no tuning mid-questionnaire
  updateProgress();
  await loadQueue();
  render();
}

function updateProgress() {
  const pct = Math.min(1, S.rated / S.target);
  // scaleX, not width: animating width relayouts the bar on every frame.
  $("ob-fill").style.transform = `scaleX(${pct})`;
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
  $("profile-view").classList.add("hidden");
  $("nav").classList.remove("hidden");
  $("stage-view").classList.remove("hidden");
  $("onboard-bar").classList.add("hidden");
  $("filters-toggle").classList.remove("hidden");
  applyFiltersOpen(S.filtersOpen);
  await loadQueue();
  render();
}

/* Filters are a tool, not the headline, so they start collapsed and the choice
   is remembered. The toggle carries a summary so the state is legible closed. */
function applyFiltersOpen(open) {
  S.filtersOpen = open;
  $("filters").classList.toggle("hidden", !open);
  $("filters-toggle").setAttribute("aria-expanded", open ? "true" : "false");
  try { localStorage.setItem(FILTERS_KEY, open ? "1" : "0"); } catch (_) {}
  updateFiltersSummary();
}

function updateFiltersSummary() {
  const band = tierBand();
  const bits = [band ? `★ ${band.min.toFixed(1)}–${band.max.toFixed(1)}` : t("f_auto_value")];
  if (S.filters.mods) bits.push("+" + S.filters.mods);
  // Worth surfacing while collapsed: it changes what the whole feed is made of.
  if (S.filters.unranked) bits.push(t("f_unranked_short"));
  $("filters-summary").textContent = "· " + bits.join(" · ");
}

// ============================ queue ============================
function feedUrl() {
  const p = new URLSearchParams({ count: "10" });
  if (S.filters.mods) p.set("mods", S.filters.mods);
  if (S.filters.unranked) p.set("unranked", "1");
  const band = tierBand();
  if (band) {
    p.set("min_stars", band.min);
    p.set("max_stars", band.max);
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
  S.filters.min = Math.round(Math.max(0, Math.min(10, sug.min)) * 10) / 10;
  S.filters.max = Math.round(Math.max(0, Math.min(10, sug.max)) * 10) / 10;
  updateRangeLabel();
}

async function loadQueue() {
  showLoading(true);
  let items = [], failed = false;
  try {
    items = S.pending ? await S.pending : await fetchBatch();
  } catch (_) { failed = true; }
  S.pending = null;
  S.queue = items;
  S.idx = 0;
  showLoading(false);
  // A network failure is not the same as "nothing matched your filters".
  if (failed) showEmpty(t("load_error"));
  else if (!items.length) showEmpty();
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

function showEmpty(msg) {
  $("stage").classList.add("hidden");
  const el = $("stage-empty");
  el.textContent = msg || (S.onboarding ? t("empty_onboarding") : t("empty_feed"));
  el.classList.remove("hidden");
}

function render() {
  const rec = current();
  if (!rec) { showEmpty(); return; }
  $("stage-empty").classList.add("hidden");
  $("stage").classList.remove("hidden");

  // Searching the whole listing turns up sets whose cover 404s, so the image is
  // shown only once it has actually decoded - otherwise the frame keeps its own
  // surface and the title still reads, instead of a broken-image icon.
  const art = $("art");
  art.classList.add("hidden");
  if (rec.cover_url) art.src = rec.cover_url;
  else art.removeAttribute("src");

  const bg = $("stage-bg");
  bg.style.backgroundImage = rec.cover_url ? `url("${rec.cover_url}")` : "none";
  bg.classList.toggle("on", Boolean(rec.cover_url));
  $("t-title").textContent = rec.title;
  $("t-artist").textContent = rec.artist;
  const sub = [`[${rec.version}]`, `${t("mapped_by")} ${rec.creator}`];
  $("t-sub").textContent = sub.join(" · ");
  renderBadges(rec);

  $("open-osu").href = rec.url;
  $("open-osu").onclick = (e) => openInOsu(e, rec);
  loadPreview(rec);

  ensurePp(rec);      // fills the pp badge in once it arrives
  ensureAccent(rec);  // recolours the interface from this cover
  preloadNext();      // cover, pp and accent of the following track
  maybePrefetch();
}

/* The interface takes its colour from the current artwork. Extraction happens
   server-side because the osu! CDN sends no CORS header, which makes a canvas
   that has drawn a cover unreadable. Cached and prefetched exactly like pp. */
const BRAND = { accent: "#ff66aa", accent_text: "#ff66aa", on_accent: "#14060d" };

function paint(colours) {
  const root = document.documentElement.style;
  root.setProperty("--accent", colours.accent);
  root.setProperty("--accent-text", colours.accent_text);
  root.setProperty("--on-accent", colours.on_accent);
}

async function ensureAccent(rec) {
  if (!rec) return;
  if (rec.accent) { if (current() === rec) paint(rec.accent); return; }
  if (rec._accentPending) return;
  rec._accentPending = true;
  try {
    const res = await fetch(`/api/accent?set_id=${rec.set_id}`);
    rec.accent = res.ok ? await res.json() : BRAND;
  } catch (_) {
    rec.accent = BRAND;
  } finally {
    rec._accentPending = false;
  }
  // The user may have rated, or switched to the profile, while this was in
  // flight - the profile deliberately does not wear a track's colour.
  if (current() === rec && onPlayer()) paint(rec.accent);
}

function onPlayer() {
  return !$("stage-view").classList.contains("hidden");
}

/* Whether a map is ranked changes what playing it is worth, so it is stated on
   the card rather than left to be guessed from the pp figure. */
function statusBadge(status) {
  if (!status) return "";
  const known = ["ranked", "approved", "qualified", "loved", "pending", "wip", "graveyard"];
  const id = known.includes(status) ? status : "";
  if (!id) return "";
  const lesser = id === "graveyard" || id === "pending" || id === "wip";
  return `<span class="badge status st-${id}${lesser ? " lesser" : ""}">` +
         `${esc(t("status_" + id))}</span>`;
}

function renderBadges(rec) {
  const b = [`<span class="badge stars">★ ${rec.stars.toFixed(2)}</span>`];
  const st = statusBadge(rec.status);
  if (st) b.push(st);
  if (rec.genre_name) {
    b.push(`<span class="badge genre">${esc(genreLabel(rec.genre_id, rec.genre_name))}</span>`);
  }
  if (rec.bpm) b.push(`<span class="badge">${Math.round(rec.bpm)} BPM</span>`);
  if (rec.length_str) b.push(`<span class="badge">${rec.length_str}</span>`);
  if (rec.mods && rec.mods.length) b.push(`<span class="badge">+${rec.mods.join("")}</span>`);
  if (rec.pp && rec.pp["100"] != null) {
    b.push(`<span class="badge pp">${Math.round(rec.pp["100"])}pp @100%</span>`);
  } else if (rec.pp === undefined) {
    b.push(`<span class="badge pp pp-loading">pp…</span>`);
  }
  $("t-badges").innerHTML = b.join("");
}

/* pp costs a .osu download server-side, so it is fetched per track instead of
   for the whole batch. `rec.pp === undefined` means "not asked yet", `{}` means
   "asked, unavailable" - so a failed lookup does not retry forever. */
async function ensurePp(rec) {
  if (!rec || rec.pp !== undefined || rec._ppPending) return;
  rec._ppPending = true;
  try {
    const mods = rec.mods && rec.mods.length ? `&mods=${rec.mods.join("")}` : "";
    const res = await fetch(`/api/pp?beatmap_id=${rec.beatmap_id}${mods}`);
    rec.pp = res.ok ? (await res.json()).pp || {} : {};
  } catch (_) {
    rec.pp = {};
  } finally {
    rec._ppPending = false;
  }
  // The user may have rated and moved on while this was in flight.
  if (current() === rec) renderBadges(rec);
}

function preloadNext() {
  const next = S.queue[S.idx + 1];
  if (!next) return;
  if (next.cover_url) new Image().src = next.cover_url;
  ensurePp(next);
  ensureAccent(next);
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

// Load the preview. It only starts on its own if the user enabled autoplay;
// otherwise they press play.
function loadPreview(rec) {
  const a = audio();
  a.pause();
  a.src = rec.preview_url || "";
  a.currentTime = 0;
  a.volume = currentVolume();
  $("seek").value = 0;
  $("t-cur").textContent = "0:00";
  $("t-dur").textContent = "0:00";

  if (S.autoplay && a.src) {
    // The browser may still refuse without a gesture; the play button always works.
    a.play().then(() => setPlayIcon(true)).catch(() => setPlayIcon(false));
  } else {
    setPlayIcon(false);
  }
}

function currentVolume() { return ($("vol").value || 0) / 100; }
function setPlayIcon(playing) {
  $("icon-play").classList.toggle("hidden", playing);
  $("icon-pause").classList.toggle("hidden", !playing);
}
function fmtTime(s) {
  if (!isFinite(s)) return "0:00";
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

function wireArt() {
  const art = $("art");
  art.onload = () => art.classList.remove("hidden");
  art.onerror = () => {
    art.classList.add("hidden");
    $("stage-bg").classList.remove("on");   // the backdrop uses the same file
  };
}

function wireAudio() {
  const a = audio();
  a.volume = DEFAULT_VOLUME;
  $("vol").value = Math.round(DEFAULT_VOLUME * 100);
  $("vol-label").textContent = Math.round(DEFAULT_VOLUME * 100) + "%";

  // Autoplay is off by default; the user's choice is remembered.
  let saved = null;
  try { saved = localStorage.getItem(AUTOPLAY_KEY); } catch (_) {}
  S.autoplay = saved === "1";
  $("autoplay").checked = S.autoplay;
  $("autoplay").onchange = (e) => {
    S.autoplay = e.target.checked;
    try { localStorage.setItem(AUTOPLAY_KEY, S.autoplay ? "1" : "0"); } catch (_) {}
    // Turning it on should start the track you are looking at right now.
    if (S.autoplay && a.src && a.paused) {
      a.play().then(() => setPlayIcon(true)).catch(() => {});
    }
  };

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
        title: rec.title, artist: rec.artist, cover_url: rec.cover_url,
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
function tierBand() {
  const picked = TIERS.filter((x) => S.filters.tiers.has(x.id));
  if (!picked.length) return null;
  return {
    min: Math.min(...picked.map((x) => x.min)),
    max: Math.max(...picked.map((x) => x.max)),
  };
}

function updateRangeLabel() {
  const band = tierBand();
  // Even on auto the resolved band is worth showing - "auto" alone tells the
  // user nothing about what they are going to be served.
  $("range-label").textContent = band
    ? `★ ${band.min.toFixed(1)} – ${band.max.toFixed(1)}`
    : `${t("f_auto_value")} · ★ ${(+S.filters.min).toFixed(1)} – ${(+S.filters.max).toFixed(1)}`;
  updateFiltersSummary();
}

function renderTiers() {
  const row = $("tier-row");
  row.innerHTML = "";
  const add = (label, on, onclick, title) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tier-chip" + (on ? " on" : "");
    b.textContent = label;
    if (title) b.title = title;
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.onclick = onclick;
    row.appendChild(b);
  };

  add(t("f_auto"), S.filters.auto, () => {
    if (S.filters.auto) return;          // already on; nothing to reload
    S.filters.tiers.clear();
    S.filters.auto = true;
    renderTiers();
    updateRangeLabel();
    scheduleApply();
  });

  for (const tier of TIERS) {
    const on = S.filters.tiers.has(tier.id);
    add(t("tier_" + tier.id), on, () => {
      if (on) S.filters.tiers.delete(tier.id);
      else S.filters.tiers.add(tier.id);
      S.filters.auto = S.filters.tiers.size === 0;
      renderTiers();
      updateRangeLabel();
      scheduleApply();
    }, `★ ${tier.min.toFixed(1)} – ${tier.max.toFixed(1)}`);
  }
}

$("filters-toggle").onclick = () => applyFiltersOpen(!S.filtersOpen);

/* Choosing a filter should change the feed, not arm a button. The delay only
   exists so that picking two adjacent tiers is one reload instead of two. */
let applyTimer = null;
function scheduleApply(delay = 550) {
  clearTimeout(applyTimer);
  applyTimer = setTimeout(applyFilters, delay);
}

async function applyFilters() {
  clearTimeout(applyTimer);
  S.filters.mods = $("mods").value.trim();
  S.pending = null;
  updateFiltersSummary();
  await loadQueue();
  render();
}

$("unranked").onchange = (e) => {
  S.filters.unranked = e.target.checked;
  try { localStorage.setItem(UNRANKED_KEY, S.filters.unranked ? "1" : "0"); } catch (_) {}
  updateFiltersSummary();
  scheduleApply(0);
};

// Typing mods commits on Enter or when the field loses focus, not per keystroke.
$("mods").onchange = () => scheduleApply(0);
$("mods").onkeydown = (e) => { if (e.key === "Enter") applyFilters(); };

$("apply-filters").onclick = applyFilters;
$("reset-history").onclick = async () => {
  await fetch("/api/reset", { method: "POST" });
  S.pending = null;
  await loadQueue();
  render();
};
$("reonboard").onclick = async () => {
  if (!confirm(t("retake_confirm"))) return;
  await fetch("/api/reonboard", { method: "POST" });
  location.reload();
};
$("logout").onclick = async () => {
  await fetch("/logout", { method: "POST" });
  location.reload();
};

// ============================ profile ============================
function showPlayer() {
  $("nav-player").classList.add("on");
  $("nav-profile").classList.remove("on");
  $("profile-view").classList.add("hidden");
  $("stage-view").classList.remove("hidden");
  paint(current()?.accent || BRAND);
  $("stage-bg").classList.toggle("on", Boolean(current()?.cover_url));
}

async function showProfile() {
  audio().pause();
  setPlayIcon(false);
  $("nav-profile").classList.add("on");
  $("nav-player").classList.remove("on");
  $("stage-view").classList.add("hidden");
  $("genres-view").classList.add("hidden");
  $("profile-view").classList.remove("hidden");
  // This page is about a taste, not about one map, so it drops the track's
  // colour and the artwork behind it.
  paint(BRAND);
  $("stage-bg").classList.remove("on");

  let data;
  try {
    const res = await fetch("/api/profile/stats");
    if (res.status === 401) { location.reload(); return; }
    data = await res.json();
  } catch (_) { return; }
  renderProfile(data);
}

function renderProfile(d) {
  // NB: never name a local `t` in this file - it would shadow the global
  // translation function t() from i18n.js.
  const tot = d.totals || {};
  $("t-rated").textContent = tot.rated ?? 0;
  $("t-liked").textContent = tot.liked ?? 0;
  $("t-rate").textContent = (tot.like_rate ?? 0) + "%";
  $("t-bpm").textContent = tot.avg_bpm || "—";

  $("taste-line").textContent = tasteLine(d.summary);
  const svt = d.skill_vs_taste || {};
  $("taste-sub").textContent = svt.verdict
    ? t("taste_sub", {
        skill: svt.skill_stars, taste: svt.taste_stars,
        verdict: t("verdict_" + svt.verdict),
      })
    : "";

  const empty = !tot.rated;
  $("profile-empty").classList.toggle("hidden", !empty);
  document.querySelectorAll(".chart-block, .stat-row")
    .forEach((c) => c.classList.toggle("hidden", empty));
  if (empty) return;

  renderGenreChart(d.genres || []);
  renderBarChart($("diff-chart"), (d.difficulty || []).map((b) => ({
    label: b.star.toFixed(1) + "★", value: b.count,
  })), t("unit_liked_maps"));
  renderBarChart($("mapper-chart"), (d.mappers || []).map((m) => ({
    label: m.name, value: m.count,
  })), t("unit_liked_maps"));
  renderRecent(d.recent_likes || []);
}

// The server sends the pieces, not a sentence, so each language can phrase it
// with its own word order.
function tasteLine(s) {
  if (!s) return t("taste_none");
  return t("taste_line", {
    tempo: t("tempo_" + s.tempo),
    genre: s.genre_id ? genreLabel(s.genre_id, s.genre_name) : t("taste_maps"),
    stars: Number(s.stars).toFixed(1),
    bpm: s.bpm,
  });
}

// Bar width: a zero value gets no sliver, everything else stays visible.
function barWidth(value, max) {
  if (!value) return "width:0;min-width:0";
  return `width:${Math.max(2, (value / max) * 100)}%`;
}

function renderGenreChart(genres) {
  const el = $("genre-chart");
  el.innerHTML = "";
  if (!genres.length) {
    el.innerHTML = `<p class="chart-note">${t("no_genre_data")}</p>`;
    return;
  }
  const max = Math.max(...genres.flatMap((g) => [g.liked, g.disliked]), 1);
  for (const g of genres) {
    const label = genreLabel(g.id, g.name);
    const row = document.createElement("div");
    row.className = "drow";
    row.innerHTML =
      `<div class="row-label" title="${esc(label)}">${esc(label)}</div>` +
      `<div class="dtrack">` +
        `<div class="dside left"><div class="dbar neg" style="${barWidth(g.disliked, max)}" ` +
          `title="${g.disliked} ${esc(t("unit_disliked"))}"></div></div>` +
        `<div class="dzero"></div>` +
        `<div class="dside"><div class="dbar pos" style="${barWidth(g.liked, max)}" ` +
          `title="${g.liked} ${esc(t("unit_liked"))}"></div></div>` +
      `</div>` +
      `<div class="row-value">${g.liked} / ${g.disliked}</div>`;
    el.appendChild(row);
  }
}

function renderBarChart(el, items, unit) {
  el.innerHTML = "";
  if (!items.length) { el.innerHTML = `<p class="chart-note">${t("nothing_yet")}</p>`; return; }
  const max = Math.max(...items.map((i) => i.value), 1);
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML =
      `<div class="row-label" title="${esc(it.label)}">${esc(it.label)}</div>` +
      `<div class="row-track"><div class="row-bar" style="${barWidth(it.value, max)}" ` +
        `title="${it.value} ${unit}"></div></div>` +
      `<div class="row-value">${it.value}</div>`;
    el.appendChild(row);
  }
}

function renderRecent(items) {
  const el = $("recent-likes");
  el.innerHTML = "";
  if (!items.length) {
    el.innerHTML = `<p class="chart-note">${t("recent_empty")}</p>`;
    return;
  }
  for (const it of items) {
    const a = document.createElement("a");
    a.className = "recent-item";
    a.href = it.url; a.target = "_blank"; a.rel = "noopener";
    a.innerHTML =
      `<img loading="lazy" src="${esc(it.cover_url)}" alt="" />` +
      `<div class="recent-title" title="${esc(it.artist)} - ${esc(it.title)}">${esc(it.title)}</div>` +
      `<div class="recent-sub">${esc(it.artist)} · ★${it.stars.toFixed(2)}</div>`;
    el.appendChild(a);
  }
}

$("nav-player").onclick = showPlayer;
$("nav-profile").onclick = showProfile;

// ============================ misc ============================
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

wireArt();
wireAudio();
$("unranked").checked = S.filters.unranked;
init();   // draws the tier chips once the language is known

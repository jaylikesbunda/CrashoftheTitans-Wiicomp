"use strict";
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const $ = (id) => document.getElementById(id);

let state = null;      // StateView from the backend
let iso = null;        // IsoInfo of the chosen disc
let preparing = false;
let buildingRuntime = false;
let padTimer = null;
let page = "welcome";
let lastMain = "welcome";  // wizard page to return to from Settings/Controls/Log/About
let buildProgressBase = 2;
const WIZARD = ["disc", "prepare", "build", "play"];
const UTIL = ["music", "episodes", "states", "settings", "controls", "logs", "about"];

const MODE_NOTES = {
  2: "Gamepad emulating the Wii remote and Nunchuk: left stick moves, right stick points.",
  3: "Like Classic, with the pointer helped by the right stick.",
  4: "Pointer follows the controller's gyro when it has one (DualShock/DualSense/Switch Pro); otherwise the right stick.",
  6: "Keyboard for movement, mouse as the pointer.",
};

function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg; t.className = "toast" + (err ? " err" : ""); t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), err ? 6000 : 2600);
}
const fmtBytes = (n) => (n > 1e9 ? (n / 1e9).toFixed(2) + " GB" : (n / 1e6).toFixed(0) + " MB");

// ---------------------------------------------------------------- pages
function go(p) {
  page = p;
  if (!UTIL.includes(p)) lastMain = p;
  document.querySelectorAll(".page").forEach((el) => el.classList.toggle("active", el.id === "page-" + p));
  const idx = WIZARD.indexOf(p);
  $("dots").hidden = idx < 0;
  document.querySelectorAll("#dots span").forEach((sp) => {
    const i = WIZARD.indexOf(sp.dataset.s);
    sp.classList.toggle("on", i === idx);
    sp.classList.toggle("done", i < idx);
  });
  clearInterval(padTimer);
  if (p !== "controls") endCapture(true);
  if (p === "controls") { if (!setup) fillControls(); else renderControls(); pollPads(); padTimer = setInterval(pollPads, 50); }
  if (p === "logs") loadLog();
  if (p === "music") loadMusic();
  if (p === "states" || p === "play") loadStates();
  syncMini();
  document.querySelector("main").scrollTop = 0;
}
document.querySelectorAll("[data-util]").forEach((b) => (b.onclick = () => go(page === b.dataset.util ? lastMain : b.dataset.util)));
document.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => go(lastMain)));

const runtimeReady = () => state.runtime_ready || (state.runtime_embedded && !state.settings.runtime_override);
// The page a returning user should land on, given what is already done.
function homePage() {
  if (!state.game_ready) return state.settings.iso_path ? "disc" : "welcome";
  return runtimeReady() ? "play" : "build";
}

// ---------------------------------------------------------------- state
async function refresh() {
  state = await invoke("get_state");
  $("ver").textContent = "v" + state.version;
  $("dataDir").textContent = state.data_dir;
  $("runtimePill").textContent = "Runtime: " + (runtimeReady() ? state.runtime_label : "not built yet");

  const busy = preparing || buildingRuntime || state.building;
  // disc page
  $("prepare").hidden = !(iso && iso.supported) || busy;
  $("prepareLabel").textContent = state.game_ready ? "Re-prepare" : "Continue";
  $("pickIso").disabled = busy || state.running;
  $("skipPrepared").hidden = !state.game_ready;
  // build page
  const canBuild = state.builder_available && state.game_ready && !state.runtime_embedded;
  if (!buildingRuntime) {
    $("runtimeText").textContent = state.runtime_embedded
      ? "Private build: the runtime is included in this launcher and unpacks itself on first play."
      : canBuild
        ? state.builder_pack_downloadable
          ? "The launcher will download its verified Windows build tools, then compile the game from your disc. This runs once and can take a while."
          : "This compiles the game for your PC from your disc. It runs once and can take a while; you can leave the launcher open in the background."
        : "The game builder is not available in this install. Set a runtime in Settings under Files.";
  }
  $("buildRuntime").hidden = !canBuild || buildingRuntime || state.building;
  $("buildLabel").textContent = state.runtime_ready ? "Rebuild" : "Start build";
  $("buildBack").hidden = buildingRuntime || state.building;
  // play page
  const canPlay = state.game_ready && runtimeReady() && !busy && !state.running;
  $("play").disabled = !canPlay;
  $("stop").hidden = !state.running;
  $("play").hidden = state.running;
  $("changeDisc").disabled = $("rebuild").disabled = busy || state.running;
  $("rebuild").hidden = !canBuild;
  $("playHint").textContent = state.running ? "Game is running." : "";
  $("ctrlLive").hidden = !state.running;
  syncChapters();
  syncLogo();
  updateResume();
  fillSettings();
}

// ---------------------------------------------------------------- settings sections
// One section of the settings shows at a time; the list on the left switches between them.
document.querySelectorAll("#snav [data-sec]").forEach((b) => (b.onclick = () => {
  document.querySelectorAll("#snav [data-sec]").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll("#page-settings .group[data-sec]").forEach((g) => (g.hidden = g.dataset.sec !== b.dataset.sec));
  document.querySelector("main").scrollTop = 0;
}));

// ---------------------------------------------------------------- episodes
// The Episodes page starts the game at an episode the game numbers one after another across worlds (L2_E1 is Episode 5). Play on
// the main page never uses this: it always starts normally.
// What the shipped test maps actually contain (checked by loading each one in the game).
const TEST_LEVEL_HINT = { CombatArena01: "combat arena", SoundTest: "empty room, no scenery", AI_pathfind: "no floor: you fall" };
let episodesLoaded = false;
async function syncChapters() {
  const host = $("episodeList");
  if (!episodesLoaded) {
    try {
      const list = await invoke("list_chapters");
      const worlds = [...new Set(list.filter((c) => !c.dev).map((c) => c.world))];
      const dev = list.filter((c) => c.dev);
      host.innerHTML = worlds.map((w) => `<h3 class="episodes-world">World ${w}</h3><div class="episodes">` +
        list.filter((c) => !c.dev && c.world === w).map((c) => `<button class="btn episode" data-ep="${esc(c.id)}"><b>Episode ${c.number}</b><small>${esc(c.id)}</small></button>`).join("") + "</div>").join("") +
        (dev.length ? `<h3 class="episodes-world">Test levels</h3><p class="note">Unfinished test maps that shipped on this disc but are not reachable from the game's menus. They always open straight in (the switch above does not apply). Most have little or no world geometry, and none has a proper exit.</p><div class="episodes">` +
          dev.map((c) => `<button class="btn episode dev" data-ep="${esc(c.id)}"><b>${esc(c.id)}</b><small>${esc(TEST_LEVEL_HINT[c.id] || "test level")}</small></button>`).join("") + "</div>" : "");
      host.querySelectorAll("[data-ep]").forEach((b) => (b.onclick = () => startGame(null, b.dataset.ep)));
      episodesLoaded = true;
    } catch { return; }
  }
  const blocked = !state.game_ready || !runtimeReady() || !!state.running;
  host.querySelectorAll("[data-ep]").forEach((b) => (b.disabled = blocked));
  $("direct").checked = state.settings.start_direct !== false;
  $("direct").disabled = !!state.running;
}

$("direct").onchange = async () => {
  const v = $("direct").checked;
  try { await invoke("set_start_direct", { direct: v }); state.settings.start_direct = v; }
  catch (e) { $("direct").checked = !v; toast(String(e), true); }
};

// ---------------------------------------------------------------- logo
// The logo is composed from the player's own disc when the game is prepared; until then only the WIICOMP wordmark shows.
let logoUrl = null;
function setLogo(url) {
  for (const img of document.querySelectorAll(".big-logo, .mid-logo")) { img.src = url || "images/wiicomp.png"; img.classList.toggle("wordmark", !url); }
}
async function syncLogo() {
  if (!state.game_ready) { if (logoUrl) { URL.revokeObjectURL(logoUrl); logoUrl = null; } setLogo(null); return; }
  if (logoUrl) return;
  try { logoUrl = URL.createObjectURL(new Blob([await invoke("get_logo")], { type: "image/png" })); setLogo(logoUrl); }
  catch { setLogo(null); }
}
function resetLogo() { if (logoUrl) URL.revokeObjectURL(logoUrl); logoUrl = null; }

// ---------------------------------------------------------------- ISO
function showIso(info) {
  iso = info;
  const meta = $("isoMeta"); meta.hidden = false; meta.replaceChildren();
  const chip = (t, bad) => { const s = document.createElement("span"); s.textContent = t; if (bad) s.className = "bad"; meta.append(s); };
  chip(info.title || "Unknown title"); chip(info.game_id, !info.supported); chip(info.format || "ISO"); chip(fmtBytes(info.size));
  $("isoText").textContent = info.message;
  refresh();
}
$("getStarted").onclick = () => go("disc");
$("pickIso").onclick = async () => {
  try { const info = await invoke("pick_iso"); if (info) { showIso(info); const s = collect(); s.iso_path = info.path; await invoke("save_settings", { settings: s }); } }
  catch (e) { toast(String(e), true); }
};
$("skipPrepared").onclick = () => go(runtimeReady() ? "play" : "build");
$("changeDisc").onclick = () => go("disc");

$("prepare").onclick = async () => {
  if (!iso) return;
  preparing = true;
  $("prepTitle").textContent = "Preparing your game";
  $("prepLead").textContent = "Extracting files from your disc and applying the title art. Keep the launcher open.";
  $("prepBack").hidden = true; $("prepFill").style.width = "0%"; $("prepText").textContent = "Reading disc…";
  go("prepare");
  await refresh();
  let ok = false;
  try { await invoke("prepare_game", { iso: iso.path }); ok = true; tracks = null; resetLogo(); }
  catch (e) { $("prepTitle").textContent = "Something went wrong"; $("prepLead").textContent = String(e); $("prepText").textContent = ""; $("prepBack").hidden = false; toast(String(e), true); }
  preparing = false;
  await refresh();
  if (!ok) return;
  if (runtimeReady()) go("play");
  else { go("build"); if (state.builder_available) startBuild(); }
};
$("prepBack").onclick = () => go("disc");

// ---------------------------------------------------------------- runtime build
async function startBuild() {
  if (buildingRuntime) return;
  buildingRuntime = true;
  buildProgressBase = state.builder_pack_downloadable ? 45 : 2;
  $("buildTitle").textContent = "Building the native game";
  $("runtimeText").textContent = "Compiling the game for your PC. This runs once and can take a while.";
  $("buildBar").hidden = false; $("buildText").hidden = false;
  $("buildFill").style.width = "0.5%"; $("buildText").textContent = "Starting the build…";
  go("build");
  await refresh();
  try { await invoke("build_runtime"); buildingRuntime = false; await refresh(); toast("Your game is ready."); go("play"); return; }
  catch (e) { $("buildTitle").textContent = "The build did not finish"; $("runtimeText").textContent = String(e) + " The Log page has the full output."; $("buildBar").hidden = true; $("buildText").hidden = true; }
  buildingRuntime = false;
  await refresh();
}
$("buildRuntime").onclick = startBuild;
$("rebuild").onclick = startBuild;
$("buildBack").onclick = () => go(state.game_ready ? "play" : "disc");
listen("runtime-build-log", (e) => {
  let progress = null;
  for (const { line } of e.payload) {
    appendLog("[builder] " + line);
    // The builder reports "@progress|<percent>|<what it is doing>"; everything else only goes to the Log page.
    if (line.startsWith("@progress|")) progress = line;   // only the newest of a batch is shown
  }
  if (!progress) return;
  const [, pct, text] = progress.split("|");
  const value = Math.max(0, Math.min(100, parseFloat(pct) || 0));
  $("buildBar").hidden = false; $("buildText").hidden = false;
  $("buildFill").style.width = value.toFixed(1) + "%";
  $("buildText").textContent = Math.floor(value) + "%  " + (text || "");
});
listen("prepare-progress", (e) => {
  const { done, total, file, phase } = e.payload;
  const pct = total ? Math.min(100, (done / total) * 100) : 0;
  $("prepFill").style.width = pct.toFixed(1) + "%";
  $("prepText").textContent = phase === "finish" ? "Verifying…" : `${pct.toFixed(0)}% · ${fmtBytes(done)} of ${fmtBytes(total)} · ${file}`;
});

// ---------------------------------------------------------------- soundtrack
let tracks = null;        // all music and ambience tracks from the prepared game files
let trackCat = "music";
let nowPlaying = null;
let trackUrl = null;
const audio = $("audio");
const fmtTime = (s) => { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };

async function loadMusic() {
  if (tracks) { if (renderTracks.cat !== trackCat) renderTracks(); else markPlaying(); return; }
  $("musicNote").textContent = "Reading the soundtrack from your game files…";
  $("trackList").replaceChildren();
  try { tracks = await invoke("list_soundtrack"); $("musicNote").textContent = ""; renderTracks(); }
  catch (e) { tracks = null; $("musicNote").textContent = String(e); }
}
function visibleTracks() {
  if (trackCat === "favourites") return (tracks || []).filter((t) => t.favourite != null).sort((a, b) => a.favourite - b.favourite);
  return (tracks || []).filter((t) => t.category === trackCat);
}
const CAT_LABEL = { favourites: "Deki's favourites", music: "music", ambience: "ambience" };
function renderTracks() {
  const list = visibleTracks();
  renderTracks.cat = trackCat;
  $("exportAll").textContent = "Export " + (trackCat === "favourites" ? "favourites" : "all " + CAT_LABEL[trackCat]) + " (" + list.length + ")";
  setSeg("mcat", trackCat);
  $("trackList").replaceChildren(...list.map((t) => {
    const row = document.createElement("div");
    row.className = "trk" + (nowPlaying && nowPlaying.id === t.id ? " on" : "");
    row.dataset.id = t.id;
    row.innerHTML = '<button class="tplay" aria-label="Play"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></button>' +
      '<div class="tname"><b></b><small></small></div><span class="tdur"></span>' +
      '<button class="texp" title="Export as WAV" aria-label="Export"><svg viewBox="0 0 24 24"><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14"/></svg></button>';
    row.querySelector("b").textContent = t.name;
    row.querySelector("small").textContent = t.file;
    row.title = t.file;
    row.querySelector(".tdur").textContent = fmtTime(t.seconds);
    row.querySelector(".tplay").onclick = () => playTrack(t);
    row.querySelector(".tname").onclick = () => playTrack(t);
    row.querySelector(".texp").onclick = () => exportTracks([t.id]);
    return row;
  }));
}
function markPlaying() {   // highlight the current row without rebuilding the list
  for (const row of $("trackList").children) row.classList.toggle("on", !!nowPlaying && Number(row.dataset.id) === nowPlaying.id);
}
async function playTrack(t) {
  if (nowPlaying && nowPlaying.id === t.id && audio.src) { audio.paused ? audio.play() : audio.pause(); return; }
  nowPlaying = t;
  $("playerbar").hidden = false; $("ptitle").textContent = t.name; $("ptime").textContent = "Loading…";
  $("miniTitle").textContent = t.name; $("miniTime").textContent = "Loading…";
  markPlaying(); syncMini();
  try {
    const buf = await invoke("get_track_wav", { id: t.id });
    if (!nowPlaying || nowPlaying.id !== t.id) return;   // another track was chosen meanwhile
    if (trackUrl) URL.revokeObjectURL(trackUrl);
    trackUrl = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
    audio.src = trackUrl;
    await audio.play();
  } catch (e) { toast(String(e), true); $("ptime").textContent = ""; }
}
const PLAY_ICON = '<path d="M8 5v14l11-7z"/>', PAUSE_ICON = '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
function syncPlayer(e) {
  if (!e || e.type !== "timeupdate") {
    const icon = audio.paused ? PLAY_ICON : PAUSE_ICON;
    $("picon").innerHTML = icon; $("miniIcon").innerHTML = icon;
    $("mini").classList.toggle("paused", audio.paused);
  }
  if (audio.duration) {
    const t = fmtTime(audio.currentTime) + " / " + fmtTime(audio.duration);
    $("ptime").textContent = t; $("miniTime").textContent = t;
    $("miniProg").style.transform = `scaleX(${audio.currentTime / audio.duration})`;
    if (!syncPlayer.dragging) $("pseek").value = Math.round((audio.currentTime / audio.duration) * 1000);
  }
}
// The small now-playing pill shows on every page except the soundtrack itself while a track is loaded.
function syncMini() { $("mini").hidden = !nowPlaying || page === "music"; }
function stepTrack(delta) {
  const list = visibleTracks(); const i = list.findIndex((t) => nowPlaying && t.id === nowPlaying.id);
  if (i < 0) return;
  if (delta < 0 && audio.currentTime > 3) { audio.currentTime = 0; return; }
  const next = list[i + delta]; if (next) playTrack(next);
}
$("miniPlay").onclick = () => { if (audio.src) audio.paused ? audio.play() : audio.pause(); };
$("miniPrev").onclick = () => stepTrack(-1);
$("miniNext").onclick = () => stepTrack(1);
$("miniOpen").onclick = () => go("music");
$("miniClose").onclick = () => { audio.pause(); audio.removeAttribute("src"); audio.load(); nowPlaying = null; $("playerbar").hidden = true; markPlaying(); syncMini(); };
// One volume for the soundtrack bar and the pill, remembered between sessions.
function setVolume(v, save) {
  v = Math.max(0, Math.min(100, Number(v)));
  audio.volume = (v / 100) ** 2;   // perceptual curve
  for (const r of document.querySelectorAll(".volr")) { r.value = v; r.style.setProperty("--v", v + "%"); }
  if (save) try { localStorage.setItem("volume", String(v)); } catch { /* storage unavailable */ }
}
for (const r of document.querySelectorAll(".volr")) r.addEventListener("input", () => setVolume(r.value, true));
{ let v = 80; try { const s = localStorage.getItem("volume"); if (s !== null) v = Number(s); } catch { /* storage unavailable */ } setVolume(v, false); }
audio.addEventListener("play", syncPlayer); audio.addEventListener("pause", syncPlayer);
audio.addEventListener("timeupdate", syncPlayer); audio.addEventListener("loadedmetadata", syncPlayer);
audio.addEventListener("ended", () => {   // continue with the next track in the list
  const list = visibleTracks(); const i = list.findIndex((t) => nowPlaying && t.id === nowPlaying.id);
  if (i >= 0 && i + 1 < list.length) playTrack(list[i + 1]); else syncPlayer();
});
$("pplay").onclick = () => { if (audio.src) audio.paused ? audio.play() : audio.pause(); };
$("pseek").addEventListener("input", () => { syncPlayer.dragging = true; if (audio.duration) audio.currentTime = (Number($("pseek").value) / 1000) * audio.duration; });
$("pseek").addEventListener("change", () => { syncPlayer.dragging = false; });
$("mcat").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; trackCat = b.dataset.v; renderTracks(); });
async function exportTracks(ids) {
  $("musicNote").textContent = "Choose a folder to export to…";
  try {
    const n = await invoke("export_tracks", { ids });
    $("musicNote").textContent = n === null ? "" : "Exported " + n + (n === 1 ? " track" : " tracks") + " as WAV files.";
    if (n !== null) toast("Export finished.");
  } catch (e) { $("musicNote").textContent = String(e); toast(String(e), true); }
}
$("exportAll").onclick = () => exportTracks(visibleTracks().map((t) => t.id));
listen("export-progress", (e) => {
  const { done, total, file } = e.payload;
  $("musicNote").textContent = done < total ? "Exporting " + (done + 1) + " of " + total + ": " + file : "";
});

// ---------------------------------------------------------------- play
// `stateId` starts the game from a save state (see list_states); `episode` (from the Episodes page) starts it at that episode; with
// neither, the game boots normally.
async function startGame(stateId = null, episode = null) {
  $("play").disabled = true; $("playLabel").textContent = "Starting…";
  try {
    await flushControls();   // controls are saved live; make sure a pending edit is written before launching
    await invoke("launch_game", { stateId, episode, skipMenus: episode ? $("direct").checked : null });
    if (stateId || episode) go("play");
  }
  catch (e) { toast(String(e), true); }
  $("playLabel").textContent = "Play";
  await refresh();
  if (page === "states") loadStates();
}
$("play").onclick = () => startGame();
$("stop").onclick = async () => { await invoke("stop_game"); };
listen("game-exit", async (e) => {
  if (e.payload && e.payload !== 0) toast("The game exited with code " + e.payload + ". See the Log page.", true);
  await refresh();
  loadStates();
});
listen("state-saved", () => { loadStates(); toast("Save state stored."); });
// The game can print thousands of lines a second. Lines go into a buffer; the Log page redraws it a few times a second, and
// only while it is open (one text update, no per-line DOM work or layout).
const logEl = $("log");
const MAX_LOG_LINES = 2000;
let logLines = [];
let logTimer = null;
let autoscroll = true;
logEl.addEventListener("scroll", () => { autoscroll = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30; });
function flushLog() {
  logTimer = null;
  if (page !== "logs") return;
  logEl.textContent = logLines.length ? logLines.join("\n") + "\n" : "";
  if (autoscroll) logEl.scrollTop = logEl.scrollHeight;
}
function appendLog(line) {
  logLines.push(line);
  if (logLines.length > MAX_LOG_LINES * 1.25) logLines.splice(0, logLines.length - MAX_LOG_LINES);
  if (!logTimer && page === "logs") logTimer = setTimeout(flushLog, 100);
}
listen("game-log", (e) => { for (const { line, stream } of e.payload) appendLog(stream === "err" ? "! " + line : line); });
async function loadLog() {
  logLines = (await invoke("get_logs")).slice(-MAX_LOG_LINES);
  autoscroll = true;
  clearTimeout(logTimer); flushLog();
}
$("clearLog").onclick = () => { logLines = []; logEl.textContent = ""; };
$("copyLog").onclick = async () => { try { await navigator.clipboard.writeText(logEl.textContent); toast("Log copied."); } catch { toast("Could not copy.", true); } };

// ---------------------------------------------------------------- settings
function fillSettings() {
  if (!state || fillSettings.dirty) return;
  const s = state.settings;
  $("scale").querySelectorAll("button").forEach((b) => b.classList.toggle("on", Number(b.dataset.v) === s.resolution_scale));
  setSeg("aspect", s.widescreen ? "16:9" : "4:3"); setSeg("dmode", s.display_mode); setSeg("aniso", String(s.anisotropy));
  $("lang").value = s.language; $("prompts").value = s.button_prompts === "follow" ? "auto" : s.button_prompts || "auto"; $("speed").value = s.game_speed; $("speedV").textContent = Number(s.game_speed).toFixed(2).replace(/0$/, "") + "x";
  $("ww").value = s.window_width; $("wh").value = s.window_height;
  $("frameLimitMode").value = s.custom_fps == null ? "regional" : "custom";
  $("customFps").value = s.custom_fps ?? 120; updateFrameLimit();
  if (!$("framePacing").disabled) $("framePacing").checked = s.frame_pacing !== false;
  if (!$("cameraGlide").disabled) { $("cameraGlide").checked = !!s.camera_glide; $("cameraGlideMs").value = s.camera_glide_ms ?? 200; updateCameraGlide(); }
  $("vsync").checked = s.vsync; $("safe").checked = s.safe_gpu_sync; $("fpslog").checked = s.fps_log;
  $("perfcensus").checked = !!s.performance_census;
  $("perf_histogram").checked = !!s.performance_histogram;
  $("directfifo").checked = !!s.direct_fifo;
  $("gxbatching").value = s.gx_batching || "legacy";
  $("mode").value = String(s.input_mode); $("modeNote").textContent = MODE_NOTES[s.input_mode] || "";
  $("pointer").value = s.pointer_speed; $("gyro").value = s.gyro_sensitivity;
  $("pointerV").textContent = Number(s.pointer_speed).toFixed(1) + "×"; $("gyroV").textContent = Number(s.gyro_sensitivity).toFixed(1) + "×";
  $("rt").value = s.runtime_override || "";
  if (!setup) fillControls();
}
const setSeg = (id, v) => $(id).querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
const getSeg = (id, fallback) => document.querySelector("#" + id + " .on")?.dataset.v || fallback;
function collect() {
  const scale = Number(document.querySelector("#scale .on")?.dataset.v || 2);
  return {
    iso_path: (iso && iso.path) || state.settings.iso_path || null,
    resolution_scale: scale, window_width: Number($("ww").value) || 1280, window_height: Number($("wh").value) || 720,
    custom_fps: $("frameLimitMode").value === "custom" ? Number($("customFps").value) : null,
    frame_pacing: $("framePacing").checked,
    camera_glide: $("cameraGlide").checked, camera_glide_ms: cameraGlideDuration(),
    vsync: $("vsync").checked, input_mode: Number($("mode").value), gyro_sensitivity: Number($("gyro").value),
    pointer_speed: Number($("pointer").value), safe_gpu_sync: $("safe").checked, fps_log: $("fpslog").checked,
    performance_census: $("perfcensus").checked,
    performance_histogram: $("perf_histogram").checked,
    direct_fifo: $("directfifo").checked,
    gx_batching: $("gxbatching").value,
    runtime_override: $("rt").value.trim() || null,
    widescreen: getSeg("aspect", "16:9") === "16:9", language: $("lang").value, button_prompts: $("prompts").value, display_mode: getSeg("dmode", "windowed"),
    game_speed: Number($("speed").value) || 1, anisotropy: Number(getSeg("aniso", "1")),
    controls: setup,
    start_direct: state.settings.start_direct !== false,
  };
}
function updateFrameLimit() {
  const custom = $("frameLimitMode").value === "custom";
  $("customFps").disabled = !custom; $("speed").disabled = custom;
}
function cameraGlideDuration() {
  return Math.min(500, Math.max(100, Math.round(Number($("cameraGlideMs").value) || 200)));
}
function updateCameraGlide() {
  $("cameraGlideMs").disabled = !$("cameraGlide").checked;
}
async function applyCameraGlide() {
  const checkbox = $("cameraGlide"), input = $("cameraGlideMs");
  const enabled = checkbox.checked, milliseconds = cameraGlideDuration();
  input.value = milliseconds;
  checkbox.disabled = input.disabled = true;
  try {
    await invoke("set_camera_glide", { enabled, milliseconds });
    state.settings.camera_glide = enabled;
    state.settings.camera_glide_ms = milliseconds;
  } catch (error) {
    checkbox.checked = !!state.settings.camera_glide;
    input.value = state.settings.camera_glide_ms ?? 200;
    toast(String(error), true);
  } finally { checkbox.disabled = false; updateCameraGlide(); }
}
$("frameLimitMode").addEventListener("change", updateFrameLimit);
$("cameraGlide").addEventListener("change", applyCameraGlide);
$("cameraGlideMs").addEventListener("change", applyCameraGlide);
$("framePacing").addEventListener("change", async () => {
  const checkbox = $("framePacing"), enabled = checkbox.checked;
  checkbox.disabled = true;
  try {
    await invoke("set_frame_pacing", { enabled });
    state.settings.frame_pacing = enabled;
  } catch (error) {
    checkbox.checked = state.settings.frame_pacing !== false;
    toast(String(error), true);
  } finally { checkbox.disabled = false; }
});
const markDirty = () => { fillSettings.dirty = true; };
// These controls persist themselves through their own commands, so editing them does not leave the form dirty.
const LIVE_SETTINGS = new Set(["framePacing", "cameraGlide", "cameraGlideMs"]);
document.querySelectorAll("#page-settings input, #page-settings select").forEach((el) => { if (!LIVE_SETTINGS.has(el.id)) el.addEventListener("input", markDirty); });
$("scale").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; $("scale").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); markDirty(); });
for (const id of ["aspect", "dmode", "aniso"]) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    setSeg(id, b.dataset.v); markDirty();
    if (id === "aspect") {   // keep the window shape in step with the chosen aspect ratio
      const w = Number($("ww").value) || 1280;
      $("wh").value = Math.round((w * (b.dataset.v === "16:9" ? 9 / 16 : 3 / 4)) / 10) * 10;
    }
  });
}
$("speed").addEventListener("input", () => ($("speedV").textContent = Number($("speed").value).toFixed(2).replace(/0$/, "") + "x"));
$("mode").addEventListener("input", () => ($("modeNote").textContent = MODE_NOTES[$("mode").value] || ""));
$("pointer").addEventListener("input", () => ($("pointerV").textContent = Number($("pointer").value).toFixed(1) + "×"));
$("gyro").addEventListener("input", () => ($("gyroV").textContent = Number($("gyro").value).toFixed(1) + "×"));
$("save").onclick = async () => {
  try { await invoke("save_settings", { settings: collect() }); fillSettings.dirty = false; await refresh(); const s = $("saved"); s.textContent = "Saved"; s.classList.add("show"); setTimeout(() => s.classList.remove("show"), 1800); }
  catch (e) { toast(String(e), true); }
};
document.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => invoke("open_folder", { which: b.dataset.open }).catch((e) => toast(String(e), true))));
$("reset").onclick = async () => {
  if (!confirm("Delete the prepared game files? You can prepare them again from your ISO.")) return;
  try { await invoke("reset_game_files"); iso = null; resetLogo(); $("isoMeta").hidden = true; await refresh(); tracks = null; toast("Prepared game files deleted."); go("disc"); } catch (e) { toast(String(e), true); }
};

// ---------------------------------------------------------------- controls
// Runtime pad-input names (identical to runtime-overlays/input_mapping.h), drawn the way each controller family prints them.
// A key cap is [text, style]; styles colour face buttons like the controller does.
const PAD_CAPS = {
  playstation: { South: ["✕", "ps-x"], East: ["○", "ps-o"], West: ["□", "ps-s"], North: ["△", "ps-t"],
    LeftShoulder: ["L1"], RightShoulder: ["R1"], LeftTrigger: ["L2"], RightTrigger: ["R2"], Back: ["Create"], Start: ["Options"],
    Guide: ["PS"], LeftStick: ["L3"], RightStick: ["R3"], Misc1: ["Mute"], Touchpad: ["Touchpad"] },
  xbox: { South: ["A", "xb-a"], East: ["B", "xb-b"], West: ["X", "xb-x"], North: ["Y", "xb-y"], LeftShoulder: ["LB"], RightShoulder: ["RB"],
    LeftTrigger: ["LT"], RightTrigger: ["RT"], Back: ["View"], Start: ["Menu"], Guide: ["Xbox"], LeftStick: ["LS"], RightStick: ["RS"], Misc1: ["Share"] },
  nintendo: { South: ["B", "face"], East: ["A", "face"], West: ["Y", "face"], North: ["X", "face"], LeftShoulder: ["L"], RightShoulder: ["R"],
    LeftTrigger: ["ZL"], RightTrigger: ["ZR"], Back: ["−"], Start: ["+"], Guide: ["Home"], LeftStick: ["LS"], RightStick: ["RS"], Misc1: ["Capture"] },
  generic: { South: ["A", "face"], East: ["B", "face"], West: ["X", "face"], North: ["Y", "face"], LeftShoulder: ["L1"], RightShoulder: ["R1"],
    LeftTrigger: ["L2"], RightTrigger: ["R2"], Back: ["Back"], Start: ["Start"], Guide: ["Guide"], LeftStick: ["L3"], RightStick: ["R3"], Misc1: ["Misc"] },
};
const COMMON_CAPS = { DpadUp: ["↑", "dp"], DpadDown: ["↓", "dp"], DpadLeft: ["←", "dp"], DpadRight: ["→", "dp"],
  Touchpad: ["Pad"], Paddle1: ["P1"], Paddle2: ["P2"], Paddle3: ["P3"], Paddle4: ["P4"] };
function padFamily(name) {
  const n = (name || "").toLowerCase();
  if (/dualsense|dualshock|playstation|ps[345]|wireless controller/.test(n)) return "playstation";
  if (/xbox|xinput|x-box/.test(n)) return "xbox";
  if (/switch|pro controller|joy-con|nintendo/.test(n)) return "nintendo";
  return "generic";
}
const padCap = (name, fam) => PAD_CAPS[fam][name] || COMMON_CAPS[name] || [name];
// PlayStation face symbols are drawn, not typed, so they stay crisp at key-cap size.
const PS_SHAPES = { "ps-x": '<path d="M7 7l10 10M17 7 7 17"/>', "ps-o": '<circle cx="12" cy="12" r="5.5"/>',
  "ps-s": '<rect x="6.8" y="6.8" width="10.4" height="10.4" rx="1"/>', "ps-t": '<path d="M12 6.2 18 16.8H6z"/>' };
const capHtml = (name, fam) => {
  if (isKb(name)) return `<kbd class="key kb">${esc(kbLabel(name))}</kbd>`;
  const [t, c] = padCap(name, fam);
  const body = PS_SHAPES[c] ? `<svg viewBox="0 0 24 24" aria-label="${esc(t)}">${PS_SHAPES[c]}</svg>` : esc(t);
  return `<kbd class="key${c ? " " + c : ""}">${body}</kbd>`;
};

// Wii inputs, by the side of the drawing their binding sits on.
const WII_REMOTE = [["a", "A"], ["b", "B"], ["one", "1"], ["two", "2"], ["minus", "− Minus"], ["home", "Home"], ["plus", "+ Plus"],
  ["up", "D-pad up"], ["down", "D-pad down"], ["left", "D-pad left"], ["right", "D-pad right"]];
const WII_NUNCHUK = [["c", "C"], ["z", "Z"], ["shake_nunchuk", "Shake Nunchuk"]];
const WII_MOTION = [["shake_remote", "Shake Remote"], ["recenter", "Recentre pointer"]];
// The runtime's built-in layout (input_mapping.h default_map), used when a Wii input has no explicit binding.
const DEFAULT_BIND = { a: ["South", "Key:Space", "Mouse:Left"], b: ["East", "RightTrigger", "Mouse:Right"], one: ["West", "Key:1"],
  two: ["North", "Key:2"], plus: ["Start", "Key:Return"], minus: ["Back", "Key:Backspace"], home: ["Guide"],
  up: ["DpadUp", "Key:Up"], down: ["DpadDown", "Key:Down"], left: ["DpadLeft", "Key:Left"], right: ["DpadRight", "Key:Right"],
  c: ["LeftShoulder", "Key:Q"], z: ["RightShoulder", "LeftTrigger", "Key:Left Shift"],
  stick_up: ["Key:W"], stick_down: ["Key:S"], stick_left: ["Key:A"], stick_right: ["Key:D"],
  shake_remote: ["Key:F"], shake_nunchuk: ["Key:R"], recenter: ["Mouse:Middle"] };
// Keyboard and mouse bindings (player 1 only) share each input's list with the controller ones: "Key:<SDL key name>", "Mouse:<button>".
const KB_STICK = [["stick_up", "Stick up"], ["stick_down", "Stick down"], ["stick_left", "Stick left"], ["stick_right", "Stick right"]];
const isKb = (t) => t.startsWith("Key:") || t.startsWith("Mouse:");
let mapView = "pad";
const kbView = () => mapView === "kbm" && setup.active === "0";
// KeyboardEvent.code -> SDL key name (what the runtime looks up). Function keys are left out: the game uses them.
const KEY_NAMES = { Space: "Space", Enter: "Return", NumpadEnter: "Keypad Enter", Backspace: "Backspace", Tab: "Tab", CapsLock: "CapsLock",
  ShiftLeft: "Left Shift", ShiftRight: "Right Shift", ControlLeft: "Left Ctrl", ControlRight: "Right Ctrl", AltLeft: "Left Alt", AltRight: "Right Alt",
  ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", Insert: "Insert", Delete: "Delete", Home: "Home", End: "End",
  PageUp: "PageUp", PageDown: "PageDown", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]", Semicolon: ";", Quote: "'",
  Comma: ",", Period: ".", Slash: "/", Backquote: "`", Backslash: "\\", IntlBackslash: "\\",
  MetaLeft: "Left GUI", MetaRight: "Right GUI", ContextMenu: "Application", PrintScreen: "PrintScreen", ScrollLock: "ScrollLock", Pause: "Pause", NumLock: "Numlock",
  NumpadDivide: "Keypad /", NumpadMultiply: "Keypad *", NumpadSubtract: "Keypad -", NumpadAdd: "Keypad +", NumpadDecimal: "Keypad .", NumpadEqual: "Keypad =" };
function sdlKeyName(code) {
  if (KEY_NAMES[code]) return KEY_NAMES[code];
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  if (code.startsWith("Key") && code.length === 4) return code.slice(3);
  if (code.startsWith("Digit") && code.length === 6) return code.slice(5);
  if (code.startsWith("Numpad") && code.length === 7) return "Keypad " + code.slice(6);
  return null;
}
const KEY_SHORT = { "Space": "Space", "Return": "Enter", "Keypad Enter": "Enter", "Backspace": "Bksp", "Left Shift": "Shift", "Right Shift": "R Shift",
  "Left Ctrl": "Ctrl", "Right Ctrl": "R Ctrl", "Left Alt": "Alt", "Right Alt": "R Alt", "CapsLock": "Caps", "Up": "↑", "Down": "↓", "Left": "←",
  "Right": "→", "PageUp": "PgUp", "PageDown": "PgDn", "Delete": "Del", "Insert": "Ins" };
const MOUSE_SHORT = { Left: "LMB", Right: "RMB", Middle: "MMB", X1: "M4", X2: "M5" };
const kbLabel = (t) => t.startsWith("Mouse:") ? (MOUSE_SHORT[t.slice(6)] || t.slice(6)) : (KEY_SHORT[t.slice(4)] || t.slice(4).replace("Keypad ", "Num "));

// A Wii Remote and Nunchuk as line art, front view, drawn to the real proportions (remote about 4.2:1 with modest corner rounding, IR window
// at the top, Nunchuk a dome tapering to the grip). Parts carry data-k so they light up, highlight and start a capture like their row.
const WII_ART = `<svg class="wiiart" viewBox="0 0 300 470" aria-hidden="true">
<path class="cable" d="M232 404 C 232 452, 150 466, 96 446 L 96 394"/>
<rect class="part trig" data-k="b" x="40" y="150" width="14" height="62" rx="7"/><text class="tl dark lbl" data-k="b" x="31" y="186">B</text>
<rect class="shell" x="52" y="22" width="88" height="372" rx="20"/>
<rect class="bevel" x="56.5" y="26.5" width="79" height="363" rx="16.5"/>
<path class="ir" d="M58 31h76"/>
<circle class="pwr" cx="70" cy="50" r="7"/><path class="pwr-mark" d="M70 45.5v4M66.8 47.3a4.6 4.6 0 1 0 6.4 0"/>
<path class="dpad-shadow" d="M84 68h24a3 3 0 0 1 3 3v20h20a3 3 0 0 1 3 3v24a3 3 0 0 1-3 3h-20v20a3 3 0 0 1-3 3H84a3 3 0 0 1-3-3v-20H61a3 3 0 0 1-3-3V94a3 3 0 0 1 3-3h20V71a3 3 0 0 1 3-3z"/>
<path class="part" data-k="up" d="M84.5 92V73.5a3 3 0 0 1 3-3h17a3 3 0 0 1 3 3V92z"/>
<path class="part" data-k="down" d="M84.5 117v18.5a3 3 0 0 0 3 3h17a3 3 0 0 0 3-3V117z"/>
<path class="part" data-k="left" d="M84 93.5H65.5a3 3 0 0 0-3 3v17a3 3 0 0 0 3 3H84z"/>
<path class="part" data-k="right" d="M108 93.5h18.5a3 3 0 0 1 3 3v17a3 3 0 0 1-3 3H108z"/>
<circle class="dcore-dot" cx="96" cy="104.5" r="3.5"/>
<path class="arrow" d="M96 75.5l-3.6 5.2h7.2zM96 133.5l-3.6-5.2h7.2zM67.5 105l5.2-3.6v7.2zM124.5 105l-5.2-3.6v7.2z"/>
<circle class="ring" cx="96" cy="180" r="24"/>
<circle class="part" data-k="a" cx="96" cy="180" r="20"/><text class="tl lbl" data-k="a" x="96" y="186">A</text>
<circle class="part" data-k="minus" cx="70" cy="250" r="8.5"/><text class="tl s lbl" data-k="minus" x="70" y="254.5">−</text>
<circle class="part" data-k="home" cx="96" cy="250" r="10"/><path class="glyph lbl" data-k="home" d="M91 251.5l5-5 5 5M92.6 250.5v4.5h6.8v-4.5"/>
<circle class="part" data-k="plus" cx="122" cy="250" r="8.5"/><text class="tl s lbl" data-k="plus" x="122" y="254.5">+</text>
<g class="holes">${[0, 1, 2, 3, 4, 5].map((i) => `<circle cx="${84 + (i % 3) * 12}" cy="${280 + Math.floor(i / 3) * 9}" r="2"/>`).join("")}</g>
<circle class="part" data-k="one" cx="96" cy="316" r="14"/><text class="tl lbl" data-k="one" x="96" y="321.5">1</text>
<circle class="part" data-k="two" cx="96" cy="350" r="14"/><text class="tl lbl" data-k="two" x="96" y="355.5">2</text>
<text class="logo" x="96" y="377">Wii</text>
<g id="artLeds">${[0, 1, 2, 3].map((i) => `<rect class="led" x="${75 + i * 12}" y="383" width="6" height="3.4" rx="1.2"/>`).join("")}</g>
<path class="shell" d="M232 116 C 279 116 296 146 295 186 C 294 228 280 254 271 290 C 264 322 263 362 252 388 C 246 402 218 402 212 388 C 201 362 200 322 193 290 C 184 254 170 228 169 186 C 168 146 185 116 232 116 Z"/>
<path class="bevel" d="M232 121 C 275 121 290 149 289.5 186 C 288.5 226 275 251 266 287 C 259 319 258 358 248 383 C 243 395 221 395 216 383 C 206 358 205 319 198 287 C 189 251 175.5 226 174.5 186 C 174 149 189 121 232 121 Z"/>
<circle class="stickwell" cx="232" cy="168" r="33"/>
<circle class="stickglow" id="stickGlow" cx="232" cy="168" r="26"/>
<g id="stickKnob"><circle class="stickknob" cx="232" cy="168" r="20"/><circle class="stickdimple" cx="232" cy="168" r="12"/></g>
<rect class="part" data-k="c" x="217" y="220" width="30" height="20" rx="10"/><text class="tl s lbl" data-k="c" x="232" y="235">C</text>
<rect class="part" data-k="z" x="205" y="250" width="54" height="32" rx="12"/><text class="tl lbl" data-k="z" x="232" y="272">Z</text>
</svg>`;

let setup = null;        // ControlSetup being edited: { active, pads }
let padsList = [];       // latest list_gamepads result
let padsKey = "";        // identity of the connected set, to rebuild the player strip only when it changes
let ctrlTimer = null;    // debounce for live save
let capturing = null;    // { key, add, timer, started } while waiting for a button press

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const defaultLayout = () => ({ bindings: {}, pointer: "auto", nunchuk_stick: "left", gyro_invert_x: false,
  gyro_invert_y: false, motion: false, motion_target: "both", motion_sensitivity: 1, motion_invert_x: false, motion_invert_y: false });
const normalizeSetup = (raw) => {
  const c = raw || {};
  const out = { active: String(c.active ?? "0"), pads: {} };
  for (const [k, v] of Object.entries(c.pads || {})) out.pads[k] = Object.assign(defaultLayout(), v, { bindings: { ...(v.bindings || {}) } });
  if (!out.pads[out.active]) out.pads[out.active] = defaultLayout();
  return out;
};
const layout = () => setup.pads[setup.active] || (setup.pads[setup.active] = defaultLayout());
const activePad = () => padsList.find((p) => String(p.slot) === setup.active);
const family = () => padFamily(activePad()?.name);
// The controller inputs a Wii input currently answers to (explicit binding, or the runtime default).
function effective(key) {
  const v = layout().bindings[key];
  if (v === undefined) return DEFAULT_BIND[key] || [];
  return v.split(",").map((s) => s.trim()).filter(Boolean);
}
function switchRow(key, label, sub, on) {
  return `<label class="set switch"><div class="rl"><b>${label}</b>${sub ? `<small>${sub}</small>` : ""}</div>` +
    `<div class="rc"><input type="checkbox" data-k="${key}" ${on ? "checked" : ""}><span></span></div></label>`;
}
const leds = (slot) => `<span class="leds">${[0, 1, 2, 3].map((i) => `<i class="${i === Number(slot) ? "lit" : ""}"></i>`).join("")}</span>`;

// ---- players strip and live test (polled while the Controls page is open)
async function pollPads() {
  try { padsList = await invoke("list_gamepads"); } catch { padsList = []; }
  if (!setup) return;
  const key = padsList.map((p) => p.slot + ":" + p.name).join("|");
  if (key !== padsKey) { padsKey = key; renderPlayers(); renderMapHead(); renderMapRows(); }
  updateLive();
}
function playerSlots() {
  const s = new Set(["0", "1", ...Object.keys(setup.pads), ...padsList.map((p) => String(p.slot))]);
  return [...s].filter((x) => Number(x) < 4).sort();
}
function renderPlayers() {
  $("players").innerHTML = playerSlots().map((slot) => {
    const pad = padsList.find((p) => String(p.slot) === slot);
    const custom = setup.pads[slot] && Object.keys(setup.pads[slot].bindings).length;
    const sub = pad ? (custom ? "Custom layout" : "Default layout") : slot === "1" ? "Plug in a second controller for co-op" : "Waiting for a controller";
    return `<button class="player${slot === setup.active ? " on" : ""}${pad ? " connected" : ""}" data-slot="${slot}">` +
      `<span class="ptop"><b>Player ${Number(slot) + 1}</b>${leds(slot)}</span>` +
      `<span class="pname">${pad ? esc(pad.name) : "Not connected"}</span><small>${sub}</small></button>`;
  }).join("");
}
function renderMapHead() {
  const pad = activePad();
  $("mapDevice").hidden = setup.active !== "0";
  for (const b of $("mapDevice").querySelectorAll("button")) b.classList.toggle("on", b.dataset.v === (kbView() ? "kbm" : "pad"));
  $("mapHint").textContent = kbView()
    ? "Click an input, then press the key or mouse button for it. Keyboard and mouse bindings are for player 1 and work alongside the controller."
    : "Click an input, then press the controller button for it. Press the controller to test: inputs light up as the game will see them.";
  $("mapTitle").textContent = "Player " + (Number(setup.active) + 1);
  $("mapSub").textContent = pad ? pad.name : "No controller in this slot yet";
}
function updateLive() {
  const pad = activePad();
  const down = new Set(pad ? pad.buttons : []);
  for (const el of document.querySelectorAll("#players .player")) {
    const p = padsList.find((x) => String(x.slot) === el.dataset.slot);
    el.classList.toggle("live", !!p && (p.buttons.length > 0 || p.axes.some(([, v]) => Math.abs(v) > 0.35)));
  }
  for (const el of document.querySelectorAll("#mapRows [data-k]")) el.classList.toggle("live", effective(el.dataset.k).some((n) => down.has(n)));
  const knob = $("stickKnob");
  if (knob) {
    const ax = Object.fromEntries(pad ? pad.axes : []);
    const right = layout().nunchuk_stick === "right";
    const x = ax[right ? "RX" : "LX"] || 0, y = ax[right ? "RY" : "LY"] || 0;
    knob.setAttribute("transform", `translate(${(x * 11).toFixed(1)} ${(-y * 11).toFixed(1)})`);
    $("stickGlow").classList.toggle("on", Math.hypot(x, y) > 0.25);
  }
}

// ---- mapping editor
function fillControls() {
  setup = normalizeSetup(state.settings.controls);
  renderControls();
}
function renderControls() {
  renderPlayers();
  renderMapHead();
  renderMapRows();
  renderMotionRows();
  updateLive();
}
function renderMapRows() {
  const fam = family();
  const rows = (list) => list.map(([key, name]) => bindRow(key, name, fam)).join("");
  const right = layout().nunchuk_stick === "right";
  $("mapRows").innerHTML =
    `<div class="mapside"><h4>Wii Remote</h4>${rows(WII_REMOTE)}</div>` +
    `<div class="mapart">${WII_ART}</div>` +
    `<div class="mapside"><h4>Nunchuk</h4>` +
      `<div class="brow static"><span class="bname">Stick</span><span class="seg sm" id="stickSeg">` +
      `<button data-v="left" class="${right ? "" : "on"}">Left</button><button data-v="right" class="${right ? "on" : ""}">Right</button></span></div>` +
      (kbView() ? rows(KB_STICK) : "") + rows(WII_NUNCHUK) + `<h4>Motion</h4>${rows(WII_MOTION)}</div>`;
  for (const [i, led] of [...document.querySelectorAll("#artLeds .led")].entries()) led.classList.toggle("lit", i === Number(setup.active));
  if (capturing) for (const el of document.querySelectorAll(`#mapRows [data-k="${capturing.key}"]`)) el.classList.add("waiting");
}
function bindRow(key, name, fam) {
  const v = layout().bindings[key];
  const kb = kbView();
  const list = effective(key).filter((t) => isKb(t) === kb);
  const waiting = capturing && capturing.key === key;
  const value = waiting ? `<span class="pending">${capturing.add ? "Press another…" : kb ? "Press a key or mouse button…" : "Press a button…"}</span>`
    : v === "" ? `<span class="unbound">Unbound</span>`
    : list.length ? list.map((n) => capHtml(n, fam)).join("")
    : `<span class="unbound">—</span>`;
  return `<div class="brow${waiting ? " waiting" : ""}${v === undefined ? " def" : ""}" data-k="${key}" data-act="capture" title="${kb ? "Click, then press a key or mouse button" : "Click, then press a controller button"}">` +
    `<span class="bname">${name}</span><span class="bval">${value}</span>` +
    `<span class="bacts"><button data-act="add" title="Add another button">+</button>` +
    `<button data-act="default" title="Back to the default"${v === undefined ? " disabled" : ""}>↺</button>` +
    `<button data-act="none" title="Unbind"${v === "" ? " disabled" : ""}>⊘</button></span></div>`;
}
$("mapRows").addEventListener("click", (e) => {
  const seg = e.target.closest("#stickSeg button");
  if (seg) { layout().nunchuk_stick = seg.dataset.v; renderMapRows(); updateLive(); applyControls(); return; }
  const b = e.target.closest("[data-act]") || e.target.closest(".part"); if (!b) return;
  const key = b.closest("[data-k]").dataset.k;
  const act = b.dataset.act || "capture";
  if (act === "capture") toggleCapture(key, false);
  else if (act === "add") toggleCapture(key, true);
  else if (act === "default") { endCapture(true); delete layout().bindings[key]; changed(); }
  else if (act === "none") { endCapture(true); layout().bindings[key] = effective(key).filter((t) => isKb(t) !== kbView()).join(","); changed(); }
});
// Hovering a row or a part of the drawing highlights its partner.
for (const [type, on] of [["mouseover", true], ["mouseout", false]]) {
  $("mapRows").addEventListener(type, (e) => {
    const el = e.target.closest("[data-k]"); if (!el) return;
    for (const x of document.querySelectorAll(`#mapRows [data-k="${el.dataset.k}"]`)) x.classList.toggle("hl", on);
  });
}
function setBinding(key, name, add) {
  const b = layout().bindings;
  // A new binding replaces those of its own device (controller, or keyboard and mouse) unless it is added; the other device's stay.
  const list = effective(key).filter((t) => add || isKb(t) !== isKb(name));
  if (!list.includes(name)) list.push(name);
  b[key] = list.join(",");
  changed();
}
function changed() { renderMapRows(); renderPlayers(); updateLive(); applyControls(); }

function toggleCapture(key, add) {
  if (capturing && capturing.key === key && capturing.add === add) { endCapture(); return; }
  endCapture(true);
  if (kbView()) {
    capturing = { key, add, kb: true, started: Date.now(), timer: setTimeout(() => { endCapture(); toast("No key pressed."); }, 8000) };
    renderMapRows(); updateLive();
    return;
  }
  capturing = { key, add, started: Date.now(), timer: setInterval(pollCapture, 40) };
  invoke("begin_capture").catch(() => {});
  renderMapRows(); updateLive();
}
async function pollCapture() {
  if (!capturing) return;
  if (Date.now() - capturing.started > 8000) { endCapture(); toast("No button pressed."); return; }
  try {
    const name = await invoke("poll_capture");
    if (!capturing || !name) return;
    const { key, add } = capturing; endCapture(true); setBinding(key, name, add);
  } catch { endCapture(); }
}
function endCapture(quiet) {
  if (!capturing) return;
  clearInterval(capturing.timer); clearTimeout(capturing.timer);
  const kb = capturing.kb;
  capturing = null;
  if (!kb) invoke("cancel_capture").catch(() => {});
  if (!quiet) { renderMapRows(); updateLive(); }
}
document.addEventListener("keydown", (e) => {
  if (!capturing) return;
  if (e.key === "Escape") { endCapture(); return; }
  if (!capturing.kb) return;
  e.preventDefault();
  const name = sdlKeyName(e.code);
  if (!name) { toast("That key can't be bound."); return; }
  const { key, add } = capturing; endCapture(true); setBinding(key, "Key:" + name, add);
});
// Mouse buttons bind while a keyboard capture is waiting (the click that started it is ignored).
document.addEventListener("mousedown", (e) => {
  if (!capturing || !capturing.kb || Date.now() - capturing.started < 200) return;
  const name = ["Left", "Middle", "Right", "X1", "X2"][e.button];
  if (!name) return;
  e.preventDefault(); e.stopPropagation();
  const { key, add } = capturing; endCapture(true); setBinding(key, "Mouse:" + name, add);
}, true);
document.addEventListener("contextmenu", (e) => { if (capturing && capturing.kb) e.preventDefault(); }, true);
$("mapDevice").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-v]"); if (!b) return;
  endCapture(true);
  mapView = b.dataset.v;
  renderMapHead(); renderMapRows(); updateLive();
});

function renderMotionRows() {
  const c = layout();
  $("motionRows").innerHTML =
    `<div class="set"><div class="rl"><b>Pointer</b><small>What moves the Wii pointer. Automatic follows the control scheme in Settings.</small></div>` +
    `<div class="rc"><select data-k="pointer"><option value="auto">Automatic</option><option value="stick">Right stick</option>` +
    `<option value="gyro">Gyro</option><option value="both">Gyro and right stick</option><option value="none">Off</option></select></div></div>` +
    switchRow("gyro_invert_x", "Invert gyro left and right", "", c.gyro_invert_x) +
    switchRow("gyro_invert_y", "Invert gyro up and down", "", c.gyro_invert_y) +
    switchRow("motion", "Controller motion", "Tilting and moving the controller moves the Wii Remote or Nunchuk accelerometers.", c.motion) +
    `<div class="set"><div class="rl"><b>Motion goes to</b></div>` +
    `<div class="rc"><select data-k="motion_target"><option value="remote">Wii Remote</option><option value="nunchuk">Nunchuk</option><option value="both">Both</option></select></div></div>` +
    `<div class="set"><div class="rl"><b>Motion sensitivity</b></div><div class="rc slide">` +
    `<input type="range" data-k="motion_sensitivity" min="0.1" max="4" step="0.1" aria-label="Motion sensitivity"><output></output></div></div>` +
    switchRow("motion_invert_x", "Invert motion left and right", "", c.motion_invert_x) +
    switchRow("motion_invert_y", "Invert motion up and down", "", c.motion_invert_y);
  for (const sel of $("motionRows").querySelectorAll("select")) sel.value = c[sel.dataset.k];
  const range = $("motionRows").querySelector("input[type=range]");
  range.value = c.motion_sensitivity;
  range.nextElementSibling.textContent = Number(c.motion_sensitivity).toFixed(1) + "x";
}

function applyControls() {
  clearTimeout(ctrlTimer);
  ctrlTimer = setTimeout(saveControlsNow, 160);
}
async function saveControlsNow() {
  if (!setup) return;
  try {
    await invoke("save_controls", { controls: setup });
    state.settings.controls = JSON.parse(JSON.stringify(setup));   // keep a later refresh from restoring the old layout
    const s = $("ctrlSaved");
    s.textContent = state.running ? "Saved · applied in game" : "Saved"; s.classList.add("show");
    clearTimeout(saveControlsNow.t); saveControlsNow.t = setTimeout(() => s.classList.remove("show"), 1400);
  } catch (e) { toast(String(e), true); }
}
async function flushControls() { clearTimeout(ctrlTimer); await saveControlsNow(); }

$("players").addEventListener("click", (e) => {
  const b = e.target.closest(".player"); if (!b) return;
  endCapture(true);
  setup.active = b.dataset.slot;
  if (!setup.pads[setup.active]) setup.pads[setup.active] = defaultLayout();
  renderControls(); applyControls();
});
$("mapReset").onclick = () => {
  endCapture(true);
  setup.pads[setup.active] = defaultLayout();
  renderControls(); applyControls();
  toast("Player " + (Number(setup.active) + 1) + " uses the default layout again.");
};
$("motionRows").addEventListener("change", (e) => {
  const el = e.target.closest("[data-k]"); if (!el) return;
  const k = el.dataset.k;
  if (el.type === "checkbox") layout()[k] = el.checked; else if (el.type === "range") layout()[k] = Number(el.value); else layout()[k] = el.value;
  applyControls();
});
$("motionRows").addEventListener("input", (e) => {
  const el = e.target; if (el.dataset.k !== "motion_sensitivity") return;
  el.nextElementSibling.textContent = Number(el.value).toFixed(1) + "x";
});

// ---------------------------------------------------------------- save states
const fmtWhen = (ms) => {
  const d = new Date(ms), now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return "Today, " + time;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "Yesterday, " + time;
  return d.toLocaleDateString([], { day: "numeric", month: "short" }) + ", " + time;
};
let statesList = [];
async function loadStates() {
  try { statesList = await invoke("list_states"); } catch (e) { statesList = []; }
  const host = $("stateList");
  if (!statesList.length) {
    host.innerHTML = '<div class="empty">No save states yet. Start the game and press <kbd>F1</kbd> to save one.</div>';
  } else {
    host.innerHTML = statesList.map((s) => `<div class="state" data-id="${esc(s.id)}">` +
      `<div class="thumb">${s.thumbnail ? `<img src="${s.thumbnail}" alt="" loading="lazy">` : ""}</div>` +
      `<div class="smeta"><b>${esc(fmtWhen(s.saved_ms))}</b><small>${esc([s.note, s.compatible ? "" : "Saved by an incompatible runtime version"].filter(Boolean).join(" · "))}</small></div>` +
      `<button class="btn primary" data-act="load"${s.compatible && !state.running ? "" : " disabled"}>Play from here</button>` +
      `<button class="icon-btn" data-act="delete" title="Delete"><svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/></svg></button></div>`).join("");
  }
  updateResume();
}
function updateResume() {
  const latest = statesList.find((s) => s.compatible);
  const b = $("resumeLatest");
  b.hidden = !latest || state.running;
  if (latest) b.textContent = "Continue from save state · " + fmtWhen(latest.saved_ms);
}
$("stateList").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const id = b.closest(".state").dataset.id;
  if (b.dataset.act === "delete") {
    if (!confirm("Delete this save state?")) return;
    try { await invoke("delete_state", { id }); } catch (err) { toast(String(err), true); }
    loadStates();
  } else startGame(id);
});
$("resumeLatest").onclick = () => { const latest = statesList.find((s) => s.compatible); if (latest) startGame(latest.id); };

// ---------------------------------------------------------------- boot
(async () => {
  await refresh();
  const saved = state.settings.iso_path;
  if (saved) { try { showIso(await invoke("inspect_iso", { path: saved })); } catch { /* file moved: ignore */ } }
  go(homePage());
  // Cheap poll; the full state is only fetched when the game starts or stops.
  setInterval(() => { invoke("game_running").then((r) => { if (r !== state.running) refresh(); }).catch(() => {}); }, 2000);
})();

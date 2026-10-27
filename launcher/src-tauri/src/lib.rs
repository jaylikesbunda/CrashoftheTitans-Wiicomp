//! Crash of the Titans launcher: ISO -> game files, settings -> config.toml, controller status, process control.
//! The embedded runtime is the native port built from the same DOL the user's ISO must contain (checked by SHA-256 before launch).

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    fs,
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;
mod builder_identity;
mod frame_limit;
mod render_pacing;
mod camera_glide;
mod direct_fifo;
mod gx_batching;
mod controls;
mod soundtrack;
mod savestates;
mod title_art;
mod releases;
mod chapters;
mod direct;
mod prompt_glyphs;
mod prompts;

mod embedded {
    include!(concat!(env!("OUT_DIR"), "/embedded.rs"));
}
static RUNTIME_ZST: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/runtime.zst"));

/// Where extract_disc puts the disc's root-level default.rcf inside the prepared game folder.
const RCF_REL_PATH: &str = "files/default.rcf";
/// Bump when the bundled title-logo tiles change. Prepared game folders record the version they were patched with and are
/// brought up to date in place at launch, so players do not have to extract their disc again.
const TITLE_ART_VERSION: &str = "4";
/// Composed title art: the launcher logo and the disc's untouched tiles, inside the prepared game folder.
const TITLE_ART_DIR: &str = ".title-art";
const TITLE_ART_MARKER: &str = ".title-art-version";

const CAMERA_GLIDE_DEFAULT_MS: u32 = camera_glide::DEFAULT_MS;

fn default_camera_glide_ms() -> u32 { CAMERA_GLIDE_DEFAULT_MS }

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub iso_path: Option<String>,
    pub resolution_scale: u32,
    pub window_width: u32,
    pub window_height: u32,
    pub vsync: bool,
    /// None uses regional pacing; Some overrides it in frames/second.
    pub custom_fps: Option<f64>,
    /// Use precise Windows deadline waits; false selects the original CV wait.
    pub frame_pacing: bool,
    /// Smooth the camera when platforms/rails move it; the duration is in milliseconds.
    #[serde(default)]
    pub camera_glide: bool,
    /// Camera glide duration, clamped to 0..=500 ms on save.
    #[serde(default = "default_camera_glide_ms")]
    pub camera_glide_ms: u32,
    pub input_mode: u32,
    pub gyro_sensitivity: f64,
    pub pointer_speed: f64,
    pub safe_gpu_sync: bool,
    pub fps_log: bool,
    /// Opt-in workload counters exported by F4 independently of the timing HUD.
    pub performance_census: bool,
    #[serde(default)]
    pub performance_histogram: bool,
    /// Compact register emission; on by default (turn off only for A/B captures).
    pub direct_fifo: bool,
    /// FIFO batch thresholds. The default (adaptive4) starts 4 KiB batches while the renderer is parked, 16 KiB otherwise;
    /// measured 51 -> 58 fps on the heavy scene vs adaptive8.
    pub gx_batching: String,
    pub runtime_override: Option<String>,
    /// Report a 16:9 television to the game (it then lays out its menus and movies for widescreen).
    pub widescreen: bool,
    /// Game language: en, fr, de, es, it or nl. Needs the matching language archive on the disc.
    pub language: String,
    /// Button prompts: auto/follow track the last device; wii/xbox/playstation/keyboard stay fixed.
    pub button_prompts: String,
    /// "windowed" or "borderless" (desktop-resolution fullscreen; F11 toggles it in game).
    pub display_mode: String,
    /// Game speed multiplier (1.0 = original speed).
    pub game_speed: f64,
    /// Anisotropic filtering level: 1 (off), 2, 4, 8 or 16.
    pub anisotropy: u32,
    /// Controller mapping from the Controls page: one layout per controller slot.
    pub controls: controls::ControlSetup,
    /// Episode a new game starts at, as the game names it ("L3_E2"); None starts at the beginning.
    /// The Episodes page's choice: open the episode straight away (no boot movies or menus) rather than through a new game's menus.
    pub start_direct: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            iso_path: None,
            resolution_scale: 2,
            window_width: 1280,
            window_height: 720,
            vsync: false,
            custom_fps: Some(120.0),
            frame_pacing: true,
            camera_glide: false,
            camera_glide_ms: CAMERA_GLIDE_DEFAULT_MS,
            input_mode: 6,
            gyro_sensitivity: 1.0,
            pointer_speed: 0.4,
            safe_gpu_sync: false,
            fps_log: false,
            performance_census: false,
            performance_histogram: false,
            direct_fifo: true,
            gx_batching: "adaptive4".into(),
            runtime_override: None,
            widescreen: true,
            language: "en".into(),
            button_prompts: "auto".into(),
            display_mode: "windowed".into(),
            game_speed: 1.0,
            anisotropy: 1,
            controls: controls::ControlSetup::default(),
            start_direct: true,
        }
    }
}

struct Running {
    child: Arc<Mutex<Option<Child>>>,
}

/// One in-flight "press a button to bind" request. The gamepad thread fills `result` on the next button press.
#[derive(Default)]
pub struct Capture {
    active: bool,
    result: Option<String>,
}

pub struct AppState {
    settings: Mutex<Settings>,
    game: Running,
    logs: Arc<Mutex<VecDeque<String>>>,
    pads: Arc<Mutex<Vec<PadInfo>>>,
    capture: Arc<Mutex<Capture>>,
    preparing: AtomicBool,
    building: Arc<AtomicBool>,
}

#[derive(Clone, Serialize, Default)]
pub struct PadInfo {
    id: usize,
    slot: usize,
    name: String,
    mapped: bool,
    power: String,
    buttons: Vec<String>,
    axes: Vec<(String, f32)>,
}

#[derive(Serialize)]
pub struct IsoInfo {
    path: String,
    game_id: String,
    title: String,
    format: String,
    size: u64,
    is_wii: bool,
    supported: bool,
    message: String,
}

#[derive(Serialize)]
pub struct StateView {
    settings: Settings,
    data_dir: String,
    game_ready: bool,
    game_dir: String,
    runtime_embedded: bool,
    runtime_ready: bool,
    runtime_label: String,
    builder_available: bool,
    builder_pack_downloadable: bool,
    building: bool,
    running: bool,
    version: String,
}

fn data_dir(app: &AppHandle) -> PathBuf {
    let dir = app.path().app_data_dir().unwrap_or_else(|_| PathBuf::from("."));
    let _ = fs::create_dir_all(&dir);
    dir
}
fn settings_path(app: &AppHandle) -> PathBuf { data_dir(app).join("settings.json") }
/// The release whose prepared files the launcher plays and builds (`game/active-release`, written by prepare_game).
fn active_release(app: &AppHandle) -> &'static releases::Release {
    fs::read_to_string(data_dir(app).join("game").join("active-release")).ok()
        .and_then(|id| releases::by_game_id(id.trim()))
        .or_else(|| releases::by_game_id(releases::DEFAULT)).unwrap()
}
fn release_dir(app: &AppHandle, release: &releases::Release) -> PathBuf { data_dir(app).join("game").join(release.game_id) }
fn game_dir(app: &AppHandle) -> PathBuf { release_dir(app, active_release(app)) }
fn profile_dir(app: &AppHandle) -> PathBuf { data_dir(app).join("profile") }
fn states_dir(app: &AppHandle) -> PathBuf { profile_dir(app).join("savestates") }

fn persistent_program_cache(root: &Path, exe: &Path) -> Option<PathBuf> {
    let cache = root.join("cache").join("program-cache");
    fs::create_dir_all(&cache).ok()?;
    // Source/driver hashes are the keys. Keep the cache outside versioned build
    // folders and import existing entries; the runtime validates every binary.
    let mut sources = Vec::new();
    if let Some(parent) = exe.parent() { sources.push(parent.join("program-cache")); }
    if let Ok(builds) = fs::read_dir(root.join("builds")) {
        for build in builds.filter_map(Result::ok).take(32) {
            if build.file_name().to_string_lossy().starts_with("clean-room-") {
                sources.push(build.path().join("storage/toolchain/program-cache"));
            }
        }
    }
    for source in sources {
        if source == cache { continue; }
        let Ok(entries) = fs::read_dir(source) else { continue; };
        for entry in entries.filter_map(Result::ok).take(4096) {
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) != Some("bin") { continue; }
            let Some(stem) = path.file_stem().and_then(|s| s.to_str()) else { continue; };
            if stem.len() != 16 || !stem.bytes().all(|c| c.is_ascii_hexdigit()) { continue; }
            let Ok(info) = entry.metadata() else { continue; };
            if !info.is_file() || info.len() > (64 << 20) + 24 { continue; }
            let target = cache.join(entry.file_name());
            if !target.exists() { let _ = fs::copy(path, target); }
        }
    }
    Some(cache)
}
fn runtime_exe_path(app: &AppHandle) -> PathBuf {
    let short = &embedded::RUNTIME_SHA256[..embedded::RUNTIME_SHA256.len().min(12)];
    data_dir(app).join("runtime").join(format!("CrashNative-{short}.exe"))
}

fn load_settings(app: &AppHandle) -> Settings {
    fs::read_to_string(settings_path(app)).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }

fn game_marker(app: &AppHandle) -> PathBuf { game_dir(app).join(".crash-launcher-ready") }
fn game_ready(app: &AppHandle) -> bool {
    let dol = game_dir(app).join("sys/main.dol");
    match (fs::read_to_string(game_marker(app)), dol.exists()) {
        (Ok(marker), true) => marker.trim() == active_release(app).dol_sha256,
        _ => false,
    }
}

fn runtime_ready(app: &AppHandle, s: &Settings) -> bool {
    if let Some(p) = &s.runtime_override {
        let p=Path::new(p);
        if p.starts_with(data_dir(app).join("builds")) {
            let dol_sha=fs::read_to_string(game_marker(app)).unwrap_or_default();
            return builder_identity::runtime_matches(p,dol_sha.trim());
        }
        return p.exists();
    }
    embedded_runtime_fits(app) && runtime_exe_path(app).exists()
}

/// An embedded runtime was recompiled from one main.dol and only runs that release.
fn embedded_runtime_fits(app: &AppHandle) -> bool {
    embedded::RUNTIME_SIZE > 0 && (embedded::DOL_SHA256.is_empty() || embedded::DOL_SHA256 == active_release(app).dol_sha256)
}

/// Tauri hands back Windows paths in the `\\?\` verbatim form. GCC derives its helper-program paths (cc1, as, ld) from its own
/// exe path and cannot run from a verbatim one, so every path given to the toolchain must be the plain form.
fn plain_path(p: PathBuf) -> PathBuf {
    const VERBATIM: &str = "\\\\?\\";
    match p.to_str().and_then(|s| s.strip_prefix(VERBATIM)) {
        Some(rest) if !rest.starts_with("UNC\\") => PathBuf::from(rest),
        _ => p,
    }
}

/// Root of the bundled builder pack (`builder-pack/` in the installed resources, or CRASH_BUILDER_PACK for development).
fn pack_root(app: &AppHandle) -> Option<PathBuf> {
    let mut roots = Vec::new();
    if let Some(p) = std::env::var_os("CRASH_BUILDER_PACK") { roots.push(PathBuf::from(p)); }
    if let Ok(res) = app.path().resource_dir() { roots.push(res.join("builder-pack")); }
    if let Ok(installed) = fs::read_dir(data_dir(app).join("builder-packs")) {
        roots.extend(installed.flatten().map(|e| e.path()).filter(|p| p.is_dir()));
    }
    roots.push(Path::new(env!("CARGO_MANIFEST_DIR")).join("builder-pack"));
    roots.into_iter().map(plain_path).find(|r| builder_identity::valid_builder(&r.join("native-builder")) && r.join("toolchain/python/python.exe").is_file())
}

fn builder_root(app: &AppHandle) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(p) = pack_root(app) { candidates.push(p.join("native-builder")); }
    if let Some(path) = std::env::var_os("CRASH_NATIVE_BUILDER_ROOT") {
        candidates.push(PathBuf::from(path));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() { candidates.push(parent.join("native-builder")); }
    }
    // Development builds can use this checkout directly. Release builds use the adjacent native-builder pack.
    candidates.push(Path::new(env!("CARGO_MANIFEST_DIR")).ancestors().nth(2)?.to_path_buf());
    candidates.into_iter().find(|p| {
        p.join("tools/clean_room_build.py").is_file()
            && builder_identity::valid_builder(p)
            && p.join("vendor").is_dir()
            && builder_index(p).is_some()
    })
}

fn builder_manifest_url() -> Option<&'static str> {
    option_env!("CRASH_BUILDER_MANIFEST_URL").filter(|s| !s.trim().is_empty())
}

fn builder_available(app: &AppHandle) -> bool {
    builder_root(app).is_some() || builder_manifest_url().is_some()
}

fn builder_index(root: &Path) -> Option<PathBuf> {
    [root.join("data/function-index.tsv"), root.join("export/index.tsv"), root.parent()?.join("export/index.tsv")]
        .into_iter().find(|p| p.is_file())
}

fn game_is_running(state: &AppState) -> bool {
    state.game.child.lock().unwrap().as_mut().map(|c| matches!(c.try_wait(), Ok(None))).unwrap_or(false)
}

/// Cheap check for the page's periodic poll (get_state touches the file system and the builder pack).
#[tauri::command]
fn game_running(state: State<AppState>) -> bool { game_is_running(&state) }

/// Async so its file-system checks run off the UI thread.
#[tauri::command]
async fn get_state(app: AppHandle, state: State<'_, AppState>) -> Result<StateView, String> {
    let settings = state.settings.lock().unwrap().clone();
    let running = game_is_running(&state);
    Ok(StateView {
        game_ready: game_ready(&app),
        game_dir: game_dir(&app).display().to_string(),
        data_dir: data_dir(&app).display().to_string(),
        runtime_embedded: embedded::RUNTIME_SIZE > 0,
        runtime_ready: runtime_ready(&app, &settings),
        runtime_label: if settings.runtime_override.as_ref().map(|p| Path::new(p).starts_with(data_dir(&app).join("builds"))).unwrap_or(false) { "built from your disc".into() }
            else if settings.runtime_override.is_some() { "custom runtime".into() }
            else if embedded::RUNTIME_SIZE > 0 { format!("built-in runtime {}", &embedded::RUNTIME_SHA256[..12]) }
            else { "no runtime embedded".into() },
        builder_available: builder_available(&app),
        builder_pack_downloadable: builder_root(&app).is_none() && builder_manifest_url().is_some(),
        building: state.building.load(Ordering::Relaxed),
        settings,
        running,
        version: env!("CARGO_PKG_VERSION").into(),
    })
}

#[tauri::command]
fn save_settings(app: AppHandle, state: State<AppState>, settings: Settings) -> Result<(), String> {
    let mut s = settings;
    if s.custom_fps.is_some() && frame_limit::sanitize(s.custom_fps).is_none() { return Err("Custom FPS must be a finite number between 10 and 1000.".into()); }
    s.custom_fps = frame_limit::sanitize(s.custom_fps);
    s.resolution_scale = s.resolution_scale.clamp(1, 4);
    s.camera_glide_ms = camera_glide::clamp_milliseconds(s.camera_glide_ms);
    s.window_width = s.window_width.clamp(640, 7680);
    s.window_height = s.window_height.clamp(480, 4320);
    s.gyro_sensitivity = s.gyro_sensitivity.clamp(0.1, 5.0);
    s.pointer_speed = s.pointer_speed.clamp(0.1, 5.0);
    if ![2, 3, 4, 6].contains(&s.input_mode) { s.input_mode = 6; }
    if language_code(&s.language).is_none() { s.language = "en".into(); }
    if !["auto", "follow", "wii", "xbox", "playstation", "keyboard"].contains(&s.button_prompts.as_str()) { s.button_prompts = "auto".into(); }
    if s.display_mode != "borderless" { s.display_mode = "windowed".into(); }
    s.game_speed = s.game_speed.clamp(1.0, 4.0);
    if ![1, 2, 4, 8, 16].contains(&s.anisotropy) { s.anisotropy = 1; }
    s.controls = s.controls.clone().sanitized();
    persist_settings(&app, &mut state.settings.lock().unwrap(), s)
}

/// Called with the settings lock held so live changes and full-form saves stay ordered.
fn persist_settings(app: &AppHandle, current: &mut Settings, updated: Settings) -> Result<(), String> {
    let saved = serde_json::to_vec_pretty(&updated).map_err(|e| e.to_string())?;
    render_pacing::write(&profile_dir(app), updated.frame_pacing)?;
    if let Err(error) = fs::write(settings_path(app), saved) {
        let _ = render_pacing::write(&profile_dir(app), current.frame_pacing);
        return Err(error.to_string());
    }
    write_prompt_mode(app, &updated)?;
    *current = updated;
    Ok(())
}

/// The Display switch applies independently of other unsaved settings.
#[tauri::command]
fn set_frame_pacing(app: AppHandle, state: State<AppState>, enabled: bool) -> Result<(), String> {
    let mut current = state.settings.lock().unwrap();
    let mut updated = current.clone(); updated.frame_pacing = enabled;
    persist_settings(&app, &mut current, updated)
}

/// The Camera switch applies independently of other unsaved settings.
#[tauri::command]
fn set_camera_glide(app: AppHandle, state: State<AppState>, enabled: bool, milliseconds: u32) -> Result<(), String> {
    let mut current = state.settings.lock().unwrap();
    let mut updated = current.clone();
    updated.camera_glide = enabled;
    updated.camera_glide_ms = camera_glide::clamp_milliseconds(milliseconds);
    persist_settings(&app, &mut current, updated)
}

// ---------------------------------------------------------------- ISO

fn inspect(path: &Path) -> Result<IsoInfo, String> {
    use nod::read::{DiscOptions, DiscReader};
    let disc = DiscReader::new(path, &DiscOptions::default()).map_err(|e| format!("Could not read this disc image: {e}"))?;
    let header = disc.header();
    let game_id = header.game_id_str().to_string();
    let title = header.game_title_str().trim().to_string();
    let is_wii = header.is_wii();
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
    let size = fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    let release = releases::by_game_id(&game_id);
    let supported = release.is_some();
    let message = if let Some(r) = release { format!("{} release. Ready to prepare.", r.label) }
        else if game_id.starts_with("RQJ") { format!("This is a different region/version of the game ({game_id}). This build supports {}.", releases::supported_list()) }
        else { format!("This is not Crash of the Titans (disc ID {game_id}). This build supports {}.", releases::supported_list()) };
    Ok(IsoInfo { path: path.display().to_string(), game_id, title, format: ext.to_uppercase(), size, is_wii, supported, message })
}

#[tauri::command]
async fn pick_iso(app: AppHandle) -> Result<Option<IsoInfo>, String> {
    let picked = app.dialog().file()
        .add_filter("Wii disc images", &["iso", "wbfs", "rvz", "wia", "ciso", "gcz", "nfs"])
        .blocking_pick_file();
    match picked {
        Some(p) => { let path = p.into_path().map_err(|e| e.to_string())?; inspect(&path).map(Some) }
        None => Ok(None),
    }
}

#[tauri::command]
async fn inspect_iso(path: String) -> Result<IsoInfo, String> { inspect(Path::new(&path)) }

#[derive(Clone, Serialize)]
struct Progress { done: u64, total: u64, file: String, phase: String }

fn write_file(path: &Path, data: &[u8]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() { fs::create_dir_all(parent)?; }
    fs::write(path, data)
}

fn extract_disc(app: &AppHandle, iso: &Path, dest: &Path) -> Result<(), String> {
    use nod::{common::PartitionKind, read::{DiscOptions, DiscReader, PartitionOptions}};
    let disc = DiscReader::new(iso, &DiscOptions::default()).map_err(|e| e.to_string())?;
    let release = releases::by_game_id(disc.header().game_id_str())
        .ok_or_else(|| format!("Disc ID {} is not {}", disc.header().game_id_str(), releases::supported_list()))?;
    let mut part = disc.open_partition_kind(PartitionKind::Data, &PartitionOptions::default()).map_err(|e| e.to_string())?;
    let meta = part.meta().map_err(|e| e.to_string())?;
    // The recompiled runtime is only valid for the exact main.dol it was generated from.
    let dol_sha = hex(&Sha256::digest(&meta.raw_dol));
    if dol_sha != release.dol_sha256 {
        return Err(format!("This disc's main.dol does not match the supported {} release (disc {}…, expected {}…). Use a clean {} dump.", release.label, &dol_sha[..12], &release.dol_sha256[..12], release.game_id));
    }
    let sys = dest.join("sys");
    write_file(&sys.join("boot.bin"), &meta.raw_boot[..]).map_err(|e| e.to_string())?;
    write_file(&sys.join("bi2.bin"), &meta.raw_bi2[..]).map_err(|e| e.to_string())?;
    write_file(&sys.join("apploader.img"), &meta.raw_apploader).map_err(|e| e.to_string())?;
    write_file(&sys.join("fst.bin"), &meta.raw_fst).map_err(|e| e.to_string())?;
    write_file(&sys.join("main.dol"), &meta.raw_dol).map_err(|e| e.to_string())?;
    let fst = meta.fst().map_err(|e| e.to_string())?;
    let nodes: Vec<(nod::disc::fst::Node, String)> = fst.iter().filter(|(_, n, _)| n.is_file()).map(|(_, n, p)| (n, p)).collect();
    let total: u64 = nodes.iter().map(|(n, _)| n.length() as u64).sum();
    let mut done = 0u64;
    let mut last_emit = std::time::Instant::now();
    let mut buf = vec![0u8; 1 << 20];
    for (node, rel) in nodes {
        let target = dest.join("files").join(rel.trim_start_matches('/'));
        if let Some(parent) = target.parent() { fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
        let mut reader = part.open_file(node).map_err(|e| e.to_string())?;
        let mut out = fs::File::create(&target).map_err(|e| format!("{}: {e}", target.display()))?;
        loop {
            let n = reader.read(&mut buf).map_err(|e| e.to_string())?;
            if n == 0 { break; }
            out.write_all(&buf[..n]).map_err(|e| e.to_string())?;
            done += n as u64;
            if last_emit.elapsed() > Duration::from_millis(80) {
                last_emit = std::time::Instant::now();
                let _ = app.emit("prepare-progress", Progress { done, total, file: rel.clone(), phase: "extract".into() });
            }
        }
    }
    let _ = app.emit("prepare-progress", Progress { done: total, total, file: String::new(), phase: "finish".into() });
    fs::write(dest.join(".crash-launcher-ready"), dol_sha).map_err(|e| e.to_string())?;
    Ok(())
}

fn le32_at(b: &[u8], at: usize) -> Result<u32, String> {
    let x = b.get(at..at + 4).ok_or("Truncated title texture data")?;
    Ok(u32::from_le_bytes(x.try_into().unwrap()))
}

/// The frontend asset package (package\cdd70a8c.p3d) of a default.rcf stream: its offset in the stream and its bytes.
fn frontend_package<R: Read + Seek>(rcf: &mut R, rcf_len: u64) -> Result<(u64, Vec<u8>), String> {
    fn be32(b: &[u8], at: usize) -> Result<u32, String> {
        let x = b.get(at..at + 4).ok_or("Truncated RCF data")?;
        Ok(u32::from_be_bytes(x.try_into().unwrap()))
    }
    rcf.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut header = [0u8; 60];
    rcf.read_exact(&mut header).map_err(|e| format!("Could not read the resource archive header: {e}"))?;
    if !header.starts_with(b"ATG CORE CEMENT LIBRARY\0") { return Err("The game's default.rcf has an unsupported format.".into()); }
    let index = be32(&header, 36)? as u64;
    let index_size = be32(&header, 40)? as u64;
    let names_off = be32(&header, 44)? as u64;
    let names_size = be32(&header, 48)? as usize;
    let count = be32(&header, 56)? as usize;
    if count > 100_000 || index_size != (count as u64) * 12 || index + index_size > rcf_len || names_off + names_size as u64 > rcf_len {
        return Err("The game's resource archive has invalid index bounds.".into());
    }
    rcf.seek(SeekFrom::Start(index)).map_err(|e| e.to_string())?;
    let mut index_bytes = vec![0u8; index_size as usize];
    rcf.read_exact(&mut index_bytes).map_err(|e| e.to_string())?;
    let mut records = Vec::with_capacity(count);
    for r in index_bytes.chunks_exact(12) {
        records.push((u32::from_be_bytes(r[0..4].try_into().unwrap()), u32::from_be_bytes(r[4..8].try_into().unwrap()), u32::from_be_bytes(r[8..12].try_into().unwrap())));
    }
    records.sort_by_key(|r| r.1);
    rcf.seek(SeekFrom::Start(names_off)).map_err(|e| e.to_string())?;
    let mut names = vec![0u8; names_size];
    rcf.read_exact(&mut names).map_err(|e| e.to_string())?;
    let mut cursor = 8usize;
    let mut member = None;
    for (key, off, len) in records {
        let meta = names.get(cursor..cursor + 16).ok_or("Truncated resource archive names table")?;
        let name_len = u32::from_le_bytes(meta[12..16].try_into().unwrap()) as usize;
        cursor += 16;
        if !(2..=1024).contains(&name_len) || cursor + name_len + 3 > names.len() { return Err("Invalid resource archive member name.".into()); }
        let name = std::str::from_utf8(&names[cursor..cursor + name_len - 1]).map_err(|e| e.to_string())?;
        cursor += name_len - 1;
        if names[cursor..cursor + 4] != [0, 0, 0, 0] { return Err("Invalid resource archive name terminator.".into()); }
        cursor += 4;
        if name.eq_ignore_ascii_case("package\\cdd70a8c.p3d") { member = Some((key, off, len)); }
    }
    if cursor != names.len() { return Err("The resource archive name table has trailing data.".into()); }
    let (key, member_off, member_len) = member.ok_or("The disc is missing its frontend asset package.")?;
    if key != 0x53a36f39 || !releases::RELEASES.iter().any(|r| r.title_package_len == member_len) || (member_off as u64) + (member_len as u64) > rcf_len {
        return Err(format!("The frontend asset package does not match a supported release ({}).", releases::supported_list()));
    }
    rcf.seek(SeekFrom::Start(member_off as u64)).map_err(|e| e.to_string())?;
    let mut p3d = vec![0u8; member_len as usize];
    rcf.read_exact(&mut p3d).map_err(|e| e.to_string())?;
    Ok((member_off as u64, p3d))
}

/// One title logo tile in the frontend package: where its PNG length and bytes live, and how many bytes the slot holds.
struct TitleSlot { len_at: usize, png_off: usize, capacity: usize }

/// The six tiles of the FE_Title_Logo texture group, in package order.
fn title_slots(p3d: &[u8]) -> Result<Vec<TitleSlot>, String> {
    let root_type = 0x0001_9005u32.to_le_bytes();
    let mut title_root = None;
    for at in p3d.windows(4).enumerate().filter_map(|(i, w)| (w == root_type).then_some(i)) {
        let total = le32_at(p3d, at + 8)? as usize;
        if total <= p3d.len() - at && p3d[at..at + total.min(256)].windows(b"FE_Title_Logo.tga".len()).any(|w| w == b"FE_Title_Logo.tga") {
            if title_root.is_some() { return Err("The frontend package has more than one title logo group.".into()); }
            title_root = Some(at);
        }
    }
    let root = title_root.ok_or("The frontend package has no FE_Title_Logo texture group.")?;
    let root_header = le32_at(p3d, root + 4)? as usize;
    let root_total = le32_at(p3d, root + 8)? as usize;
    if root_header != 58 || root_total != 56_886 || root + root_total > p3d.len() { return Err("Unexpected title logo texture-group layout.".into()); }
    let mut slots = Vec::new();
    let (mut pos, end) = (root + root_header, root + root_total);
    while pos < end {
        let ty = le32_at(p3d, pos)?;
        let header_len = le32_at(p3d, pos + 4)? as usize;
        let total = le32_at(p3d, pos + 8)? as usize;
        if header_len < 12 || total < header_len || pos + total > end { return Err("Invalid child in the title logo texture group.".into()); }
        if ty == 0x0001_9001 {
            if slots.len() >= title_art::TILES.len() || header_len != 61 { return Err("Unexpected number or layout of title logo textures.".into()); }
            let image = pos + header_len;
            if le32_at(p3d, image)? != 0x0001_9002 { return Err("A title logo image chunk is missing.".into()); }
            let image_total = le32_at(p3d, image + 8)? as usize;
            if image_total < 16 || image + image_total > pos + total { return Err("Invalid PNG extent in the title logo package.".into()); }
            let png_len = le32_at(p3d, image + 12)? as usize;
            if image + 16 + png_len > image + image_total { return Err("Truncated original title logo PNG.".into()); }
            slots.push(TitleSlot { len_at: image + 12, png_off: image + 16, capacity: image_total - 16 });
        }
        pos += total;
    }
    if pos != end || slots.len() != title_art::TILES.len() { return Err(format!("Expected six title logo tiles, found {}.", slots.len())); }
    Ok(slots)
}

fn slot_pngs(p3d: &[u8], slots: &[TitleSlot]) -> Result<Vec<Vec<u8>>, String> {
    slots.iter().map(|s| Ok(p3d[s.png_off..s.png_off + le32_at(p3d, s.len_at)? as usize].to_vec())).collect()
}

/// The disc's untouched title logo tiles, read straight from the disc image (for a prepared copy that was already patched).
fn original_title_tiles_from_disc(iso: &Path) -> Result<Vec<Vec<u8>>, String> {
    use nod::{common::PartitionKind, read::{DiscOptions, DiscReader, PartitionOptions}};
    let disc = DiscReader::new(iso, &DiscOptions::default()).map_err(|e| format!("Could not read the disc image: {e}"))?;
    let mut part = disc.open_partition_kind(PartitionKind::Data, &PartitionOptions::default()).map_err(|e| e.to_string())?;
    let meta = part.meta().map_err(|e| e.to_string())?;
    let fst = meta.fst().map_err(|e| e.to_string())?;
    let node = fst.iter().find(|(_, n, p)| n.is_file() && p.trim_start_matches('/').eq_ignore_ascii_case(RCF_REL_PATH.trim_start_matches("files/")))
        .map(|(_, n, _)| n).ok_or("The disc has no default.rcf.")?;
    let len = node.length() as u64;
    let mut reader = part.open_file(node).map_err(|e| e.to_string())?;
    let (_, p3d) = frontend_package(&mut reader, len)?;
    slot_pngs(&p3d, &title_slots(&p3d)?)
}

/// Writes the WIICOMP title art into a prepared default.rcf and the launcher logo (`logo_out`).
/// `originals` are the disc's own tiles; None reads them from the archive itself, which must then still be untouched.
fn patch_title_logo(archive_path: &Path, originals: Option<Vec<Vec<u8>>>, logo_out: &Path) -> Result<(), String> {
    let mut archive = fs::OpenOptions::new().read(true).write(true).open(archive_path)
        .map_err(|e| format!("Could not open the game's resource archive: {e}"))?;
    let archive_len = archive.metadata().map_err(|e| e.to_string())?.len();
    let (member_off, mut p3d) = frontend_package(&mut archive, archive_len)?;
    let slots = title_slots(&p3d)?;
    let originals = match originals { Some(o) => o, None => slot_pngs(&p3d, &slots)? };
    let art = title_art::compose(&originals)?;
    for (tile, slot) in slots.iter().enumerate() {
        let png = title_art::tile_png(&art.canvas, tile, slot.capacity)?;
        p3d[slot.len_at..slot.len_at + 4].copy_from_slice(&(png.len() as u32).to_le_bytes());
        p3d[slot.png_off..slot.png_off + png.len()].copy_from_slice(&png);
        p3d[slot.png_off + png.len()..slot.png_off + slot.capacity].fill(0);
    }
    // Keep the untouched tiles beside the game so a later art revision starts from them, not from patched output.
    if let Some(dir) = logo_out.parent() {
        let _ = fs::create_dir_all(dir);
        for (i, png) in originals.iter().enumerate() { let _ = fs::write(dir.join(format!("original-tile-{i}.png")), png); }
    }
    fs::write(logo_out, &art.logo_png).map_err(|e| format!("Could not save the launcher logo: {e}"))?;
    archive.seek(SeekFrom::Start(member_off)).map_err(|e| e.to_string())?;
    archive.write_all(&p3d).map_err(|e| format!("Could not write the custom title logo into the prepared game files: {e}"))?;
    archive.flush().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn prepare_game(app: AppHandle, state: State<'_, AppState>, iso: String) -> Result<(), String> {
    if state.building.load(Ordering::Relaxed) { return Err("Wait for the runtime build to finish before preparing another disc.".into()); }
    if state.preparing.swap(true, Ordering::SeqCst) { return Err("Already preparing".into()); }
    let handle = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let release = releases::by_game_id(&inspect(Path::new(&iso))?.game_id)
            .ok_or_else(|| format!("This disc is not {}.", releases::supported_list()))?;
        let dest = release_dir(&handle, release);
        let staging = dest.with_extension("partial");
        let _ = fs::remove_dir_all(&staging);
        let outcome = extract_disc(&handle, Path::new(&iso), &staging)
            .and_then(|_| patch_title_logo(&staging.join(RCF_REL_PATH), None, &staging.join(TITLE_ART_DIR).join("logo.png")))
            .and_then(|_| {
            let _ = fs::remove_dir_all(&dest);
            if let Some(p) = dest.parent() { fs::create_dir_all(p).map_err(|e| e.to_string())?; }
            fs::rename(&staging, &dest).map_err(|e| e.to_string())?;
            let _ = fs::write(dest.join(TITLE_ART_MARKER), TITLE_ART_VERSION);
            fs::write(data_dir(&handle).join("game").join("active-release"), release.game_id).map_err(|e| e.to_string())?;
            adopt_build(&handle, release);
            Ok(())
            });
        if outcome.is_err() { let _ = fs::remove_dir_all(&staging); }
        outcome
    }).await.map_err(|e| e.to_string())?;
    state.preparing.store(false, Ordering::SeqCst);
    result
}

/// After switching discs, select this launcher's earlier build for that disc if there is one; otherwise a build made for the
/// other disc stays selected and shows as needing a rebuild.
fn adopt_build(app: &AppHandle, release: &releases::Release) {
    let state = app.state::<AppState>();
    let mut settings = state.settings.lock().unwrap();
    if let Some(p) = &settings.runtime_override {
        if !Path::new(p).starts_with(data_dir(app).join("builds")) || builder_identity::runtime_matches(Path::new(p), release.dol_sha256) { return; }
    }
    let exe = data_dir(app).join("builds").join(builder_identity::build_dir_name(release.dol_sha256))
        .join("storage/toolchain/CrashNative.exe");
    if builder_identity::runtime_matches(&exe, release.dol_sha256) {
        settings.runtime_override = Some(exe.display().to_string());
        let _ = serde_json::to_vec_pretty(&*settings).map(|b| fs::write(settings_path(app), b));
    }
}

#[derive(Clone, Serialize)]
struct BuilderLine { line: String }

/// Forwards items to the page in batches, at most about 12 events a second (and 500 items each). The game and the builder can
/// print thousands of lines a second; one event per line kept the page busy enough to freeze it, most of all with the Log page open.
fn batched_emitter<T: Serialize + Clone + Send + 'static>(app: AppHandle, event: &'static str) -> std::sync::mpsc::Sender<T> {
    let (tx, rx) = std::sync::mpsc::channel::<T>();
    std::thread::spawn(move || {
        while let Ok(first) = rx.recv() {
            let mut batch = vec![first];
            let deadline = std::time::Instant::now() + Duration::from_millis(80);
            while batch.len() < 500 {
                match rx.recv_timeout(deadline.saturating_duration_since(std::time::Instant::now())) { Ok(item) => batch.push(item), Err(_) => break }
            }
            let _ = app.emit(event, batch);
        }
    });
    tx
}

fn pump_builder(app: AppHandle, logs: Arc<Mutex<VecDeque<String>>>, reader: impl Read + Send + 'static) {
    let tx = batched_emitter::<BuilderLine>(app, "runtime-build-log");
    for line in BufReader::new(reader).split(b'\n').flatten() {
        let line = String::from_utf8_lossy(&line).trim_end_matches('\r').to_string();
        if line.is_empty() { continue; }
        { let mut l = logs.lock().unwrap(); if l.len() >= 4000 { l.pop_front(); } l.push_back(line.clone()); }
        let _ = tx.send(BuilderLine { line });
    }
}

#[derive(Clone, Serialize)]
struct BuilderDownloadProgress { phase: String, done: u64, total: u64, detail: String }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BuilderReleaseManifest {
    schema_version: u32,
    version: String,
    platform: String,
    archive_url: String,
    archive_size: u64,
    archive_sha256: String,
    unpacked_size: u64,
    file_count: usize,
}

fn emit_builder_log(app: &AppHandle, logs: &Arc<Mutex<VecDeque<String>>>, line: String) {
    {
        let mut l = logs.lock().unwrap();
        if l.len() >= 4000 { l.pop_front(); }
        l.push_back(line.clone());
    }
    let _ = app.emit("runtime-build-log", vec![BuilderLine { line }]);
}

fn emit_download_progress(app: &AppHandle, phase: &str, done: u64, total: u64, detail: &str) {
    let _ = app.emit("builder-download-progress", BuilderDownloadProgress {
        phase: phase.into(), done, total, detail: detail.into(),
    });
}

fn valid_builder_pack(root: &Path) -> bool {
    builder_identity::valid_builder(&root.join("native-builder"))
        && root.join("native-builder/tools/clean_room_build.py").is_file()
        && root.join("native-builder/vendor/NWiiRecomp/PINNED_COMMIT").is_file()
        && root.join("native-builder/vendor").is_dir()
        && root.join("toolchain/python/python.exe").is_file()
        && root.join("toolchain/mingw64/bin/cmake.exe").is_file()
        && root.join("toolchain/mingw64/bin/ninja.exe").is_file()
        && root.join("deps/sdl2-src").is_dir()
        && root.join("deps/tomlplusplus-src").is_dir()
        && root.join("deps/glad-src").is_dir()
        && root.join("export/index.tsv").is_file()
}

fn download_builder_pack(
    app: &AppHandle,
    logs: &Arc<Mutex<VecDeque<String>>>,
    manifest_url: &str,
) -> Result<PathBuf, String> {
    use reqwest::Url;
    use std::io::{Read as _, Write as _};

    const MAX_MANIFEST_BYTES: u64 = 64 * 1024;
    const MAX_ARCHIVE_BYTES: u64 = 3 * 1024 * 1024 * 1024;
    const MAX_EXPANDED_BYTES: u64 = 4 * 1024 * 1024 * 1024;

    let manifest_url = Url::parse(manifest_url).map_err(|e| format!("Invalid builder manifest URL: {e}"))?;
    if manifest_url.scheme() != "https" { return Err("The builder download must use HTTPS.".into()); }
    let client = reqwest::blocking::Client::builder()
        .https_only(true)
        .user_agent(concat!("CrashLauncher/", env!("CARGO_PKG_VERSION")))
        .build().map_err(|e| format!("Could not initialize secure downloads: {e}"))?;

    emit_builder_log(app, logs, "Checking for the Windows game builder…".into());
    emit_download_progress(app, "manifest", 0, 0, "Checking builder version");
    let response = client.get(manifest_url.clone()).send()
        .and_then(reqwest::blocking::Response::error_for_status)
        .map_err(|e| format!("Could not download the builder manifest: {e}"))?;
    if response.content_length().map(|n| n > MAX_MANIFEST_BYTES).unwrap_or(false) {
        return Err("Builder manifest is unexpectedly large.".into());
    }
    let mut manifest_bytes = Vec::new();
    response.take(MAX_MANIFEST_BYTES + 1).read_to_end(&mut manifest_bytes)
        .map_err(|e| format!("Could not read the builder manifest: {e}"))?;
    if manifest_bytes.len() as u64 > MAX_MANIFEST_BYTES { return Err("Builder manifest is unexpectedly large.".into()); }
    let manifest: BuilderReleaseManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|e| format!("Builder manifest is invalid: {e}"))?;
    if manifest.schema_version != 1 || manifest.platform != "windows-x86_64" {
        return Err("This builder release is not compatible with this Windows PC.".into());
    }
    if manifest.version.is_empty() || manifest.version.len() > 64
        || !manifest.version.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)) {
        return Err("Builder manifest has an invalid version.".into());
    }
    if manifest.archive_size == 0 || manifest.archive_size > MAX_ARCHIVE_BYTES
        || manifest.archive_sha256.len() != 64
        || !manifest.archive_sha256.bytes().all(|b| b.is_ascii_hexdigit())
        || manifest.unpacked_size == 0 || manifest.unpacked_size > MAX_EXPANDED_BYTES
        || manifest.file_count == 0 || manifest.file_count > 50_000 {
        return Err("Builder manifest has invalid archive metadata.".into());
    }
    let archive_url = manifest_url.join(&manifest.archive_url)
        .map_err(|e| format!("Builder archive URL is invalid: {e}"))?;
    if archive_url.scheme() != "https" { return Err("The builder archive must use HTTPS.".into()); }

    let cache_dir = data_dir(app).join("builder-packs");
    fs::create_dir_all(&cache_dir).map_err(|e| format!("Could not create builder cache: {e}"))?;
    let pack_dir = cache_dir.join(format!("{}-{}", manifest.version, &manifest.archive_sha256[..12]));
    let marker = pack_dir.join(".crash-builder-release.json");
    if valid_builder_pack(&pack_dir)
        && fs::read(&marker).ok().as_deref() == Some(manifest_bytes.as_slice()) {
        emit_download_progress(app, "ready", 1, 1, "Using cached builder pack");
        return Ok(pack_dir);
    }

    let nonce = format!("{}-{}", std::process::id(), std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis());
    let archive_path = cache_dir.join(format!("download-{nonce}.zip"));
    let stage_dir = cache_dir.join(format!("extract-{nonce}"));
    let outcome = (|| -> Result<PathBuf, String> {
        emit_builder_log(app, logs, format!("Downloading builder pack {}…", manifest.version));
        let mut response = client.get(archive_url).send()
            .and_then(reqwest::blocking::Response::error_for_status)
            .map_err(|e| format!("Could not download the builder pack: {e}"))?;
        if response.content_length().map(|n| n != manifest.archive_size).unwrap_or(false) {
            return Err("Builder archive size does not match its manifest.".into());
        }
        let mut output = fs::File::create(&archive_path).map_err(|e| e.to_string())?;
        let mut digest = Sha256::new();
        let mut buffer = vec![0u8; 1 << 20];
        let mut received = 0u64;
        let mut last_emit = std::time::Instant::now();
        loop {
            let count = response.read(&mut buffer).map_err(|e| format!("Builder download failed: {e}"))?;
            if count == 0 { break; }
            received = received.saturating_add(count as u64);
            if received > manifest.archive_size || received > MAX_ARCHIVE_BYTES {
                return Err("Builder archive exceeded its declared size.".into());
            }
            digest.update(&buffer[..count]);
            output.write_all(&buffer[..count]).map_err(|e| e.to_string())?;
            if last_emit.elapsed() >= Duration::from_millis(150) {
                emit_download_progress(app, "download", received, manifest.archive_size, "Downloading verified builder tools");
                last_emit = std::time::Instant::now();
            }
        }
        output.flush().map_err(|e| e.to_string())?;
        if received != manifest.archive_size || !hex(&digest.finalize()).eq_ignore_ascii_case(&manifest.archive_sha256) {
            return Err("Builder archive failed its size or SHA-256 check.".into());
        }
        emit_download_progress(app, "download", received, manifest.archive_size, "Download verified");

        fs::create_dir(&stage_dir).map_err(|e| e.to_string())?;
        let file = fs::File::open(&archive_path).map_err(|e| e.to_string())?;
        let mut zip = zip::ZipArchive::new(file).map_err(|e| format!("Builder archive is invalid: {e}"))?;
        if zip.len() > 50_000 || zip.len() != manifest.file_count { return Err("Builder archive file count does not match its manifest.".into()); }
        let zip_len = zip.len();
        let mut expanded = 0u64;
        for i in 0..zip_len {
            let mut entry = zip.by_index(i).map_err(|e| format!("Could not read builder archive: {e}"))?;
            let relative = entry.enclosed_name().ok_or("Builder archive contains an unsafe path.")?.to_path_buf();
            if entry.unix_mode().map(|m| m & 0o170000 == 0o120000).unwrap_or(false) {
                return Err("Builder archive contains an unsupported symbolic link.".into());
            }
            expanded = expanded.checked_add(entry.size()).ok_or("Builder archive size overflow.")?;
            if expanded > MAX_EXPANDED_BYTES { return Err("Expanded builder archive is too large.".into()); }
            let target = stage_dir.join(relative);
            if entry.is_dir() {
                fs::create_dir_all(&target).map_err(|e| e.to_string())?;
            } else {
                if let Some(parent) = target.parent() { fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
                let mut output = fs::File::create(&target).map_err(|e| e.to_string())?;
                std::io::copy(&mut entry, &mut output).map_err(|e| format!("Could not extract builder pack: {e}"))?;
            }
            drop(entry);
            if i % 100 == 0 || i + 1 == zip_len {
                emit_download_progress(app, "extract", (i + 1) as u64, zip_len as u64, "Installing builder files");
            }
        }
        if expanded != manifest.unpacked_size { return Err("Builder archive contents do not match the declared unpacked size.".into()); }
        if !valid_builder_pack(&stage_dir) { return Err("Builder pack is missing required tools or source files.".into()); }
        fs::write(stage_dir.join(".crash-builder-release.json"), &manifest_bytes).map_err(|e| e.to_string())?;
        if pack_dir.exists() { fs::remove_dir_all(&pack_dir).map_err(|e| format!("Could not replace an older builder pack: {e}"))?; }
        fs::rename(&stage_dir, &pack_dir).map_err(|e| format!("Could not install builder pack: {e}"))?;
        emit_download_progress(app, "ready", 1, 1, "Builder pack ready");
        emit_builder_log(app, logs, format!("Builder pack {} installed and verified.", manifest.version));
        Ok(pack_dir.clone())
    })();
    let _ = fs::remove_file(&archive_path);
    if outcome.is_err() { let _ = fs::remove_dir_all(&stage_dir); }
    outcome
}

#[tauri::command]
async fn build_runtime(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    if !game_ready(&app) { return Err("Prepare and verify your disc first.".into()); }
    if state.game.child.lock().unwrap().as_mut().map(|c| matches!(c.try_wait(), Ok(None))).unwrap_or(false) {
        return Err("Close the game before rebuilding its runtime.".into());
    }
    let root = builder_root(&app);
    let manifest_url = builder_manifest_url().map(str::to_owned);
    if root.is_none() && manifest_url.is_none() { return Err("The verified game-builder download is not configured in this launcher.".into()); }
    let release = active_release(&app);
    let dol = game_dir(&app).join("sys/main.dol");
    let dol_sha = hex(&Sha256::digest(fs::read(&dol).map_err(|e| e.to_string())?));
    let dest = data_dir(&app).join("builds").join(builder_identity::build_dir_name(&dol_sha));
    let exe = dest.join("storage/toolchain/CrashNative.exe");
    if state.building.swap(true, Ordering::SeqCst) { return Err("A runtime build is already running.".into()); }
    let handle = app.clone();
    let logs = state.logs.clone();
    let output = tauri::async_runtime::spawn_blocking(move || -> Result<PathBuf, String> {
        fs::create_dir_all(dest.parent().unwrap()).map_err(|e| e.to_string())?;
        let pack = match root {
            Some(r) => pack_root(&handle).filter(|p| r.starts_with(p.join("native-builder"))),
            None => Some(download_builder_pack(&handle, &logs, manifest_url.as_deref().unwrap())?),
        };
        let root = if let Some(pack) = &pack { pack.join("native-builder") }
            else { builder_root(&handle).ok_or("The game builder is missing from this install.")? };
        let script = root.join("tools/clean_room_build.py");
        let index = if release.game_id == releases::DEFAULT { builder_index(&root) } else { Some(root.join(release.index)).filter(|p| p.is_file()) }
            .ok_or_else(|| format!("This game builder has no function index for the {} release ({}). Update the launcher.", release.label, release.game_id))?;
        // Bundled toolchain first; fall back to whatever is on PATH for development checkouts.
        let (python, path_env) = match &pack {
            Some(p) => {
                let tc = p.join("toolchain");
                let dirs = [tc.join("mingw64/bin"), tc.join("python")];
                let old = std::env::var_os("PATH").unwrap_or_default();
                let joined = std::env::join_paths(dirs.iter().cloned().chain(std::env::split_paths(&old))).map_err(|e| e.to_string())?;
                (tc.join("python/python.exe"), Some(joined))
            }
            None => (PathBuf::from("python"), None),
        };
        let mut cmd = Command::new(&python);
        if let Some(path) = path_env { cmd.env("PATH", path); }
        if let Some(p) = &pack { cmd.arg("-X").arg("utf8"); cmd.env("PYTHONHOME", p.join("toolchain/python")); }
        cmd.arg(&script).arg("--dest").arg(&dest).arg("--dol").arg(&dol).arg("--index").arg(&index)
            .arg("--resume")
            .arg("--expected-builder-revision").arg(builder_identity::revision())
            .arg("--jobs").arg(std::thread::available_parallelism().map(|n| n.get().min(10).to_string()).unwrap_or_else(|_| "4".into())).arg("--reuse-deps")
            .current_dir(root.parent().unwrap()).stdout(Stdio::piped()).stderr(Stdio::piped()).stdin(Stdio::null());
        if let Some(p) = &pack { cmd.arg("--deps").arg(p.join("deps")); }
        #[cfg(windows)]
        { use std::os::windows::process::CommandExt; cmd.creation_flags(0x08000000); }
        let mut child = cmd.spawn().map_err(|e| format!("Could not start Python builder: {e}"))?;
        let out = child.stdout.take().map(|r| { let a=handle.clone(); let l=logs.clone(); std::thread::spawn(move || pump_builder(a,l,r)) });
        let err = child.stderr.take().map(|r| { let a=handle.clone(); let l=logs.clone(); std::thread::spawn(move || pump_builder(a,l,r)) });
        let status = child.wait().map_err(|e| e.to_string())?;
        if let Some(t) = out { let _ = t.join(); }
        if let Some(t) = err { let _ = t.join(); }
        if !status.success() { return Err(format!("Native build failed with {status}. See the Log tab for its output.")); }
        if !exe.is_file() { return Err("The builder completed without producing CrashNative.exe.".into()); }
        if !builder_identity::runtime_matches(&exe,&dol_sha) { return Err("The build did not record the required native patch revision.".into()); }
        Ok(exe)
    }).await.map_err(|e| e.to_string());
    state.building.store(false, Ordering::SeqCst);
    let exe = output??;
    let mut settings = state.settings.lock().unwrap();
    settings.runtime_override = Some(exe.display().to_string());
    fs::write(settings_path(&app), serde_json::to_vec_pretty(&*settings).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let _ = app.emit("runtime-build-log", vec![BuilderLine { line: format!("Runtime ready: {}", exe.display()) }]);
    Ok(())
}

// ---------------------------------------------------------------- launch

fn ensure_runtime(app: &AppHandle, s: &Settings) -> Result<PathBuf, String> {
    if let Some(p) = &s.runtime_override {
        if Path::new(p).starts_with(data_dir(app).join("builds")) && !runtime_ready(app,s) {
            return Err("Rebuild the native game to apply the latest renderer and overlay updates.".into());
        }
        return if Path::new(p).exists() { Ok(PathBuf::from(p)) } else { Err(format!("Custom runtime not found: {p}")) };
    }
    if embedded::RUNTIME_SIZE == 0 { return Err("This launcher build has no embedded runtime. Set a custom runtime in Settings under Files.".into()); }
    if !embedded_runtime_fits(app) { return Err(format!("The built-in runtime is for another release. Build the game from your {} disc first.", active_release(app).label)); }
    let exe = runtime_exe_path(app);
    if exe.exists() && fs::metadata(&exe).map(|m| m.len() as usize == embedded::RUNTIME_SIZE).unwrap_or(false) { return Ok(exe); }
    let dir = exe.parent().unwrap();
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    // clear older runtimes
    if let Ok(rd) = fs::read_dir(dir) { for e in rd.flatten() { let _ = fs::remove_file(e.path()); } }
    let tmp = exe.with_extension("tmp");
    let mut decoder = zstd::Decoder::new(RUNTIME_ZST).map_err(|e| e.to_string())?;
    let mut out = fs::File::create(&tmp).map_err(|e| e.to_string())?;
    std::io::copy(&mut decoder, &mut out).map_err(|e| e.to_string())?;
    drop(out);
    let check = hex(&Sha256::digest(fs::read(&tmp).map_err(|e| e.to_string())?));
    if check != embedded::RUNTIME_SHA256 { let _ = fs::remove_file(&tmp); return Err("Runtime unpack failed its integrity check.".into()); }
    fs::rename(&tmp, &exe).map_err(|e| e.to_string())?;
    Ok(exe)
}

/// Wii system-menu language numbers for the languages this disc ships archives for.
fn language_code(lang: &str) -> Option<u8> {
    Some(match lang { "en" => 1, "de" => 2, "fr" => 3, "es" => 4, "it" => 5, "nl" => 6, _ => return None })
}

/// A real Wii SYSCONF image (the file the SDK's settings library reads): "SCv0", item count, count+1 item offsets (the last one marks
/// the end of the items), the items, and "SCed" in the last four bytes. The game's loader rejects the whole file unless every offset
/// equals the running cursor and the final offset equals the end of the items.
/// Item header byte = (type << 5) | (name length - 1); type 3 is a byte, type 5 a big-endian long.
fn build_sysconf(widescreen: bool, language: u8) -> Vec<u8> {
    enum V { Byte(u8), Long(u32) }
    let items = [
        ("IPL.CB", V::Long(0)),
        ("IPL.AR", V::Byte(widescreen as u8)),
        ("IPL.E60", V::Byte(0)),
        ("IPL.PGS", V::Byte(0)),
        ("IPL.LNG", V::Byte(language)),
    ];
    let mut buf = vec![0u8; 0x4000];
    buf[0..4].copy_from_slice(b"SCv0");
    buf[4..6].copy_from_slice(&(items.len() as u16).to_be_bytes());
    let mut pos = 6 + 2 * (items.len() + 1);
    for (i, (name, value)) in items.iter().enumerate() {
        buf[6 + 2 * i..8 + 2 * i].copy_from_slice(&(pos as u16).to_be_bytes());
        let ty: u8 = match value { V::Byte(_) => 3, V::Long(_) => 5 };
        buf[pos] = (ty << 5) | (name.len() as u8 - 1);
        pos += 1;
        buf[pos..pos + name.len()].copy_from_slice(name.as_bytes());
        pos += name.len();
        match value {
            V::Byte(b) => { buf[pos] = *b; pos += 1; }
            V::Long(l) => { buf[pos..pos + 4].copy_from_slice(&l.to_be_bytes()); pos += 4; }
        }
    }
    let end = 6 + 2 * items.len();
    buf[end..end + 2].copy_from_slice(&(pos as u16).to_be_bytes());
    buf[0x3FFC..0x4000].copy_from_slice(b"SCed");
    buf
}

/// The runtime's virtual NAND lives in <profile>/nand. Rewritten on every launch so the launcher settings always win.
/// Re-applies the current title-logo tiles to an already prepared game folder when it was patched with an older version.
fn ensure_title_art(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    let dir = game_dir(app);
    let marker = dir.join(TITLE_ART_MARKER);
    let art = dir.join(TITLE_ART_DIR);
    let current = fs::read_to_string(&marker).ok();
    if current.as_deref().map(str::trim) == Some(TITLE_ART_VERSION) && art.join("logo.png").is_file() { return Ok(()); }
    // Start from the disc's own tiles: kept beside the game since art version 4, untouched in the archive if it was never
    // patched, otherwise read again from the disc image.
    let kept: Option<Vec<Vec<u8>>> = (0..title_art::TILES.len()).map(|i| fs::read(art.join(format!("original-tile-{i}.png"))).ok()).collect();
    let originals = match (kept, current) {
        (Some(tiles), _) => Some(tiles),
        (None, None) => None,
        (None, Some(_)) => match settings.iso_path.as_deref().map(|p| original_title_tiles_from_disc(Path::new(p))) {
            Some(Ok(tiles)) => Some(tiles),
            // Without the disc the previous title art stays; the launcher shows the wordmark alone.
            _ => return Ok(()),
        },
    };
    patch_title_logo(&dir.join(RCF_REL_PATH), originals, &art.join("logo.png"))?;
    fs::write(&marker, TITLE_ART_VERSION).map_err(|e| e.to_string())
}

fn write_sysconf(app: &AppHandle, s: &Settings) -> Result<(), String> {
    let path = profile_dir(app).join("nand/shared2/sys/SYSCONF");
    if let Some(parent) = path.parent() { fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
    // Releases ship different language archives (the USA disc has no French, German, Spanish, Italian or Dutch one).
    let archive = match s.language.as_str() { "de" => "german", "fr" => "french", "es" => "spanish", "it" => "italian", "nl" => "dutch", _ => "english" };
    let lang = if game_dir(app).join("files").join(format!("{archive}.rcf")).is_file() { language_code(&s.language).unwrap_or(1) } else { 1 };
    fs::write(&path, build_sysconf(s.widescreen, lang)).map_err(|e| format!("Could not write the system settings: {e}"))
}

/// Writes the controller mapping. The current runtime reads a single file for controller 0 from `NWII_INPUT_MAP`, so that
/// file gets slot 0's layout; every slot additionally gets `input-maps/controller-<slot>.txt` for a runtime that loads a
/// map per controller. Called before launch and again on every Controls edit, so a running runtime that watches the files
/// picks a remap up immediately.
fn write_input_map(app: &AppHandle, s: &Settings) -> Result<PathBuf, String> {
    let profile = profile_dir(app);
    fs::create_dir_all(&profile).map_err(|e| e.to_string())?;
    let setup = s.controls.clone().sanitized();
    let dir = profile.join("input-maps");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    write_prompt_mode(app, s)?;
    // Drop stale per-slot files so a removed controller's layout cannot linger.
    if let Ok(entries) = fs::read_dir(&dir) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with("controller-") && name.ends_with(".txt") { let _ = fs::remove_file(entry.path()); }
        }
    }
    for (name, text) in setup.map_files() {
        fs::write(dir.join(&name), text).map_err(|e| format!("Could not write the controller mapping: {e}"))?;
    }
    let path = profile.join("input-map.txt");
    fs::write(&path, setup.layout("0").to_map_file()).map_err(|e| format!("Could not write the controller mapping: {e}"))?;
    Ok(path)
}

fn write_prompt_mode(app: &AppHandle, s: &Settings) -> Result<(), String> {
    let dir = profile_dir(app).join("input-maps");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    fs::write(dir.join("prompt-mode.txt"), format!("{} {}\n", s.input_mode, s.button_prompts))
        .map_err(|e| format!("Could not update live button prompts: {e}"))
}

/// Persists the Controls page and rewrites the mapping files at once, so edits apply without relaunching the game.
#[tauri::command]
fn save_controls(app: AppHandle, state: State<AppState>, controls: controls::ControlSetup) -> Result<(), String> {
    let mut s = state.settings.lock().unwrap();
    s.controls = controls.sanitized();
    fs::write(settings_path(&app), serde_json::to_vec_pretty(&*s).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    write_input_map(&app, &s)?;
    let rcf = game_dir(&app).join(RCF_REL_PATH);
    if rcf.exists() {
        prompts::live_pack(&rcf, &s.controls.layout("0"), &profile_dir(&app).join("button-prompts.bin"))?;
    }
    Ok(())
}

fn write_config(app: &AppHandle, s: &Settings) -> Result<PathBuf, String> {
    let profile = profile_dir(app);
    fs::create_dir_all(&profile).map_err(|e| e.to_string())?;
    let path = profile.join("config.toml");
    let release = active_release(app);
    let text = format!(
r#"# Generated by Crash Launcher. Edit settings in the launcher instead.
[system]
platform = "Wii"

[game]
id = "{game_id}"

[graphics]
backend = "OpenGL"
resolution_scale = {scale}
window_width = {w}
window_height = {h}
vsync = {vsync}

[input]
mode = {mode}
gyro_sensitivity = {gyro:.2}
pointer_speed = {pointer:.2}

[hle]
ext_interrupt_dispatch = 0x{dispatch:08X}
sched_lock_offset = 0x{sched_lock:X}
"#,
        game_id = release.game_id, dispatch = release.ext_interrupt_dispatch, sched_lock = release.sched_lock_offset,
        scale = s.resolution_scale, w = s.window_width, h = s.window_height, vsync = s.vsync,
        mode = s.input_mode, gyro = s.gyro_sensitivity, pointer = s.pointer_speed);
    fs::write(&path, text).map_err(|e| e.to_string())?;
    Ok(path)
}

#[derive(Clone, Serialize)]
struct LogLine { line: String, stream: &'static str }

fn pump(app: AppHandle, logs: Arc<Mutex<VecDeque<String>>>, stream: &'static str, reader: impl Read + Send + 'static) {
    let tx = batched_emitter::<LogLine>(app.clone(), "game-log");
    std::thread::spawn(move || {
        for line in BufReader::new(reader).split(b'\n').flatten() {
            let line = String::from_utf8_lossy(&line).trim_end_matches('\r').to_string();
            if line.is_empty() { continue; }
            { let mut l = logs.lock().unwrap(); if l.len() >= 4000 { l.pop_front(); } l.push_back(line.clone()); }
            if line.starts_with("[SaveState] saved") { let _ = app.emit("state-saved", ()); }
            let _ = tx.send(LogLine { line, stream });
        }
    });
}

#[tauri::command]
async fn launch_game(app: AppHandle, state: State<'_, AppState>, state_id: Option<String>, episode: Option<String>, skip_menus: Option<bool>) -> Result<(), String> {
    if state.building.load(Ordering::Relaxed) { return Err("Wait for the runtime build to finish before launching.".into()); }
    {
        let mut guard = state.game.child.lock().unwrap();
        if let Some(child) = guard.as_mut() { if matches!(child.try_wait(), Ok(None)) { return Err("The game is already running.".into()); } }
        *guard = None;
    }
    if !game_ready(&app) { return Err("Prepare the game from your ISO first.".into()); }
    let load_state = match state_id.as_deref() {
        Some(id) => Some(savestates::state_file(&states_dir(&app), id).ok_or("That save state no longer exists.")?),
        None => None,
    };
    let settings = state.settings.lock().unwrap().clone();
    ensure_title_art(&app, &settings)?;
    // Play always starts the game normally. Only the Episodes page names an episode: the menu script is then edited in place, either so
    // the game opens straight in it (skipping the boot movies and menus) or so a new game started from the menus begins there.
    if let Some(e) = &episode { if !chapters::is_chapter(e) && !chapters::is_dev_level(e) { return Err(format!("Unknown episode {e}.")); } }
    let episode = if load_state.is_some() { None } else { episode };
    let rcf = game_dir(&app).join(RCF_REL_PATH);
    let normal = || direct::revert(&rcf).and_then(|_| chapters::apply(&rcf, chapters::DEFAULT).map(|_| ()));
    match &episode {
        // Test levels can only be started straight in; the menus' own new-game slots are too narrow for their names.
        Some(e) if chapters::is_dev_level(e) || skip_menus.unwrap_or(settings.start_direct) => { direct::revert(&rcf)?; chapters::apply(&rcf, chapters::DEFAULT)?; direct::apply(&rcf, e)?; }
        Some(e) => { direct::revert(&rcf)?; chapters::apply(&rcf, e)?; }
        None => { let _ = normal(); }
    }
    // Keep original glyphs in guest memory. Runtime substitution handles both
    // fixed families and live changes without reloading the game's font.
    let prompt_family = match settings.button_prompts.as_str() {
        "wii" | "auto" | "follow" => None,
        "xbox" => Some(prompts::Family::Xbox),
        "playstation" => Some(prompts::Family::PlayStation),
        "keyboard" => Some(prompts::Family::Keyboard),
        _ if settings.input_mode == 6 => Some(prompts::Family::Keyboard),
        _ => {
            let pads = state.pads.lock().unwrap();
            Some(pads.iter().find(|p| p.slot == 0).or(pads.first()).map_or(prompts::Family::Keyboard, |p| prompts::Family::of_controller(&p.name)))
        }
    };
    let prompt_result = prompts::revert(&rcf);
    if let Err(e) = prompt_result { let _ = app.emit("game-log", vec![LogLine { line: format!("[Launcher] button prompts unchanged: {e}"), stream: "out" }]); }
    let exe = { let (a, s) = (app.clone(), settings.clone()); tauri::async_runtime::spawn_blocking(move || ensure_runtime(&a, &s)).await.map_err(|e| e.to_string())?? };
    let config = write_config(&app, &settings)?;
    write_sysconf(&app, &settings)?;
    let input_map = write_input_map(&app, &settings)?;
    let prompt_pack = profile_dir(&app).join("button-prompts.bin");
    let live_prompts = match prompts::live_pack(&rcf, &settings.controls.layout("0"), &prompt_pack) {
            Ok(()) => true,
            Err(e) => {
                let _ = app.emit("game-log", vec![LogLine { line: format!("[Launcher] live button prompts unavailable: {e}"), stream: "out" }]);
                if let Some(family) = prompt_family { let _ = prompts::apply(&rcf, family, &settings.controls.layout("0")); }
                false
            }
        };
    let render_wait = render_pacing::write(&profile_dir(&app), settings.frame_pacing)?;
    let shader_cache = persistent_program_cache(&data_dir(&app), &exe);
    let mut cmd = Command::new(&exe);
    cmd.arg(game_dir(&app)).arg(&config).current_dir(profile_dir(&app))
        .stdout(Stdio::piped()).stderr(Stdio::piped()).stdin(Stdio::null())
        .env("NWII_FPS_LOG", if settings.fps_log { "1" } else { "" });
    if !settings.fps_log { cmd.env_remove("NWII_FPS_LOG"); }
    if settings.performance_census { cmd.env("NWII_PERF_CENSUS", "1"); }
    else { cmd.env_remove("NWII_PERF_CENSUS"); }
    if settings.performance_census && settings.performance_histogram { cmd.env("NWII_PERF_PC_HISTOGRAM", "1"); }
    else { cmd.env_remove("NWII_PERF_PC_HISTOGRAM"); }
    frame_limit::apply(&mut cmd, settings.custom_fps);
    render_pacing::apply(&mut cmd, settings.frame_pacing, &render_wait);
    camera_glide::apply(&mut cmd, settings.camera_glide, settings.camera_glide_ms);
    direct_fifo::apply(&mut cmd, settings.direct_fifo);
    gx_batching::apply(&mut cmd, &settings.gx_batching);
    if settings.vsync { cmd.env("NWII_VSYNC", "1"); } else { cmd.env_remove("NWII_VSYNC"); }
    if settings.safe_gpu_sync { cmd.env("NWII_SYNC_GPU", "1"); } else { cmd.env_remove("NWII_SYNC_GPU"); }
    cmd.env("NWII_WALLTB", "1");
    cmd.env("NWII_INPUT_MAP", &input_map);
    if live_prompts { cmd.env("NWII_PROMPT_PACK", &prompt_pack); }
    else { cmd.env_remove("NWII_PROMPT_PACK"); }
    // Per-controller layouts (controller-<slot>.txt) for runtimes that map each player separately and reload on change.
    cmd.env("NWII_INPUT_MAP_DIR", profile_dir(&app).join("input-maps"));
    let states = states_dir(&app);
    let _ = fs::create_dir_all(&states);
    cmd.env("NWII_STATE_DIR", &states);
    let dol_sha = fs::read_to_string(game_marker(&app)).unwrap_or_default();
    if dol_sha.trim().len() == 64 { cmd.env("NWII_DOL_SHA256", dol_sha.trim()); }
    match &load_state { Some(p) => { cmd.env("NWII_LOAD_STATE", p); } None => { cmd.env_remove("NWII_LOAD_STATE"); } }
    if let Some(cache) = shader_cache { cmd.env("NWII_PROGRAM_CACHE_DIR", cache); }
    if settings.game_speed > 1.0 { cmd.env("NWII_TURBO", format!("{:.2}", settings.game_speed)); } else { cmd.env_remove("NWII_TURBO"); }
    if settings.anisotropy > 1 { cmd.env("NWII_ANISO", settings.anisotropy.to_string()); } else { cmd.env_remove("NWII_ANISO"); }
    if settings.display_mode == "borderless" { cmd.env("NWII_WINDOW_MODE", "borderless"); } else { cmd.env_remove("NWII_WINDOW_MODE"); }
    #[cfg(windows)]
    { use std::os::windows::process::CommandExt; cmd.creation_flags(0x08000000); } // CREATE_NO_WINDOW: no console flash
    state.logs.lock().unwrap().clear();
    let mut child = cmd.spawn().map_err(|e| format!("Could not start the game: {e}"))?;
    if let Some(o) = child.stdout.take() { pump(app.clone(), state.logs.clone(), "out", o); }
    if let Some(e) = child.stderr.take() { pump(app.clone(), state.logs.clone(), "err", e); }
    let slot = state.game.child.clone();
    *slot.lock().unwrap() = Some(child);
    let handle = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(400));
        let mut guard = slot.lock().unwrap();
        match guard.as_mut() {
            Some(c) => match c.try_wait() {
                Ok(Some(status)) => { let code = status.code().unwrap_or(-1); *guard = None; let _ = handle.emit("game-exit", code); break; }
                Ok(None) => {}
                Err(_) => { *guard = None; let _ = handle.emit("game-exit", -1); break; }
            },
            None => { let _ = handle.emit("game-exit", 0); break; }
        }
    });
    Ok(())
}

#[tauri::command]
fn stop_game(state: State<AppState>) -> bool {
    let mut guard = state.game.child.lock().unwrap();
    match guard.as_mut() { Some(c) => c.kill().is_ok(), None => false }
}

#[tauri::command]
fn get_logs(state: State<AppState>) -> Vec<String> { state.logs.lock().unwrap().iter().cloned().collect() }

// ---------------------------------------------------------------- soundtrack

#[tauri::command]
async fn list_soundtrack(app: AppHandle) -> Result<Vec<soundtrack::Track>, String> {
    if !game_ready(&app) { return Err("Prepare your game first. The soundtrack is read from your disc files.".into()); }
    let dir = game_dir(&app);
    tauri::async_runtime::spawn_blocking(move || soundtrack::list_tracks(&dir)).await.map_err(|e| e.to_string())?
}

/// One decoded track as a complete WAV file, sent to the page as raw bytes for playback.
#[tauri::command]
async fn get_track_wav(app: AppHandle, id: usize) -> Result<tauri::ipc::Response, String> {
    if !game_ready(&app) { return Err("Prepare your game first.".into()); }
    let dir = game_dir(&app);
    let wav = tauri::async_runtime::spawn_blocking(move || soundtrack::track_wav(&dir, id)).await.map_err(|e| e.to_string())??;
    Ok(tauri::ipc::Response::new(wav))
}

#[derive(Clone, Serialize)]
struct ExportProgress { done: usize, total: usize, file: String }

fn safe_file_name(name: &str) -> String {
    name.chars().map(|c| if r#"<>:"/\|?*"#.contains(c) || c.is_control() { '_' } else { c }).collect()
}

/// Asks for a folder, then writes the chosen tracks there as WAV files (one sub-folder per category).
/// Returns the number of files written, or None when the folder prompt was cancelled.
#[tauri::command]
async fn export_tracks(app: AppHandle, ids: Vec<usize>) -> Result<Option<usize>, String> {
    if !game_ready(&app) { return Err("Prepare your game first.".into()); }
    let Some(picked) = app.dialog().file().blocking_pick_folder() else { return Ok(None) };
    let root = picked.into_path().map_err(|e| e.to_string())?.join("Crash of the Titans Soundtrack");
    let dir = game_dir(&app);
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || -> Result<Option<usize>, String> {
        let tracks = soundtrack::list_tracks(&dir)?;
        let chosen: Vec<&soundtrack::Track> = ids.iter().filter_map(|id| tracks.iter().find(|t| t.id == *id)).collect();
        let mut used = std::collections::HashSet::new();
        for (n, t) in chosen.iter().enumerate() {
            let folder = root.join(match t.category.as_str() { "music" => "Music", "ambience" => "Ambience", other => other });
            fs::create_dir_all(&folder).map_err(|e| format!("Could not create {}: {e}", folder.display()))?;
            let stem = t.file.rsplit_once('.').map(|(s, _)| s).unwrap_or(&t.file);
            let mut name = format!("{}.wav", safe_file_name(stem));
            if !used.insert(folder.join(&name)) { name = format!("{}-{}.wav", safe_file_name(stem), t.id); used.insert(folder.join(&name)); }
            let _ = handle.emit("export-progress", ExportProgress { done: n, total: chosen.len(), file: name.clone() });
            let wav = soundtrack::track_wav(&dir, t.id)?;
            fs::write(folder.join(&name), wav).map_err(|e| format!("Could not write {name}: {e}"))?;
        }
        let _ = handle.emit("export-progress", ExportProgress { done: chosen.len(), total: chosen.len(), file: String::new() });
        Ok(Some(chosen.len()))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn open_folder(app: AppHandle, which: String) -> Result<(), String> {
    let path = match which.as_str() { "profile" => profile_dir(&app), "game" => game_dir(&app), "states" => states_dir(&app), _ => data_dir(&app) };
    let _ = fs::create_dir_all(&path);
    Command::new("explorer").arg(&path).spawn().map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn reset_game_files(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    if state.building.load(Ordering::Relaxed) { return Err("Wait for the runtime build to finish before resetting game files.".into()); }
    if state.game.child.lock().unwrap().is_some() { return Err("Close the game first.".into()); }
    fs::remove_dir_all(game_dir(&app)).or_else(|e| if e.kind() == std::io::ErrorKind::NotFound { Ok(()) } else { Err(e) }).map_err(|e| e.to_string())
}

#[tauri::command]
async fn list_states(app: AppHandle) -> Vec<savestates::StateInfo> {
    let dol_sha = fs::read_to_string(game_marker(&app)).unwrap_or_default();
    savestates::list(&states_dir(&app), dol_sha.trim())
}

#[tauri::command]
fn delete_state(app: AppHandle, id: String) -> Result<(), String> { savestates::delete(&states_dir(&app), &id) }

/// The launcher logo composed from the player's disc (PNG bytes); errors until the game is prepared.
#[tauri::command]
async fn get_logo(app: AppHandle, state: State<'_, AppState>) -> Result<tauri::ipc::Response, String> {
    if !game_ready(&app) { return Err("The game is not prepared yet.".into()); }
    let settings = state.settings.lock().unwrap().clone();
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || ensure_title_art(&handle, &settings)).await.map_err(|e| e.to_string())??;
    fs::read(game_dir(&app).join(TITLE_ART_DIR).join("logo.png")).map(tauri::ipc::Response::new).map_err(|_| "No composed logo yet.".to_string())
}

#[tauri::command]
fn list_chapters(app: AppHandle) -> Vec<chapters::ChapterInfo> { chapters::list(&game_dir(&app).join(RCF_REL_PATH)) }

/// Remembers the Episodes page's "Skip menus and cutscenes" choice.
#[tauri::command]
fn set_start_direct(app: AppHandle, state: State<AppState>, direct: bool) -> Result<(), String> {
    let mut s = state.settings.lock().unwrap();
    s.start_direct = direct;
    fs::write(settings_path(&app), serde_json::to_vec_pretty(&*s).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

#[tauri::command]
fn list_gamepads(state: State<AppState>) -> Vec<PadInfo> { state.pads.lock().unwrap().clone() }

#[tauri::command]
fn begin_capture(state: State<AppState>) {
    let mut c = state.capture.lock().unwrap();
    c.active = true; c.result = None;
}

#[tauri::command]
fn poll_capture(state: State<AppState>) -> Option<String> {
    state.capture.lock().unwrap().result.take()
}

#[tauri::command]
fn cancel_capture(state: State<AppState>) {
    let mut c = state.capture.lock().unwrap();
    c.active = false; c.result = None;
}

/// Controller inputs the runtime understands (`controls::PAD_INPUTS`), with the gilrs button each one comes from. The
/// array index is the identity used to detect a freshly pressed button for capture.
const CAPTURE_BUTTONS: &[(gilrs::Button, &str)] = &[
    (gilrs::Button::South, "South"), (gilrs::Button::East, "East"), (gilrs::Button::West, "West"), (gilrs::Button::North, "North"),
    (gilrs::Button::LeftTrigger, "LeftShoulder"), (gilrs::Button::RightTrigger, "RightShoulder"),
    (gilrs::Button::LeftTrigger2, "LeftTrigger"), (gilrs::Button::RightTrigger2, "RightTrigger"),
    (gilrs::Button::Select, "Back"), (gilrs::Button::Start, "Start"), (gilrs::Button::Mode, "Guide"),
    (gilrs::Button::LeftThumb, "LeftStick"), (gilrs::Button::RightThumb, "RightStick"),
    (gilrs::Button::DPadUp, "DpadUp"), (gilrs::Button::DPadDown, "DpadDown"),
    (gilrs::Button::DPadLeft, "DpadLeft"), (gilrs::Button::DPadRight, "DpadRight"),
];

fn spawn_gamepad_thread(pads: Arc<Mutex<Vec<PadInfo>>>, capture: Arc<Mutex<Capture>>) {
    std::thread::spawn(move || {
        use gilrs::{Axis, Gilrs};
        let mut gilrs = match Gilrs::new() { Ok(g) => g, Err(_) => return };
        let mut previous: Vec<usize> = Vec::new();
        loop {
            while gilrs.next_event().is_some() {}
            let mut list = Vec::new();
            let mut pressed_now: Vec<usize> = Vec::new();
            for (slot, (id, pad)) in gilrs.gamepads().enumerate() {
                let mut buttons = Vec::new();
                for (index, (button, name)) in CAPTURE_BUTTONS.iter().enumerate() {
                    if pad.is_pressed(*button) {
                        buttons.push((*name).to_string());
                        if !pressed_now.contains(&index) { pressed_now.push(index); }
                    }
                }
                let axes = [(Axis::LeftStickX, "LX"), (Axis::LeftStickY, "LY"), (Axis::RightStickX, "RX"), (Axis::RightStickY, "RY")]
                    .iter().map(|(a, n)| (n.to_string(), pad.value(*a))).collect();
                list.push(PadInfo {
                    id: usize::from(id), slot, name: pad.name().to_string(),
                    mapped: pad.mapping_source() != gilrs::MappingSource::None,
                    power: format!("{:?}", pad.power_info()), buttons, axes,
                });
            }
            {
                let mut c = capture.lock().unwrap();
                if c.active {
                    if let Some(&index) = pressed_now.iter().find(|&&i| !previous.contains(&i)) {
                        c.result = Some(CAPTURE_BUTTONS[index].1.to_string());
                        c.active = false;
                    }
                }
                previous = pressed_now;
            }
            *pads.lock().unwrap() = list;
            std::thread::sleep(Duration::from_millis(16));
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let pads = Arc::new(Mutex::new(Vec::new()));
    let capture = Arc::new(Mutex::new(Capture::default()));
    spawn_gamepad_thread(pads.clone(), capture.clone());
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
            let settings = load_settings(app.handle());
            app.manage(AppState {
                settings: Mutex::new(settings),
                game: Running { child: Arc::new(Mutex::new(None)) },
                logs: Arc::new(Mutex::new(VecDeque::new())),
                pads,
                capture,
                preparing: AtomicBool::new(false),
                building: Arc::new(AtomicBool::new(false)),
            });
            // Validate the builder pack once in the background so the first page refresh does not wait for it.
            let handle = app.handle().clone();
            std::thread::spawn(move || { let _ = builder_available(&handle); });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_state, game_running, save_settings, set_frame_pacing, set_camera_glide, pick_iso, inspect_iso, prepare_game, build_runtime, launch_game, stop_game, get_logs,
            open_folder, reset_game_files, list_gamepads, list_soundtrack, get_track_wav, export_tracks,
            save_controls, begin_capture, poll_capture, cancel_capture, list_states, delete_state, get_logo, list_chapters, set_start_direct
        ])
        .run(tauri::generate_context!())
        .expect("error while running the launcher");
}

#[cfg(test)]
mod tests {
    #[test]
    fn old_settings_keep_performance_census_disabled() {
        let mut settings: super::Settings = serde_json::from_str(r#"{"fps_log":true,"custom_fps":60}"#).unwrap();
        assert!(!settings.performance_census);
        assert!(!settings.performance_histogram);
        // Defaults now ship the measured-fastest GX settings (adaptive4 batching, compact commands).
        assert!(settings.direct_fifo);
        assert_eq!(settings.gx_batching,"adaptive4");
        assert!(settings.fps_log);
        assert_eq!(settings.custom_fps, Some(60.0));
        // Old profiles without the camera fields keep the shipped defaults.
        assert!(!settings.camera_glide);
        assert_eq!(settings.camera_glide_ms, 200);
        settings.performance_census = true;
        settings.performance_histogram = true;
        settings.direct_fifo = true;
        settings.gx_batching = "adaptive8".into();
        settings.camera_glide = true;
        settings.camera_glide_ms = 350;
        let saved = serde_json::to_string(&settings).unwrap();
        let restored: super::Settings = serde_json::from_str(&saved).unwrap();
        assert!(restored.performance_census);
        assert!(restored.performance_histogram);
        assert!(restored.direct_fifo);
        assert_eq!(restored.gx_batching,"adaptive8");
        assert!(restored.camera_glide);
        assert_eq!(restored.camera_glide_ms, 350);
    }
    #[test]
    fn program_cache_survives_build_revisions() {
        use super::*;
        let root=std::env::temp_dir().join(format!("crash-cache-migration-{}",std::process::id()));
        fs::create_dir(&root).unwrap();
        let old=root.join("builds/clean-room-old/storage/toolchain/program-cache");fs::create_dir_all(&old).unwrap();
        fs::write(old.join("0123456789abcdef.bin"),b"old binary").unwrap();fs::write(old.join("notes.txt"),b"ignored").unwrap();
        let first=persistent_program_cache(&root,&root.join("builds/clean-room-new/storage/toolchain/CrashNative.exe")).unwrap();
        assert_eq!(fs::read(first.join("0123456789abcdef.bin")).unwrap(),b"old binary");assert!(!first.join("notes.txt").exists());
        fs::write(first.join("0123456789abcdef.bin"),b"new binary").unwrap();
        let second=persistent_program_cache(&root,&root.join("builds/clean-room-next/storage/toolchain/CrashNative.exe")).unwrap();
        assert_eq!(first,second);assert_eq!(fs::read(second.join("0123456789abcdef.bin")).unwrap(),b"new binary");
        fs::remove_file(first.join("0123456789abcdef.bin")).unwrap();fs::remove_file(old.join("0123456789abcdef.bin")).unwrap();fs::remove_file(old.join("notes.txt")).unwrap();
        for path in [old.clone(),old.parent().unwrap().to_path_buf(),old.parent().unwrap().parent().unwrap().to_path_buf(),root.join("builds/clean-room-old"),root.join("builds"),first,root.join("cache"),root] {fs::remove_dir(path).unwrap();}
    }
    use super::*;

    /// Minimal SC reader following the SDK layout, to check build_sysconf from the reading side.
    fn sc_get(buf: &[u8], key: &str) -> Option<u32> {
        assert_eq!(&buf[0..4], b"SCv0");
        assert_eq!(&buf[0x3FFC..0x4000], b"SCed");
        let n = u16::from_be_bytes([buf[4], buf[5]]) as usize;
        let offset = |i: usize| u16::from_be_bytes([buf[6 + 2 * i], buf[7 + 2 * i]]) as usize;
        // The game's validation: items are contiguous starting after the (n + 1)-entry table, and the last entry is the end.
        let mut cursor = 6 + 2 * (n + 1);
        let mut found = None;
        for i in 0..n {
            let off = offset(i);
            assert_eq!(off, cursor, "item {i} offset");
            let ty = buf[off] >> 5;
            let len = (buf[off] & 0x1f) as usize + 1;
            let size = match ty { 3 | 7 => 1, 4 => 2, 5 => 4, 6 => 8, _ => panic!("type {ty}") };
            if &buf[off + 1..off + 1 + len] == key.as_bytes() {
                let v = &buf[off + 1 + len..];
                found = Some(match size { 1 => v[0] as u32, 4 => u32::from_be_bytes([v[0], v[1], v[2], v[3]]), _ => panic!("size {size}") });
            }
            cursor += 1 + len + size;
        }
        assert_eq!(offset(n), cursor, "end offset");
        assert!(cursor <= 0x3FFC);
        found
    }

    #[test]
    fn sysconf_carries_aspect_and_language() {
        let wide = build_sysconf(true, language_code("fr").unwrap());
        assert_eq!(wide.len(), 0x4000);
        assert_eq!(sc_get(&wide, "IPL.AR"), Some(1));
        assert_eq!(sc_get(&wide, "IPL.LNG"), Some(3));
        assert_eq!(sc_get(&wide, "IPL.E60"), Some(0));
        let narrow = build_sysconf(false, language_code("en").unwrap());
        assert_eq!(sc_get(&narrow, "IPL.AR"), Some(0));
        assert_eq!(sc_get(&narrow, "IPL.LNG"), Some(1));
        assert_eq!(sc_get(&narrow, "IPL.NOPE"), None);
        assert!(language_code("xx").is_none());
    }

    #[test]
    fn plain_path_strips_verbatim_prefix() {
        assert_eq!(plain_path(PathBuf::from(r"\\?\C:\a b\c")), PathBuf::from(r"C:\a b\c"));
        assert_eq!(plain_path(PathBuf::from(r"C:\x")), PathBuf::from(r"C:\x"));
        assert_eq!(plain_path(PathBuf::from(r"\\?\UNC\srv\share")), PathBuf::from(r"\\?\UNC\srv\share"));
    }

    /// Needs a real disc: CRASH_TEST_ISO=<path to RQJP7D image>. Patches a temp copy of default.rcf only.
    #[test]
    fn title_logo_patch_fits_real_archive() {
        let Some(iso) = std::env::var_os("CRASH_TEST_ISO") else { return };
        use nod::{common::PartitionKind, read::{DiscOptions, DiscReader, PartitionOptions}};
        let disc = DiscReader::new(Path::new(&iso), &DiscOptions::default()).unwrap();
        let mut part = disc.open_partition_kind(PartitionKind::Data, &PartitionOptions::default()).unwrap();
        let meta = part.meta().unwrap();
        let fst = meta.fst().unwrap();
        let node = fst.iter().find(|(_, n, p)| n.is_file() && p.ends_with("default.rcf")).map(|(_, n, _)| n).unwrap();
        let mut data = Vec::new();
        part.open_file(node).unwrap().read_to_end(&mut data).unwrap();
        let dir = std::env::temp_dir().join(format!("crash-test-title-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let tmp = dir.join("default.rcf");
        fs::write(&tmp, &data).unwrap();
        let logo = dir.join(".title-art/logo.png");
        patch_title_logo(&tmp, None, &logo).unwrap();
        let once = fs::read(&tmp).unwrap();
        assert_eq!(once.len(), data.len());
        // The disc's tiles were kept, and the same disc read straight from the image gives the same tiles.
        let kept: Vec<Vec<u8>> = (0..6).map(|i| fs::read(dir.join(format!(".title-art/original-tile-{i}.png"))).unwrap()).collect();
        assert_eq!(original_title_tiles_from_disc(Path::new(&iso)).unwrap(), kept);
        // Re-patching an already patched archive from the kept tiles gives the same result.
        patch_title_logo(&tmp, Some(kept), &logo).unwrap();
        assert_eq!(fs::read(&tmp).unwrap(), once);
        // Every patched tile is a paletted PNG of the original size; the launcher logo decodes.
        let mut f = fs::File::open(&tmp).unwrap();
        let (_, p3d) = frontend_package(&mut f, once.len() as u64).unwrap();
        for (png, &(_, _, w, h)) in slot_pngs(&p3d, &title_slots(&p3d).unwrap()).unwrap().iter().zip(title_art::TILES.iter()) {
            assert_eq!(png[25], 3);
            let img = title_art::decode_png(png).unwrap();
            assert_eq!((img.w, img.h), (w, h));
        }
        assert!(title_art::decode_png(&fs::read(&logo).unwrap()).unwrap().w > 300);
        if let Some(out) = std::env::var_os("CRASH_TEST_ART_OUT") {   // optional: keep the results for a visual check
            let out = PathBuf::from(out); fs::create_dir_all(&out).unwrap();
            fs::copy(&logo, out.join("logo.png")).unwrap();
            for (i, png) in slot_pngs(&p3d, &title_slots(&p3d).unwrap()).unwrap().iter().enumerate() { fs::write(out.join(format!("tile-{i}.png")), png).unwrap(); }
        }
        let _ = fs::remove_dir_all(&dir);
    }
}

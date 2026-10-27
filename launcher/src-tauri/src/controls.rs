//! Controller mapping chosen on the Controls page.
//!
//! Two sets of files are written before each launch and again after every edit (`save_controls`):
//!   * `input-maps/controller-<slot>.txt` — one layout per controller slot, and
//!   * `input-map.txt` — slot 0, which the runtime reads from `NWII_INPUT_MAP`
//!     (see `runtime-overlays/input_mapping.h`).
//! Writing on every edit is what lets a runtime that watches the file apply a remap while the game runs.
//!
//! A Wii input maps to one or more controller inputs (comma separated, as the runtime accepts). A missing entry keeps
//! the runtime's built-in default; an empty value deliberately unbinds the input. Only settings that differ from the
//! runtime's defaults are written for bindings; pointer and motion are written explicitly once the user sets them.

use serde::{Deserialize, Deserializer, Serialize};
use std::collections::BTreeMap;

/// Wii Remote / Nunchuk inputs the runtime knows, in the order the editor lists them.
pub const WII_INPUTS: &[(&str, &str)] = &[
    ("a", "A"), ("b", "B"), ("one", "1"), ("two", "2"), ("plus", "+"), ("minus", "-"), ("home", "Home"),
    ("up", "D-pad up"), ("down", "D-pad down"), ("left", "D-pad left"), ("right", "D-pad right"),
    ("c", "C"), ("z", "Z"),
];

/// Controller inputs the runtime knows (SDL game controller names), with labels as they appear on a common pad.
pub const PAD_INPUTS: &[(&str, &str)] = &[
    ("South", "A / Cross"), ("East", "B / Circle"), ("West", "X / Square"), ("North", "Y / Triangle"),
    ("LeftShoulder", "L1 / LB"), ("RightShoulder", "R1 / RB"), ("LeftTrigger", "L2 / LT"), ("RightTrigger", "R2 / RT"),
    ("Back", "Share / Back"), ("Start", "Options / Start"), ("Guide", "PS / Guide"),
    ("LeftStick", "L3"), ("RightStick", "R3"),
    ("DpadUp", "D-pad up"), ("DpadDown", "D-pad down"), ("DpadLeft", "D-pad left"), ("DpadRight", "D-pad right"),
    ("Touchpad", "Touchpad"), ("Misc1", "Mute / Mic / Create"), ("Paddle1", "Paddle 1"), ("Paddle2", "Paddle 2"),
    ("Paddle3", "Paddle 3"), ("Paddle4", "Paddle 4"),
];

/// Shake inputs are not Wii buttons; they start a motion burst on the chosen device.
pub const SHAKE_INPUTS: &[(&str, &str)] = &[("shake_remote", "Shake Wii Remote"), ("shake_nunchuk", "Shake Nunchuk"), ("recenter", "Recentre pointer")];

/// The Nunchuk stick from keys (player 1's keyboard; a controller's stick drives it directly).
pub const KEY_STICK_INPUTS: &[(&str, &str)] = &[("stick_up", "Stick up"), ("stick_down", "Stick down"), ("stick_left", "Stick left"), ("stick_right", "Stick right")];

/// The runtime's built-in bindings (input_mapping.h `default_map`), used for a Wii input with no explicit binding.
pub const DEFAULT_BINDINGS: &[(&str, &str)] = &[
    ("a", "South,Key:Space,Mouse:Left"), ("b", "East,RightTrigger,Mouse:Right"), ("one", "West,Key:1"), ("two", "North,Key:2"),
    ("plus", "Start,Key:Return"), ("minus", "Back,Key:Backspace"), ("home", "Guide"),
    ("up", "DpadUp,Key:Up"), ("down", "DpadDown,Key:Down"), ("left", "DpadLeft,Key:Left"), ("right", "DpadRight,Key:Right"),
    ("c", "LeftShoulder,Key:Q"), ("z", "RightShoulder,LeftTrigger,Key:Left Shift"),
    ("stick_up", "Key:W"), ("stick_down", "Key:S"), ("stick_left", "Key:A"), ("stick_right", "Key:D"),
    ("shake_remote", "Key:F"), ("shake_nunchuk", "Key:R"), ("recenter", "Mouse:Middle"),
];

/// Mouse buttons the runtime reads, as written after `Mouse:`.
pub const MOUSE_BUTTONS: &[&str] = &["Left", "Right", "Middle", "X1", "X2"];

const POINTERS: &[&str] = &["auto", "stick", "gyro", "both", "none"];
const MOTION_TARGETS: &[&str] = &["remote", "nunchuk", "both"];
/// Controller slots the runtime exposes (0..=3). Slot keys are the string form of the index.
pub const SLOTS: &[&str] = &["0", "1", "2", "3"];

fn is_wii_input(name: &str) -> bool {
    WII_INPUTS.iter().chain(SHAKE_INPUTS.iter()).chain(KEY_STICK_INPUTS.iter()).any(|(n, _)| *n == name)
}

/// The canonical spelling of one binding: a controller input, `Key:<SDL key name>` or `Mouse:<button>`; None for anything else.
pub fn canonical_binding(part: &str) -> Option<String> {
    let part = part.trim();
    if let Some(name) = canonical_pad(part) { return Some(name.to_string()); }
    let (head, rest) = part.split_once(':')?;
    let rest = rest.trim();
    if head.trim().eq_ignore_ascii_case("key") {
        let ok = !rest.is_empty() && rest.len() <= 24 && rest.chars().all(|c| c.is_ascii_graphic() || c == ' ') && !rest.contains([',', '=', '#']);
        return ok.then(|| format!("Key:{rest}"));
    }
    if head.trim().eq_ignore_ascii_case("mouse") {
        return MOUSE_BUTTONS.iter().find(|b| b.eq_ignore_ascii_case(rest)).map(|b| format!("Mouse:{b}"));
    }
    None
}

/// The canonical spelling of a controller input, or None when the name is not one the runtime knows.
fn canonical_pad(name: &str) -> Option<&'static str> {
    let name = name.trim();
    PAD_INPUTS.iter().find(|(n, _)| (*n).eq_ignore_ascii_case(name)).map(|(n, _)| *n)
}

/// One controller's layout. `bindings` values are comma-separated controller inputs; a missing key keeps the runtime
/// default and an empty value unbinds the input.
#[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
#[serde(default)]
pub struct Controls {
    pub bindings: BTreeMap<String, String>,
    /// "auto" follows the control scheme; otherwise stick, gyro, both or none.
    pub pointer: String,
    /// "left" or "right".
    pub nunchuk_stick: String,
    pub gyro_invert_x: bool,
    pub gyro_invert_y: bool,
    pub motion: bool,
    pub motion_target: String,
    pub motion_sensitivity: f64,
    pub motion_invert_x: bool,
    pub motion_invert_y: bool,
}

impl Default for Controls {
    fn default() -> Self {
        Self {
            bindings: BTreeMap::new(),
            pointer: "auto".into(),
            nunchuk_stick: "left".into(),
            gyro_invert_x: false,
            gyro_invert_y: false,
            motion: false,
            motion_target: "both".into(),
            motion_sensitivity: 1.0,
            motion_invert_x: false,
            motion_invert_y: false,
        }
    }
}

impl Controls {
    pub fn is_default(&self) -> bool { *self == Controls::default() }

    /// What a Wii input answers to: its explicit binding, or the runtime's default.
    pub fn effective(&self, wii: &str) -> Vec<String> {
        let value = self.bindings.get(wii).map(String::as_str)
            .unwrap_or_else(|| DEFAULT_BINDINGS.iter().find(|(k, _)| *k == wii).map(|(_, v)| *v).unwrap_or(""));
        value.split(',').filter_map(canonical_binding).collect()
    }

    /// Keeps only known names and in-range values, so a hand-edited or stale settings file cannot produce a broken
    /// mapping file. Binding values become a de-duplicated comma list of canonical controller inputs, or "" to unbind.
    pub fn sanitized(mut self) -> Self {
        let source = std::mem::take(&mut self.bindings);
        for (wii, pad) in source {
            if !is_wii_input(&wii) { continue; }
            let pad = pad.trim();
            if pad.is_empty() { self.bindings.insert(wii, String::new()); continue; }
            let mut kept: Vec<String> = Vec::new();
            for part in pad.split(',') {
                if let Some(name) = canonical_binding(part) {
                    if !kept.contains(&name) { kept.push(name); }
                }
            }
            if !kept.is_empty() { self.bindings.insert(wii, kept.join(",")); }
        }
        if !POINTERS.contains(&self.pointer.as_str()) { self.pointer = "auto".into(); }
        if self.nunchuk_stick != "right" { self.nunchuk_stick = "left".into(); }
        if !MOTION_TARGETS.contains(&self.motion_target.as_str()) { self.motion_target = "both".into(); }
        self.motion_sensitivity = if self.motion_sensitivity.is_finite() { self.motion_sensitivity.clamp(0.1, 4.0) } else { 1.0 };
        self
    }

    /// The text of one `controller-<slot>.txt` / `input-map.txt`. Only the keys the runtime reads are written, one
    /// `key=value` per line.
    pub fn to_map_file(&self) -> String {
        let s = self.clone().sanitized();
        let mut out = String::from("# Written by Crash Launcher from the Controls page. Edit the launcher settings instead.\n");
        for (wii, _) in WII_INPUTS.iter().chain(SHAKE_INPUTS.iter()).chain(KEY_STICK_INPUTS.iter()) {
            if let Some(pad) = s.bindings.get(*wii) {
                out.push_str(&format!("{wii}={pad}\n"));
            }
        }
        if s.pointer != "auto" { out.push_str(&format!("pointer={}\n", s.pointer)); }
        out.push_str(&format!("nunchuk_stick={}\n", s.nunchuk_stick));
        out.push_str(&format!("gyro_invert_x={}\ngyro_invert_y={}\n", s.gyro_invert_x as u8, s.gyro_invert_y as u8));
        out.push_str(&format!("motion={}\nmotion_target={}\n", s.motion as u8, s.motion_target));
        out.push_str(&format!("motion_sensitivity={:.2}\nmotion_invert_x={}\nmotion_invert_y={}\n",
            s.motion_sensitivity, s.motion_invert_x as u8, s.motion_invert_y as u8));
        out
    }
}

/// The full Controls page state: one layout per controller slot plus the slot currently being edited.
///
/// Deserialization also accepts the older flat settings shape (a single `Controls`) and migrates it into slot 0, so an
/// existing `settings.json` keeps its mapping.
#[derive(Clone, Serialize, PartialEq, Debug)]
pub struct ControlSetup {
    pub active: String,
    pub pads: BTreeMap<String, Controls>,
}

impl Default for ControlSetup {
    fn default() -> Self {
        let mut pads = BTreeMap::new();
        pads.insert("0".to_string(), Controls::default());
        Self { active: "0".to_string(), pads }
    }
}

#[derive(Deserialize)]
#[serde(default)]
struct RawSetup {
    active: String,
    pads: BTreeMap<String, Controls>,
}

impl Default for RawSetup {
    fn default() -> Self { Self { active: String::new(), pads: BTreeMap::new() } }
}

impl<'de> Deserialize<'de> for ControlSetup {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        use serde::de::Error as _;
        let value = serde_json::Value::deserialize(deserializer)?;
        if value.is_null() { return Ok(ControlSetup::default()); }
        let setup = if value.get("pads").is_some() || value.get("active").is_some() {
            let raw = RawSetup::deserialize(value).map_err(D::Error::custom)?;
            ControlSetup { active: raw.active, pads: raw.pads }
        } else {
            // Legacy flat layout: a single Controls becomes slot 0.
            let layout = Controls::deserialize(value).map_err(D::Error::custom)?;
            let mut pads = BTreeMap::new();
            pads.insert("0".to_string(), layout);
            ControlSetup { active: "0".to_string(), pads }
        };
        Ok(setup.sanitized())
    }
}

impl ControlSetup {
    /// Drops unknown slots, sanitizes every layout, guarantees slot 0 and an in-range active slot.
    pub fn sanitized(mut self) -> Self {
        self.pads.retain(|slot, _| SLOTS.contains(&slot.as_str()));
        let pads = std::mem::take(&mut self.pads);
        self.pads = pads.into_iter().map(|(slot, layout)| (slot, layout.sanitized())).collect();
        if self.pads.is_empty() { self.pads.insert("0".to_string(), Controls::default()); }
        if !self.pads.contains_key(&self.active) { self.active = "0".to_string(); }
        self
    }

    /// The layout for a slot, or the defaults when the slot has none.
    pub fn layout(&self, slot: &str) -> Controls {
        self.pads.get(slot).cloned().unwrap_or_default()
    }

    /// Every `(file name, text)` the launcher writes, one per configured slot.
    pub fn map_files(&self) -> Vec<(String, String)> {
        self.clone().sanitized().pads.iter()
            .map(|(slot, layout)| (format!("controller-{slot}.txt"), layout.to_map_file()))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(text: &str) -> Vec<String> {
        text.lines().filter(|l| !l.starts_with('#') && !l.is_empty()).map(str::to_string).collect()
    }

    #[test]
    fn keyboard_and_mouse_bindings_survive_and_are_written() {
        let mut c = Controls::default();
        c.bindings.insert("a".into(), "south, key:space ,Mouse:left,Key:Left Shift,Key:,Mouse:Nose,Key:a=b".into());
        c.bindings.insert("stick_up".into(), "Key:W".into());
        let s = c.sanitized();
        assert_eq!(s.bindings.get("a").map(String::as_str), Some("South,Key:space,Mouse:Left,Key:Left Shift"));
        let file = s.to_map_file();
        assert!(file.contains("a=South,Key:space,Mouse:Left,Key:Left Shift\n") && file.contains("stick_up=Key:W\n"));
        assert_eq!(canonical_binding("MOUSE:x2").as_deref(), Some("Mouse:X2"));
    }

    #[test]
    fn defaults_write_only_the_explicit_settings() {
        let text = Controls::default().to_map_file();
        let l = lines(&text);
        assert!(!l.iter().any(|x| x.starts_with("a=") || x.starts_with("pointer=")), "{l:?}");
        assert!(l.contains(&"nunchuk_stick=left".to_string()));
        assert!(l.contains(&"motion=0".to_string()));
    }

    #[test]
    fn c_can_be_bound_to_any_controller_button() {
        let mut c = Controls::default();
        c.bindings.insert("c".into(), "North".into());
        c.bindings.insert("two".into(), String::new());
        let l = lines(&c.to_map_file());
        assert!(l.contains(&"c=North".to_string()));
        assert!(l.contains(&"two=".to_string()));
    }

    #[test]
    fn multiple_controller_inputs_are_kept_as_a_comma_list() {
        let mut c = Controls::default();
        c.bindings.insert("z".into(), "RightShoulder, LeftTrigger ,RightShoulder".into());
        let l = lines(&c.to_map_file());
        assert!(l.contains(&"z=RightShoulder,LeftTrigger".to_string()), "{l:?}");
    }

    #[test]
    fn unknown_names_and_bad_values_are_dropped() {
        let mut c = Controls::default();
        c.bindings.insert("c".into(), "Bogus".into());
        c.bindings.insert("notawiiinput".into(), "South".into());
        c.pointer = "laser".into();
        c.nunchuk_stick = "sideways".into();
        c.motion_target = "toaster".into();
        c.motion_sensitivity = f64::NAN;
        let s = c.sanitized();
        assert!(s.bindings.is_empty());
        assert_eq!((s.pointer.as_str(), s.nunchuk_stick.as_str(), s.motion_target.as_str()), ("auto", "left", "both"));
        assert_eq!(s.motion_sensitivity, 1.0);
    }

    #[test]
    fn every_offered_name_is_one_the_writer_accepts() {
        let mut c = Controls::default();
        for (wii, _) in WII_INPUTS.iter().chain(SHAKE_INPUTS.iter()) {
            for (pad, _) in PAD_INPUTS {
                c.bindings.insert(wii.to_string(), pad.to_string());
                assert_eq!(c.clone().sanitized().bindings.get(*wii).map(String::as_str), Some(*pad), "{wii} <- {pad}");
            }
        }
    }

    #[test]
    fn pointer_and_motion_are_written_when_set() {
        let mut c = Controls::default();
        c.pointer = "gyro".into();
        c.gyro_invert_y = true;
        c.motion = true;
        c.motion_target = "nunchuk".into();
        c.motion_sensitivity = 0.5;
        let l = lines(&c.to_map_file());
        for want in ["pointer=gyro", "gyro_invert_y=1", "motion=1", "motion_target=nunchuk", "motion_sensitivity=0.50"] {
            assert!(l.contains(&want.to_string()), "missing {want} in {l:?}");
        }
    }

    #[test]
    fn legacy_flat_layout_migrates_into_slot_zero() {
        let setup: ControlSetup = serde_json::from_str(r#"{"bindings":{"a":"North"},"pointer":"gyro"}"#).unwrap();
        assert_eq!(setup.active, "0");
        assert_eq!(setup.layout("0").bindings.get("a").map(String::as_str), Some("North"));
        assert_eq!(setup.layout("0").pointer, "gyro");
    }

    #[test]
    fn slots_are_filtered_and_active_is_kept_in_range() {
        let setup: ControlSetup = serde_json::from_str(
            r#"{"active":"9","pads":{"0":{"bindings":{"a":"South"}},"2":{"pointer":"none"},"7":{}}}"#).unwrap();
        assert_eq!(setup.pads.keys().map(String::as_str).collect::<Vec<_>>(), vec!["0", "2"]);
        assert_eq!(setup.active, "0");
    }

    #[test]
    fn map_files_cover_every_configured_slot() {
        let setup: ControlSetup = serde_json::from_str(
            r#"{"active":"1","pads":{"0":{"bindings":{"a":"South"}},"1":{"bindings":{"a":"North"}}}}"#).unwrap();
        let files = setup.map_files();
        let names: Vec<&str> = files.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(names, vec!["controller-0.txt", "controller-1.txt"]);
        assert!(files[1].1.contains("a=North"), "{}", files[1].1);
        assert!(files[0].1.contains("a=South"));
    }
}

//! Button prompts that show the player's own controller, or keyboard and mouse.
//!
//! The game draws its button prompts ("PRESS A + B TO START", "HOLD Z + C") as glyphs of its text font: one 32 x 32 texture per Wii
//! button, at two font sizes, inside `package\b4c85fe7.p3d` of default.rcf (the same on both releases). Each texture's image is a PNG
//! behind a length field. Before a launch the launcher writes, over the eight button glyphs of both sizes, the glyph of the button the
//! player has bound to that Wii button (player 1's layout from the Controls page), drawn by `tools/make_prompt_glyphs.py` in the game's own
//! style. A new image never needs more room than the old one; the slot keeps its size and the rest is zero.
//!
//! Every edit is reversible: the original bytes of each slot are kept in a `.prompts` file beside the archive until they are restored.

use crate::{controls::Controls, prompt_glyphs::GLYPHS, soundtrack::rcf_members, title_art};
use std::{fs, io::{Read, Seek, SeekFrom, Write}, path::{Path, PathBuf}};

const PACKAGE: &str = "package\\b4c85fe7.p3d";
/// The Wii buttons in the order of their font textures, and the first texture of each font size.
const BUTTONS: [&str; 8] = ["a", "two", "b", "z", "minus", "plus", "c", "one"];
const FONTS: [(&str, usize); 2] = [("Titans_v3_48_texture_", 4), ("Titans_v3_32_texture_", 2)];
const MAGIC: &[u8; 8] = b"WCPROMPT";

/// Automatic and the older explicit follow setting both track activity during play.
pub fn follows_device(setting: &str) -> bool { matches!(setting, "auto" | "follow") }

/// Which buttons the prompts show.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Family { Xbox, PlayStation, Keyboard }

impl Family {
    fn dir(self) -> &'static str { match self { Family::Xbox => "xbox", Family::PlayStation => "playstation", Family::Keyboard => "kbm" } }

    /// The family a controller's name suggests (the same rule as the Controls page uses for its key caps).
    pub fn of_controller(name: &str) -> Family {
        let n = name.to_ascii_lowercase();
        let ps = ["dualsense", "dualshock", "playstation", "ps3", "ps4", "ps5", "wireless controller"];
        if ps.iter().any(|k| n.contains(k)) { Family::PlayStation } else { Family::Xbox }
    }
}

/// The glyph file name for a keyboard key, as the glyph tool names it.
fn key_file(name: &str) -> String {
    let safe = match name {
        "Keypad /" => "Keypad_Divide", "Keypad *" => "Keypad_Multiply", "Keypad -" => "Keypad_Minus",
        "Keypad +" => "Keypad_Plus", "Keypad ." => "Keypad_Period", "Keypad =" => "Keypad_Equals", "\\" => "Backslash",
        "-" => "Minus", "=" => "Equals", "[" => "LeftBracket", "]" => "RightBracket", ";" => "Semicolon", "'" => "Quote",
        "," => "Comma", "." => "Period", "/" => "Slash", "`" => "Backquote", other => other,
    };
    format!("Key_{}", safe.replace(' ', "_"))
}

fn glyph(family: Family, name: &str) -> Option<&'static [u8]> {
    GLYPHS.iter().find(|(f, n, _)| *f == family.dir() && n.eq_ignore_ascii_case(name)).map(|(_, _, b)| *b)
}

/// The glyph for one Wii button: the first of its bindings that belongs to the family's device and has a drawing.
fn glyph_for(family: Family, bindings: &[String]) -> Option<&'static [u8]> {
    bindings.iter().find_map(|t| {
        if family == Family::Keyboard {
            if let Some(k) = t.strip_prefix("Key:") { glyph(family, &key_file(k)) }
            else if let Some(b) = t.strip_prefix("Mouse:") { glyph(family, &format!("Mouse_{b}")) }
            else { None }
        } else if t.contains(':') { None } else { glyph(family, t) }
    })
}

fn backup_path(rcf: &Path) -> PathBuf { let mut s = rcf.as_os_str().to_owned(); s.push(".prompts"); PathBuf::from(s) }

fn find(d: &[u8], needle: &[u8], from: usize) -> Option<usize> { d.get(from..)?.windows(needle.len()).position(|w| w == needle).map(|i| i + from) }

/// Where each glyph's PNG length field sits inside the package, and how many bytes the slot holds (length field + PNG).
fn slots(package: &[u8]) -> Result<Vec<(usize, &'static str, usize)>, String> {
    let mut out = Vec::new();
    for (prefix, first) in FONTS {
        for (i, button) in BUTTONS.iter().enumerate() {
            let name = format!("{prefix}{}\0", first + i);
            let at = find(package, name.as_bytes(), 0).ok_or("The game font has an unexpected layout, so its button prompts cannot be changed.")?;
            let png = find(package, b"\x89PNG", at).filter(|&p| p - at < 160).ok_or("A button prompt in the game font is not a PNG image.")?;
            let len = u32::from_le_bytes(package[png - 4..png].try_into().unwrap()) as usize;
            if png + len > package.len() { return Err("A button prompt in the game font is damaged.".into()); }
            out.push((png - 4, *button, 4 + len));
        }
    }
    Ok(out)
}

/// Restores the game's own prompts if they were replaced. Safe to call at any time.
pub fn revert(rcf: &Path) -> Result<(), String> {
    let backup = backup_path(rcf);
    let Ok(data) = fs::read(&backup) else { return Ok(()) };
    let bad = || "The record of the changed button prompts is damaged.".to_string();
    if data.get(..8) != Some(&MAGIC[..]) { return Err(bad()); }
    let mut f = fs::OpenOptions::new().write(true).open(rcf).map_err(|e| format!("Could not open the game's resource archive: {e}"))?;
    let mut at = 8;
    while at < data.len() {
        let rec = data.get(at..at + 12).ok_or_else(bad)?;
        let offset = u64::from_le_bytes(rec[..8].try_into().unwrap());
        let len = u32::from_le_bytes(rec[8..12].try_into().unwrap()) as usize;
        let bytes = data.get(at + 12..at + 12 + len).ok_or_else(bad)?;
        f.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        f.write_all(bytes).map_err(|e| e.to_string())?;
        at += 12 + len;
    }
    f.flush().map_err(|e| e.to_string())?;
    drop(f);
    fs::remove_file(&backup).map_err(|e| e.to_string())
}

/// The new contents of every slot whose Wii button has a glyph for `family`: (offset in the package, slot bytes).
fn plan(package: &[u8], family: Family, layout: &Controls) -> Result<Vec<(usize, Vec<u8>)>, String> {
    let mut out = Vec::new();
    for (at, button, room) in slots(package)? {
        let Some(png) = glyph_for(family, &layout.effective(button)) else { continue };
        let img = title_art::decode_png(png)?;
        let mut encoded = title_art::encode_indexed(&img, 64)?;
        if 4 + encoded.len() > room { encoded = title_art::encode_indexed(&img, 24)?; }
        if 4 + encoded.len() > room { continue; }   // keeps the game's glyph rather than overrun the slot
        let mut slot = (encoded.len() as u32).to_le_bytes().to_vec();
        slot.extend_from_slice(&encoded);
        slot.resize(room, 0);
        out.push((at, slot));
    }
    Ok(out)
}

/// GX RGBA8 stores each 4x4 tile as sixteen AR pairs followed by sixteen GB pairs.
fn tiled_rgba(px: &[u8]) -> Vec<u8> {
    let mut out = vec![0; 4096];
    for y in 0..32 { for x in 0..32 {
        let p = (y * 32 + x) * 4;
        let t = ((y / 4) * 8 + x / 4) * 64 + ((y % 4) * 4 + x % 4) * 2;
        out[t] = px[p + 3]; out[t + 1] = px[p];
        out[t + 32] = px[p + 1]; out[t + 33] = px[p + 2];
    }}
    out
}

/// Build a host-only runtime pack from the restored archive. Originals remain in guest RAM.
/// Each record contains one exact GX source and Xbox/PlayStation/keyboard RGBA pixels.
pub fn live_pack(rcf: &Path, layout: &Controls, destination: &Path) -> Result<(), String> {
    let member = rcf_members(rcf)?.into_iter().find(|m| m.name.eq_ignore_ascii_case(PACKAGE)).ok_or("The game font was not found.")?;
    let mut f = fs::File::open(rcf).map_err(|e| e.to_string())?;
    let mut package = vec![0; member.len as usize];
    f.seek(SeekFrom::Start(member.offset)).map_err(|e| e.to_string())?;
    f.read_exact(&mut package).map_err(|e| e.to_string())?;
    let mut data = b"WCPRMT01".to_vec();
    for (at, button, room) in slots(&package)? {
        let original = title_art::decode_png(&package[at + 4..at + room])?;
        if (original.w, original.h) != (32, 32) { return Err("Unexpected button prompt dimensions.".into()); }
        data.extend_from_slice(&tiled_rgba(&original.px));
        for family in [Family::Xbox, Family::PlayStation, Family::Keyboard] {
            let replacement = glyph_for(family, &layout.effective(button)).map(title_art::decode_png).transpose()?;
            data.extend_from_slice(replacement.as_ref().map_or(original.px.as_slice(), |image| image.px.as_slice()));
        }
    }
    let pending = destination.with_extension("pending");
    fs::write(&pending, data).map_err(|e| format!("Could not write runtime button prompts: {e}"))?;
    fs::rename(&pending, destination).map_err(|e| format!("Could not publish runtime button prompts: {e}"))
}

/// Shows `family`'s buttons in the game's prompts, following player 1's bindings. Undo with `revert`.
pub fn apply(rcf: &Path, family: Family, layout: &Controls) -> Result<usize, String> {
    revert(rcf)?;
    let member = rcf_members(rcf)?.into_iter().find(|m| m.name.eq_ignore_ascii_case(PACKAGE)).ok_or("The game font was not found in default.rcf.")?;
    let mut f = fs::OpenOptions::new().read(true).write(true).open(rcf).map_err(|e| format!("Could not open the game's resource archive: {e}"))?;
    let mut package = vec![0u8; member.len as usize];
    f.seek(SeekFrom::Start(member.offset)).map_err(|e| e.to_string())?;
    f.read_exact(&mut package).map_err(|e| e.to_string())?;
    let edits = plan(&package, family, layout)?;
    if edits.is_empty() { return Ok(0); }
    // The undo record is written before anything changes, so an interrupted edit can always be reverted.
    let mut record = MAGIC.to_vec();
    for (at, slot) in &edits {
        record.extend_from_slice(&(member.offset + *at as u64).to_le_bytes());
        record.extend_from_slice(&(slot.len() as u32).to_le_bytes());
        record.extend_from_slice(&package[*at..*at + slot.len()]);
    }
    fs::write(backup_path(rcf), record).map_err(|e| format!("Could not record the game's button prompts: {e}"))?;
    for (at, slot) in &edits {
        f.seek(SeekFrom::Start(member.offset + *at as u64)).map_err(|e| e.to_string())?;
        f.write_all(slot).map_err(|e| e.to_string())?;
    }
    f.flush().map_err(|e| e.to_string())?;
    Ok(edits.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn automatic_tracks_devices_but_explicit_families_stay_fixed() {
        for setting in ["auto", "follow"] { assert!(follows_device(setting)); }
        for setting in ["wii", "xbox", "playstation", "keyboard"] { assert!(!follows_device(setting)); }
    }

    #[test]
    fn gx_tiles_keep_all_rgba_channels() {
        let px: Vec<u8> = (0..4096).map(|i| ((i * 13 + i / 7) % 256) as u8).collect();
        let tiled = tiled_rgba(&px);
        for y in 0..32 { for x in 0..32 {
            let at = ((y / 4) * 8 + x / 4) * 64 + ((y % 4) * 4 + x % 4) * 2;
            assert_eq!([tiled[at + 1], tiled[at + 32], tiled[at + 33], tiled[at]], px[(y * 32 + x) * 4..(y * 32 + x + 1) * 4]);
        }}
    }

    #[test]
    fn scratch_live_pack_uses_originals_and_player_bindings() {
        let Some(game) = std::env::var_os("CRASH_TEST_SCRATCH_GAME") else { return };
        let rcf = Path::new(&game).join("files/default.rcf");
        revert(&rcf).unwrap();
        let dest = Path::new(&game).join("button-prompts.bin");
        let c = layout(&[("a", "North,Key:J")]);
        live_pack(&rcf, &c, &dest).unwrap();
        let data = fs::read(&dest).unwrap();
        assert_eq!(data.len(), 8 + 16 * 4096 * 4);
        assert_eq!(&data[..8], b"WCPRMT01");
        let expected = title_art::decode_png(glyph(Family::Keyboard, "Key_J").unwrap()).unwrap();
        assert_eq!(&data[8 + 4096 * 3..8 + 4096 * 4], &expected.px);
    }

    fn layout(pairs: &[(&str, &str)]) -> Controls {
        let mut c = Controls::default();
        for (k, v) in pairs { c.bindings.insert(k.to_string(), v.to_string()); }
        c
    }

    #[test]
    fn every_glyph_decodes_and_fits_a_font_slot() {
        // The game's glyph PNGs are 1829 bytes; every replacement must fit in that.
        for (family, name, png) in GLYPHS {
            let img = title_art::decode_png(png).unwrap_or_else(|e| panic!("{family}/{name}: {e}"));
            assert_eq!((img.w, img.h), (32, 32), "{family}/{name}");
            let small = title_art::encode_indexed(&img, 64).unwrap();
            assert!(small.len() + 4 <= 1833, "{family}/{name} is {} bytes", small.len());
        }
    }

    #[test]
    fn every_bindable_controller_and_extended_keyboard_input_has_art() {
        for family in [Family::Xbox,Family::PlayStation] {
            for (input,_) in crate::controls::PAD_INPUTS {
                assert!(glyph_for(family,&[input.to_string()]).is_some(),"{family:?}/{input}");
            }
        }
        for key in ["\\","Keypad /","Keypad *","Keypad -","Keypad +","Keypad .","Keypad =","Keypad Enter","Left GUI","Right GUI","Application","PrintScreen","ScrollLock","Pause","Numlock"] {
            assert!(glyph_for(Family::Keyboard,&[format!("Key:{key}")]).is_some(),"{key}");
        }
        for n in 1..=24 {assert!(glyph_for(Family::Keyboard,&[format!("Key:F{n}")]).is_some());}
        for button in crate::controls::MOUSE_BUTTONS {assert!(glyph_for(Family::Keyboard,&[format!("Mouse:{button}")]).is_some());}
    }


    #[test]
    fn glyphs_follow_the_bindings_of_the_chosen_device() {
        let c = layout(&[("a", "North,Key:J"), ("b", "Mouse:X1"), ("z", "Paddle1")]);
        assert_eq!(glyph_for(Family::Xbox, &c.effective("a")), glyph(Family::Xbox, "North"));
        assert_eq!(glyph_for(Family::Keyboard, &c.effective("a")), glyph(Family::Keyboard, "Key_J"));
        assert_eq!(glyph_for(Family::Keyboard, &c.effective("b")), glyph(Family::Keyboard, "Mouse_X1"));
        assert!(glyph_for(Family::Xbox, &c.effective("b")).is_none(), "no controller binding keeps the Wii glyph");
        assert_eq!(glyph_for(Family::PlayStation, &c.effective("z")), glyph(Family::PlayStation,"Paddle1"));
        // Unset buttons use the runtime's defaults: A is South / Space, Z is the right shoulder / Left Shift.
        let d = Controls::default();
        assert_eq!(glyph_for(Family::PlayStation, &d.effective("a")), glyph(Family::PlayStation, "South"));
        assert_eq!(glyph_for(Family::Keyboard, &d.effective("z")), glyph(Family::Keyboard, "Key_Left_Shift"));
        assert_eq!(Family::of_controller("DualSense Wireless Controller"), Family::PlayStation);
        assert_eq!(Family::of_controller("Xbox Series X Controller"), Family::Xbox);
    }

    /// Manual in-game check: CRASH_PROMPT_APPLY=<folder> applies one family and leaves it ("xbox", "playstation", "keyboard" or "revert"
    /// in CRASH_PROMPT_FAMILY).
    #[test]
    fn manual_apply_for_an_in_game_check() {
        let Some(game) = std::env::var_os("CRASH_PROMPT_APPLY") else { return };
        let rcf = Path::new(&game).join("files/default.rcf");
        match std::env::var("CRASH_PROMPT_FAMILY").as_deref() {
            Ok("xbox") => { apply(&rcf, Family::Xbox, &Controls::default()).unwrap(); }
            Ok("keyboard") => { apply(&rcf, Family::Keyboard, &Controls::default()).unwrap(); }
            Ok("playstation") => { apply(&rcf, Family::PlayStation, &Controls::default()).unwrap(); }
            _ => revert(&rcf).unwrap(),
        }
    }

    /// Destructive but reversible; needs a throwaway prepared game: CRASH_TEST_SCRATCH_GAME=<folder holding files/default.rcf>.
    #[test]
    fn applying_and_reverting_restores_the_archive_byte_for_byte() {
        let Some(game) = std::env::var_os("CRASH_TEST_SCRATCH_GAME") else { return };
        let rcf = Path::new(&game).join("files/default.rcf");
        let sum = |p: &Path| { let mut h = std::collections::hash_map::DefaultHasher::new(); std::hash::Hasher::write(&mut h, &fs::read(p).unwrap()); std::hash::Hasher::finish(&h) };
        let before = sum(&rcf);
        assert_eq!(apply(&rcf, Family::PlayStation, &Controls::default()).unwrap(), 16);
        assert_ne!(sum(&rcf), before);
        assert_eq!(apply(&rcf, Family::Keyboard, &Controls::default()).unwrap(), 16);
        revert(&rcf).unwrap();
        assert_eq!(sum(&rcf), before, "the archive is byte-identical after reverting");
    }
}

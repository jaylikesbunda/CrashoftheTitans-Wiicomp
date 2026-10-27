//! Start a new game at a chosen episode.
//!
//! The menu's state machine (`fighttrees\Frontend.bfig` in default.rcf) holds Lua text, and every way of starting a new game ends in
//! `self:StartLevelByName( [[L1_E1]] )`. Choosing an episode rewrites those calls; choosing the beginning restores them. The edit is
//! in place and the same length (`( [[L1_E1]] )` and `([[L3_E1A]] )` are both 13 bytes), so nothing else in the archive moves. The first
//! call in the file is a different path (it starts L1_E3) and is never touched. Both releases carry the same script.

use crate::soundtrack::rcf_members;
use serde::Serialize;
use std::{fs, io::{Read, Seek, SeekFrom, Write}, path::Path};

/// The episode a new game starts at when nothing is chosen.
pub const DEFAULT: &str = "L1_E1";
const SCRIPT: &str = "fighttrees\\Frontend.bfig";
const CALL: &[u8] = b"StartLevelByName";
/// `( [[L1_E1]] )`: everything from the opening to the closing parenthesis.
const SPAN: usize = 13;

/// The levels the game knows, in game order (its own table of level names). The game numbers its episodes through this table, one
/// after the other across worlds, so the position here is the number it shows (L2_E1 is Episode 5).
pub const CHAPTERS: &[&str] = &[
    "L1_E1", "L1_E2", "L1_E3", "L1_E4", "L2_E1", "L2_E2", "L2_E4", "L3_E1", "L3_E1A", "L3_E1B",
    "L3_E2", "L3_E3", "L3_E4", "L4_E1", "L4_E2", "L4_E3", "L4_E4", "L5_E1", "L5_E2", "L5_E3",
];

/// Levels that ship on some discs but are not reachable from the game's menus (test maps). Their names are longer than the menu
/// script's fixed-width slots, so they can only be started by the straight-in route (direct.rs).
pub const DEV_LEVELS: &[&str] = &["CombatArena01", "SoundTest", "AI_pathfind"];

#[derive(Serialize)]
pub struct ChapterInfo { pub id: &'static str, pub number: usize, pub world: u32, pub dev: bool }

/// The episodes, then the test levels the disc's archive actually holds (`rcf` is its default.rcf).
pub fn list(rcf: &Path) -> Vec<ChapterInfo> {
    let mut out: Vec<ChapterInfo> = CHAPTERS.iter().enumerate().map(|(i, &id)| ChapterInfo { id, number: i + 1, world: id[1..2].parse().unwrap_or(0), dev: false }).collect();
    let present = dev_levels_present(rcf);
    out.extend(DEV_LEVELS.iter().filter(|id| present.contains(*id)).map(|&id| ChapterInfo { id, number: 0, world: 0, dev: true }));
    out
}
pub fn is_chapter(id: &str) -> bool { CHAPTERS.contains(&id) }
pub fn is_dev_level(id: &str) -> bool { DEV_LEVELS.contains(&id) }

/// Which test levels have their files in the archive (the NTSC disc has none).
pub fn dev_levels_present(rcf: &Path) -> Vec<&'static str> {
    let Ok(members) = rcf_members(rcf) else { return Vec::new() };
    DEV_LEVELS.iter().copied().filter(|id| {
        let sep = char::from(92u8); // the archive separates folders with a backslash
        let prefix = format!("levels{sep}{id}{sep}").to_ascii_lowercase();
        members.iter().any(|m| m.name.to_ascii_lowercase().starts_with(&prefix))
    }).collect()
}

/// The 13 bytes for a call that starts `chapter`.
pub(crate) fn render(chapter: &str) -> Result<Vec<u8>, String> {
    let text = match chapter.len() {
        5 => format!("( [[{chapter}]] )"),
        6 => format!("([[{chapter}]] )"),
        _ => return Err(format!("Unsupported episode name {chapter}.")),
    };
    debug_assert_eq!(text.len(), SPAN);
    Ok(text.into_bytes())
}

/// Offsets of every `StartLevelByName( [[…]] )` call and the level it names, in file order.
fn calls(data: &[u8]) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    // Calls before the title screen's state belong to the straight-in edit (direct.rs), not to the menu.
    let mut title = vec![0x83, 0xa9, 0x36, 0x33, 0, 0, 0, 11];
    title.extend_from_slice(b"TitleScreen");
    let mut at = data.windows(title.len()).position(|w| w == &title[..]).unwrap_or(0);
    while let Some(i) = data[at..].windows(CALL.len()).position(|w| w == CALL) {
        let start = at + i + CALL.len();
        at = start;
        let Some(span) = data.get(start..start + SPAN) else { break };
        let name = if span.starts_with(b"( [[") && span.ends_with(b"]] )") { &span[4..span.len() - 4] }
            else if span.starts_with(b"([[") && span.ends_with(b"]] )") { &span[3..span.len() - 4] }
            else { continue };
        if let Ok(name) = std::str::from_utf8(name) {
            if is_chapter(name) { out.push((start, name.to_string())); }
        }
    }
    out
}

/// Rewrites the new-game calls in `data` to start `chapter`; returns how many changed. The first call is left alone.
fn rewrite(data: &mut [u8], chapter: &str) -> Result<usize, String> {
    let sites = calls(data);
    if sites.len() < 2 || sites[0].1 != "L1_E3" {
        return Err("The game's menu script has an unexpected layout, so the starting episode cannot be changed.".into());
    }
    let new = render(chapter)?;
    let mut changed = 0;
    for (start, _) in &sites[1..] {
        if data[*start..*start + SPAN] != new[..] { data[*start..*start + SPAN].copy_from_slice(&new); changed += 1; }
    }
    Ok(changed)
}

/// The episode new games currently start at in the archive, or None if its menu script is not recognised.
pub fn current(rcf: &Path) -> Option<String> {
    let (_, data) = read_script(rcf).ok()?;
    let sites = calls(&data);
    (sites.len() >= 2 && sites[0].1 == "L1_E3").then(|| sites[1].1.clone())
}

fn read_script(rcf: &Path) -> Result<(u64, Vec<u8>), String> {
    let member = rcf_members(rcf)?.into_iter().find(|m| m.name.eq_ignore_ascii_case(SCRIPT))
        .ok_or("The game's menu script was not found in default.rcf.")?;
    if member.len > 8 << 20 { return Err("The game's menu script has an unexpected size.".into()); }
    let mut f = fs::File::open(rcf).map_err(|e| e.to_string())?;
    f.seek(SeekFrom::Start(member.offset)).map_err(|e| e.to_string())?;
    let mut data = vec![0u8; member.len as usize];
    f.read_exact(&mut data).map_err(|e| e.to_string())?;
    Ok((member.offset, data))
}

/// Makes new games start at `chapter` (use DEFAULT for the beginning) by editing the prepared default.rcf in place.
pub fn apply(rcf: &Path, chapter: &str) -> Result<usize, String> {
    if !is_chapter(chapter) { return Err(format!("Unknown episode {chapter}.")); }
    let (offset, mut data) = read_script(rcf)?;
    let before = data.clone();
    let changed = rewrite(&mut data, chapter)?;
    if changed == 0 { return Ok(0); }
    let mut f = fs::OpenOptions::new().write(true).open(rcf).map_err(|e| format!("Could not open the game's resource archive: {e}"))?;
    // Only the bytes that differ are written.
    let mut i = 0;
    while i < data.len() {
        if data[i] == before[i] { i += 1; continue; }
        let start = i;
        while i < data.len() && data[i] != before[i] { i += 1; }
        f.seek(SeekFrom::Start(offset + start as u64)).map_err(|e| e.to_string())?;
        f.write_all(&data[start..i]).map_err(|e| format!("Could not write the starting episode: {e}"))?;
    }
    f.flush().map_err(|e| e.to_string())?;
    Ok(changed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn script() -> Vec<u8> {
        let mut s = b"head\nXself:StartLevelByName( [[L1_E3]] )\r\nmid\n".to_vec();
        for _ in 0..16 { s.extend_from_slice(b"\nself:StartLevelByName( [[L1_E1]] )\r\nExitX\n"); }
        s
    }

    #[test]
    fn every_call_has_the_same_width() {
        for &id in CHAPTERS { assert_eq!(render(id).unwrap().len(), SPAN, "{id}"); }
        assert!(render("L1_E").is_err() && render("L1_E1ABC").is_err());
    }

    #[test]
    fn rewrites_the_new_game_calls_and_leaves_the_first() {
        let mut s = script();
        let len = s.len();
        assert_eq!(rewrite(&mut s, "L3_E1A").unwrap(), 16);
        assert_eq!(s.len(), len);
        let sites = calls(&s);
        assert_eq!(sites[0].1, "L1_E3");
        assert!(sites[1..].iter().all(|(_, n)| n == "L3_E1A"));
        assert_eq!(sites.len(), 17);
        // Switching again and restoring the default gives the original bytes back.
        assert_eq!(rewrite(&mut s, "L5_E2").unwrap(), 16);
        assert_eq!(rewrite(&mut s, DEFAULT).unwrap(), 16);
        assert_eq!(s, script());
        assert_eq!(rewrite(&mut s, DEFAULT).unwrap(), 0);
    }

    #[test]
    fn unexpected_scripts_are_refused() {
        assert!(rewrite(&mut b"nothing here".to_vec(), "L2_E1").is_err());
        let mut no_debug_call = b"self:StartLevelByName( [[L1_E1]] )\r\nself:StartLevelByName( [[L1_E1]] )".to_vec();
        assert!(rewrite(&mut no_debug_call, "L2_E1").is_err());
    }

    /// A one-member archive in default.rcf's layout holding `data` as the menu script.
    fn tiny_rcf(path: &Path, data: &[u8]) {
        let name = SCRIPT.as_bytes();
        let mut names = vec![0u8; 8];
        let mut meta = [0u8; 16];
        meta[12..16].copy_from_slice(&((name.len() + 1) as u32).to_le_bytes());
        names.extend_from_slice(&meta);
        names.extend_from_slice(name);
        names.extend_from_slice(&[0u8; 4]);
        let (index_off, names_off) = (60u32, 72u32);
        let data_off = names_off + names.len() as u32;
        let mut f = vec![0u8; 60];
        f[..24].copy_from_slice(b"ATG CORE CEMENT LIBRARY\0");
        for (at, v) in [(36, index_off), (40, 12), (44, names_off), (48, names.len() as u32), (56, 1)] { f[at..at + 4].copy_from_slice(&v.to_be_bytes()); }
        f.extend_from_slice(&0u32.to_be_bytes());
        f.extend_from_slice(&data_off.to_be_bytes());
        f.extend_from_slice(&(data.len() as u32).to_be_bytes());
        f.extend_from_slice(&names);
        f.extend_from_slice(data);
        fs::write(path, f).unwrap();
    }

    #[test]
    fn apply_edits_only_the_new_game_calls_and_restores_them() {
        let path = std::env::temp_dir().join(format!("crash-chapters-{}.rcf", std::process::id()));
        tiny_rcf(&path, &script());
        let original = fs::read(&path).unwrap();
        assert_eq!(current(&path).as_deref(), Some("L1_E1"));
        assert_eq!(apply(&path, "L3_E1A").unwrap(), 16);
        assert_eq!(current(&path).as_deref(), Some("L3_E1A"));
        let edited = fs::read(&path).unwrap();
        assert_eq!(edited.len(), original.len());
        assert_eq!(edited.iter().zip(&original).filter(|(a, b)| a != b).count() > 0, true);
        assert_eq!(apply(&path, "L3_E1A").unwrap(), 0, "applying the same episode again changes nothing");
        assert_eq!(apply(&path, "L5_E3").unwrap(), 16);
        assert_eq!(apply(&path, DEFAULT).unwrap(), 16);
        assert_eq!(fs::read(&path).unwrap(), original, "the beginning restores the original bytes exactly");
        assert!(apply(&path, "L9_E9").is_err());
        let _ = fs::remove_file(&path);
    }

    /// Needs a prepared game: CRASH_TEST_GAME=<folder holding files/default.rcf>. Works on a temporary copy of the script only.
    #[test]
    fn real_script_round_trips() {
        let Some(game) = std::env::var_os("CRASH_TEST_GAME") else { return };
        let rcf = Path::new(&game).join("files/default.rcf");
        let (_, original) = read_script(&rcf).unwrap();
        let mut copy = original.clone();
        assert_eq!(calls(&copy).len(), 17);
        assert_eq!(rewrite(&mut copy, "L4_E2").unwrap(), 16);
        assert_eq!(rewrite(&mut copy, DEFAULT).unwrap(), 16);
        assert_eq!(copy, original);
        assert!(current(&rcf).is_some());
    }
}

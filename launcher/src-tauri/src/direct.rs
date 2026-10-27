//! Start straight in an episode, with no boot movies and no menus.
//!
//! The menu state machine (`fighttrees\Frontend.bfig` in default.rcf) leaves the boot sequence through the transition
//! `ExitTitleScreen` out of `LoadPersistentPostBootUp`. The title screen has a built-in debug transition, `ExitToLevel`, that starts a level
//! and jumps to level loading; here that transition's body (with the chosen episode) replaces the boot one. The three ways into the licence
//! screens are pointed at the persistent load instead, and the boot movies are made one frame long (their Bink header frame counts).
//!
//! Everything is reversible: the script is rewritten in place (it only grows into the padding before the next member, and its length in the
//! archive index is updated), and the original script bytes and movie frame counts are kept in a `.direct` file beside the archive until
//! the edit is undone.

use crate::{chapters, soundtrack::rcf_members};
use std::{fs, io::{Read, Seek, SeekFrom, Write}, path::{Path, PathBuf}};

const SCRIPT: &str = "fighttrees\\Frontend.bfig";
const TRANSITION: [u8; 4] = [0xbc, 0x27, 0x35, 0x9d];
const STATE: [u8; 4] = [0x83, 0xa9, 0x36, 0x33];
const ACTIONS: [u8; 4] = [0x0a, 0x87, 0x0f, 0x46];
const BOOT_MOVIES: [&str; 4] = ["dolby_", "radical_", "sierra_", "shadow_"];
const MAGIC: &[u8; 8] = b"WCDIRECT";

fn be32(d: &[u8], at: usize) -> Option<u32> { d.get(at..at + 4).map(|b| u32::from_be_bytes(b.try_into().unwrap())) }
fn put32(d: &mut [u8], at: usize, v: u32) { d[at..at + 4].copy_from_slice(&v.to_be_bytes()); }

struct Record { start: usize, name: Vec<u8>, fields: usize }

/// Start of the fields after a record's name (the name is padded to four bytes).
fn name_end(d: &[u8], at: usize) -> Option<(usize, Vec<u8>)> {
    let n = be32(d, at + 4)? as usize;
    let name = d.get(at + 8..at + 8 + n)?.to_vec();
    Some((at + 8 + n.div_ceil(4) * 4, name))
}

fn transitions(d: &[u8]) -> Vec<Record> {
    let mut out = Vec::new();
    let mut at = 0;
    while let Some(i) = d[at..].windows(4).position(|w| w == TRANSITION) {
        let start = at + i;
        at = start + 4;
        let Some((fields, name)) = name_end(d, start) else { continue };
        if !name.is_empty() && name.iter().all(|c| c.is_ascii_alphanumeric() || *c == b'_') { out.push(Record { start, name, fields }); }
    }
    out
}

fn find(d: &[u8], needle: &[u8], from: usize) -> Option<usize> { d.get(from..)?.windows(needle.len()).position(|w| w == needle).map(|i| i + from) }

/// End of a transition record: its action block, which is 12 bytes of header plus its size (or a fixed 20 bytes when empty).
fn transition_end(d: &[u8], r: &Record) -> Option<usize> {
    let block = find(d, &ACTIONS, r.fields)?;
    let size = be32(d, block + 4)? as usize;
    Some(if size == 0 { block + 20 } else { block + 12 + size })
}

/// The script with the boot sequence replaced; None if its layout is not the one this edit was made for.
fn build(script: &[u8], chapter: &str) -> Option<Vec<u8>> {
    let recs = transitions(script);
    let mut boot_state = STATE.to_vec();
    boot_state.extend_from_slice(&24u32.to_be_bytes());
    boot_state.extend_from_slice(b"LoadPersistentPostBootUp");
    let after = find(script, &boot_state, 0)?;
    // State numbers differ between releases, so they are read from the script: the persistent load is the `Demo` branch that follows
    // LoadPersistentPostBootUp, and the licence screens are the state whose first transition comes right after `LicenseScreen`.
    let mut demo_branch = vec![0xe8, 0xcb, 0x72, 0x1b, 0, 0, 0, 4];
    demo_branch.extend_from_slice(b"Demo");
    let demo = find(script, &demo_branch, after)?;
    let persistent_load = be32(script, demo + 12)?;
    let mut license_screen = STATE.to_vec();
    license_screen.extend_from_slice(&13u32.to_be_bytes());
    license_screen.extend_from_slice(b"LicenseScreen");
    let license_at = find(script, &license_screen, 0)?;
    let license = recs.iter().find(|r| r.start > license_at).and_then(|r| be32(script, r.fields))?;
    let boot_exit = recs.iter().find(|r| r.name == b"ExitTitleScreen" && r.start > after && be32(script, r.fields) == Some(persistent_load))?;
    let debug = recs.iter().find(|r| r.name == b"ExitToLevel" && script.get(r.start..r.start + 0x100).is_some_and(|w| find(w, b"[[L1_E3]]", 0).is_some()))?;
    let (boot_end, debug_end) = (transition_end(script, boot_exit)?, transition_end(script, debug)?);
    if script.get(boot_end..boot_end + 4)? != STATE || script.get(debug_end..debug_end + 4)? != TRANSITION { return None; }
    let mut body = script[debug.fields + 4..debug_end].to_vec();
    let old = b"( [[L1_E3]] )";
    let at = find(&body, old, 0)?;
    if find(&body, old, at + 1).is_some() { return None; }
    // Episodes keep the call's width; any other level (a test map) is written out in full, and the two lengths that cover the
    // call's text (its own and its action block's) grow with it.
    let call = chapters::render(chapter).unwrap_or_else(|_| format!("( [[{chapter}]] )").into_bytes());
    if !chapter.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') { return None; }
    let grow = call.len() as i64 - old.len() as i64;
    body.splice(at..at + old.len(), call);
    if grow != 0 {
        let text = find(&body, b"self:StartLevelByName", 0)?;
        let block = find(&body, &ACTIONS, 0)?;
        for field in [text.checked_sub(4)?, block + 4] {
            let len = be32(&body, field)? as i64 + grow;
            put32(&mut body, field, u32::try_from(len).ok()?);
        }
    }
    let mut out = script[..boot_exit.start].to_vec();
    out.extend_from_slice(&script[boot_exit.start..boot_exit.fields + 4]);
    out.extend_from_slice(&body);
    out.extend_from_slice(&script[boot_end..]);
    let mut retargeted = 0;
    for r in transitions(&out) {
        if be32(&out, r.fields + 24) == Some(license) { put32(&mut out, r.fields + 24, persistent_load); retargeted += 1; }
    }
    (retargeted > 0).then_some(out)
}

fn backup_path(rcf: &Path) -> PathBuf { let mut s = rcf.as_os_str().to_owned(); s.push(".direct"); PathBuf::from(s) }

struct Movie { header: u64, frames: u32, again: u32 }

fn read_u32le(f: &mut fs::File, at: u64) -> Result<u32, String> {
    f.seek(SeekFrom::Start(at)).map_err(|e| e.to_string())?;
    let mut b = [0u8; 4];
    f.read_exact(&mut b).map_err(|e| e.to_string())?;
    Ok(u32::from_le_bytes(b))
}

fn write_u32le(f: &mut fs::File, at: u64, v: u32) -> Result<(), String> {
    f.seek(SeekFrom::Start(at)).map_err(|e| e.to_string())?;
    f.write_all(&v.to_le_bytes()).map_err(|e| e.to_string())
}

/// Offset of the archive index entry that points at `offset` (its length field is 8 bytes in).
fn index_slot(f: &mut fs::File, offset: u64) -> Result<u64, String> {
    let mut h = [0u8; 60];
    f.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    f.read_exact(&mut h).map_err(|e| e.to_string())?;
    let (index, size) = (be32(&h, 36).ok_or("short header")? as u64, be32(&h, 40).ok_or("short header")? as usize);
    let mut ib = vec![0u8; size];
    f.seek(SeekFrom::Start(index)).map_err(|e| e.to_string())?;
    f.read_exact(&mut ib).map_err(|e| e.to_string())?;
    ib.chunks_exact(12).position(|e| be32(e, 4) == Some(offset as u32)).map(|i| index + i as u64 * 12).ok_or_else(|| "The menu script is missing from the archive index.".into())
}

/// Whether the archive is currently edited (an undo record exists).
pub fn is_applied(rcf: &Path) -> bool { backup_path(rcf).is_file() }

/// Undoes the edit if it was made. Safe to call at any time.
pub fn revert(rcf: &Path) -> Result<(), String> {
    let backup = backup_path(rcf);
    let Ok(data) = fs::read(&backup) else { return Ok(()) };
    let bad = || "The record of the starting-episode edit is damaged.".to_string();
    if data.get(..8) != Some(&MAGIC[..]) { return Err(bad()); }
    let script_len = be32(&data, 8).ok_or_else(bad)? as usize;
    let script = data.get(12..12 + script_len).ok_or_else(bad)?;
    let mut at = 12 + script_len;
    let count = be32(&data, at).ok_or_else(bad)? as usize;
    at += 4;
    let member = rcf_members(rcf)?.into_iter().find(|m| m.name.eq_ignore_ascii_case(SCRIPT)).ok_or("The game's menu script was not found in default.rcf.")?;
    let mut f = fs::OpenOptions::new().read(true).write(true).open(rcf).map_err(|e| format!("Could not open the game's resource archive: {e}"))?;
    let slot = index_slot(&mut f, member.offset)?;
    f.seek(SeekFrom::Start(member.offset)).map_err(|e| e.to_string())?;
    let mut padded = script.to_vec();
    padded.resize((member.len as usize).max(script.len()), 0);
    f.write_all(&padded).map_err(|e| e.to_string())?;
    f.seek(SeekFrom::Start(slot + 8)).map_err(|e| e.to_string())?;
    f.write_all(&(script.len() as u32).to_be_bytes()).map_err(|e| e.to_string())?;
    for _ in 0..count {
        let rec = data.get(at..at + 16).ok_or_else(bad)?;
        let header = u64::from_be_bytes(rec[..8].try_into().unwrap());
        write_u32le(&mut f, header + 8, be32(rec, 8).ok_or_else(bad)?)?;
        write_u32le(&mut f, header + 16, be32(rec, 12).ok_or_else(bad)?)?;
        at += 16;
    }
    f.flush().map_err(|e| e.to_string())?;
    drop(f);
    fs::remove_file(&backup).map_err(|e| e.to_string())
}

/// Makes the game start straight in `chapter`. Undo with `revert`.
pub fn apply(rcf: &Path, chapter: &str) -> Result<(), String> {
    if !chapters::is_chapter(chapter) && !chapters::is_dev_level(chapter) { return Err(format!("Unknown episode {chapter}.")); }
    revert(rcf)?;
    let members = rcf_members(rcf)?;
    let member = members.iter().find(|m| m.name.eq_ignore_ascii_case(SCRIPT)).ok_or("The game's menu script was not found in default.rcf.")?;
    let next = members.iter().map(|m| m.offset).filter(|&o| o > member.offset).min().unwrap_or(u64::MAX);
    let mut f = fs::OpenOptions::new().read(true).write(true).open(rcf).map_err(|e| format!("Could not open the game's resource archive: {e}"))?;
    let mut script = vec![0u8; member.len as usize];
    f.seek(SeekFrom::Start(member.offset)).map_err(|e| e.to_string())?;
    f.read_exact(&mut script).map_err(|e| e.to_string())?;
    let new = build(&script, chapter).ok_or("The game's menu script has an unexpected layout, so it cannot start straight in an episode.")?;
    if member.offset + new.len() as u64 > next { return Err("The edited menu script does not fit in the archive.".into()); }
    let slot = index_slot(&mut f, member.offset)?;
    let mut movies = Vec::new();
    for m in &members {
        let base = m.name.rsplit('\\').next().unwrap_or("").to_ascii_lowercase();
        if !base.ends_with(".bik") || !BOOT_MOVIES.iter().any(|p| base.starts_with(p)) { continue; }
        let mut magic = [0u8; 3];
        f.seek(SeekFrom::Start(m.offset)).map_err(|e| e.to_string())?;
        f.read_exact(&mut magic).map_err(|e| e.to_string())?;
        if &magic != b"BIK" { continue; }
        movies.push(Movie { header: m.offset, frames: read_u32le(&mut f, m.offset + 8)?, again: read_u32le(&mut f, m.offset + 16)? });
    }
    // The record of how to undo comes first, so an interrupted edit can always be reverted.
    let mut record = MAGIC.to_vec();
    record.extend_from_slice(&(script.len() as u32).to_be_bytes());
    record.extend_from_slice(&script);
    record.extend_from_slice(&(movies.len() as u32).to_be_bytes());
    for m in &movies {
        record.extend_from_slice(&m.header.to_be_bytes());
        record.extend_from_slice(&m.frames.to_be_bytes());
        record.extend_from_slice(&m.again.to_be_bytes());
    }
    fs::write(backup_path(rcf), record).map_err(|e| format!("Could not record the original menu script: {e}"))?;
    let mut padded = new.clone();
    padded.resize(script.len().max(new.len()), 0);
    f.seek(SeekFrom::Start(member.offset)).map_err(|e| e.to_string())?;
    f.write_all(&padded).map_err(|e| e.to_string())?;
    f.seek(SeekFrom::Start(slot + 8)).map_err(|e| e.to_string())?;
    f.write_all(&(new.len() as u32).to_be_bytes()).map_err(|e| e.to_string())?;
    for m in &movies {
        write_u32le(&mut f, m.header + 8, 1)?;
        write_u32le(&mut f, m.header + 16, 1)?;
    }
    f.flush().map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn real_script() -> Option<Vec<u8>> {
        let game = std::env::var_os("CRASH_TEST_GAME")?;
        let rcf = Path::new(&game).join("files/default.rcf");
        let m = rcf_members(&rcf).unwrap().into_iter().find(|m| m.name.eq_ignore_ascii_case(SCRIPT)).unwrap();
        let mut f = fs::File::open(&rcf).unwrap();
        f.seek(SeekFrom::Start(m.offset)).unwrap();
        let mut d = vec![0u8; m.len as usize];
        f.read_exact(&mut d).unwrap();
        Some(d)
    }

    #[test]
    fn unrecognised_scripts_are_refused() {
        assert!(build(b"nothing here", "L2_E1").is_none());
    }

    /// Needs a prepared game (CRASH_TEST_GAME=<folder holding files/default.rcf>); works on the script bytes only.
    #[test]
    fn real_script_gets_the_level_start_and_keeps_its_structure() {
        let Some(script) = real_script() else { return };
        // A script that already had its new-game episode changed is built the same way.
        for chapter in ["L1_E1", "L2_E1", "L3_E1A", "L5_E3"] {
            let out = build(&script, chapter).expect("layout");
            assert!(out.len() > script.len() && out.len() < script.len() + 400);
            let sites = chapters_sites(&out);
            assert_eq!(sites.first().map(String::as_str), Some(chapter), "the boot exit starts the chosen episode");
            assert_eq!(sites.iter().filter(|c| c.as_str() == "L1_E3").count() + usize::from(chapter == "L1_E3"), 1 + usize::from(chapter == "L1_E3"));
            // Every transition record still parses and every licence entry now goes to the persistent load.
            assert!(transitions(&out).iter().all(|r| !matches!(be32(&out, r.fields + 24), Some(0x2c | 0x24))), "no transition enters the licence screens");
            assert_eq!(transitions(&out).len(), transitions(&script).len());
        }
        if let Some(path) = std::env::var_os("CRASH_TEST_OUT") { fs::write(path, build(&script, "L2_E1").unwrap()).unwrap(); }
    }

    /// Destructive but reversible; needs a throwaway prepared game: CRASH_TEST_SCRATCH_GAME=<folder holding files/default.rcf>.
    #[test]
    fn applying_and_reverting_restores_the_archive_byte_for_byte() {
        let Some(game) = std::env::var_os("CRASH_TEST_SCRATCH_GAME") else { return };
        let rcf = Path::new(&game).join("files/default.rcf");
        let sum = |p: &Path| { let mut h = std::collections::hash_map::DefaultHasher::new(); std::hash::Hasher::write(&mut h, &fs::read(p).unwrap()); std::hash::Hasher::finish(&h) };
        let before = sum(&rcf);
        assert!(!is_applied(&rcf));
        // The menu's new-game edit (a chosen episode) may already be in the script.
        chapters::apply(&rcf, "L4_E2").unwrap();
        apply(&rcf, "L3_E1A").unwrap();
        assert!(is_applied(&rcf));
        assert_ne!(sum(&rcf), before);
        let script = rcf_members(&rcf).unwrap().into_iter().find(|m| m.name.eq_ignore_ascii_case(SCRIPT)).unwrap();
        assert!(script.len > 0);
        // Applying again from the edited state starts from the original, and the menu edit still works on top of a revert.
        apply(&rcf, "L5_E3").unwrap();
        revert(&rcf).unwrap();
        assert!(!is_applied(&rcf));
        assert_eq!(chapters::current(&rcf).as_deref(), Some("L4_E2"));
        chapters::apply(&rcf, chapters::DEFAULT).unwrap();
        assert_eq!(sum(&rcf), before, "the archive is byte-identical after reverting");
        revert(&rcf).unwrap();
    }

    /// A test level has a longer name than an episode: the call grows and the two lengths covering its text grow with it.
    #[test]
    fn test_level_names_widen_the_call_and_its_lengths() {
        let Some(script) = real_script() else { return };
        let base = build(&script, "L2_E1").expect("layout");
        let out = build(&script, "CombatArena01").expect("layout");
        let grow = "CombatArena01".len() - "L2_E1".len();
        assert_eq!(out.len(), base.len() + grow);
        let call = find(&out, b"StartLevelByName( [[CombatArena01]] )", 0).expect("the call names the level");
        let (base_call, text) = (find(&base, b"StartLevelByName( [[L2_E1]] )", 0).unwrap(), call - "self:".len());
        assert_eq!(be32(&out, text - 4).unwrap(), be32(&base, base_call - "self:".len() - 4).unwrap() + grow as u32);
        let block = |d: &[u8], at: usize| be32(d, d[..at].windows(4).rposition(|w| w == ACTIONS).unwrap() + 4).unwrap();
        assert_eq!(block(&out, call), block(&base, base_call) + grow as u32);
        assert!(build(&script, "Bad Name").is_none() && build(&script, "A;B").is_none());
        if let Some(path) = std::env::var_os("CRASH_TEST_OUT_DEV") { fs::write(path, &out).unwrap(); }
    }

    fn chapters_sites(d: &[u8]) -> Vec<String> {
        let call = b"StartLevelByName";
        let mut out = Vec::new();
        let mut at = 0;
        while let Some(i) = find(d, call, at) {
            at = i + call.len();
            let span = &d[at..(at + 13).min(d.len())];
            let name = if span.starts_with(b"( [[") { &span[4..span.len().saturating_sub(4)] } else if span.starts_with(b"([[") { &span[3..span.len().saturating_sub(4)] } else { continue };
            if let Ok(n) = std::str::from_utf8(name) { if chapters::is_chapter(n) { out.push(n.to_string()); } }
        }
        out
    }
}

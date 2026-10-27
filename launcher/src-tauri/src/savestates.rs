//! Save states written by the runtime (F1 in game) and listed on the Save states page.
//!
//! The runtime writes three files per state into the folder named by `NWII_STATE_DIR` (`<profile>/savestates`):
//!   * `<id>.nwstate` — the snapshot itself (guest memory, CPU and device state),
//!   * `<id>.json`    — metadata: `format`, `saved_ms` (Unix milliseconds), `dol_sha256`, optional `note`,
//!   * `<id>.bmp`     — an optional small screenshot of the frame on screen when the state was taken.
//!
//! A state is started by launching the runtime with `NWII_LOAD_STATE=<path to .nwstate>`. It holds only guest-visible
//! state at a dispatcher boundary, so it stays valid across runtime builds made from the same disc and format version.

use serde::{Deserialize, Serialize};
use std::{fs, path::{Path, PathBuf}};

/// The snapshot layout this launcher knows how to start (runtime `native_savestate.h`, kFormat).
pub const FORMAT: u32 = 1;

#[derive(Deserialize, Default)]
#[serde(default)]
struct Meta { format: u32, saved_ms: u64, dol_sha256: String, note: String }

#[derive(Serialize)]
pub struct StateInfo {
    pub id: String,
    pub saved_ms: u64,
    pub note: String,
    pub compatible: bool,
    /// `data:image/bmp;base64,…`, or empty when the runtime wrote no screenshot.
    pub thumbnail: String,
}

/// Ids are file stems the runtime chose; only plain names are accepted so an id can never point outside the folder.
pub fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

pub fn state_file(dir: &Path, id: &str) -> Option<PathBuf> {
    if !valid_id(id) { return None; }
    let p = dir.join(format!("{id}.nwstate"));
    p.is_file().then_some(p)
}

/// Newest first.
pub fn list(dir: &Path, dol_sha: &str) -> Vec<StateInfo> {
    let Ok(entries) = fs::read_dir(dir) else { return Vec::new() };
    let mut out: Vec<StateInfo> = entries.flatten().filter_map(|e| {
        let path = e.path();
        if path.extension().and_then(|x| x.to_str()) != Some("nwstate") { return None; }
        let id = path.file_stem()?.to_str()?.to_string();
        if !valid_id(&id) { return None; }
        let meta: Meta = fs::read(path.with_extension("json")).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
        let saved_ms = if meta.saved_ms > 0 { meta.saved_ms } else {
            e.metadata().ok().and_then(|m| m.modified().ok()).and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_millis() as u64).unwrap_or(0)
        };
        let compatible = meta.format == FORMAT && (dol_sha.is_empty() || meta.dol_sha256.is_empty() || meta.dol_sha256 == dol_sha);
        let thumbnail = fs::read(path.with_extension("bmp")).ok().filter(|b| b.len() < (2 << 20) && b.starts_with(b"BM"))
            .map(|b| format!("data:image/bmp;base64,{}", base64(&b))).unwrap_or_default();
        Some(StateInfo { id, saved_ms, note: meta.note, compatible, thumbnail })
    }).collect();
    out.sort_by(|a, b| b.saved_ms.cmp(&a.saved_ms));
    out
}

pub fn delete(dir: &Path, id: &str) -> Result<(), String> {
    if !valid_id(id) { return Err("Unknown save state.".into()); }
    for ext in ["nwstate", "json", "bmp"] {
        let p = dir.join(format!("{id}.{ext}"));
        match fs::remove_file(&p) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("Could not delete {}: {e}", p.display())),
        }
    }
    Ok(())
}

fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut s = String::with_capacity((data.len() + 2) / 3 * 4);
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        for i in 0..4 {
            s.push(if i <= c.len() { T[(n >> (18 - 6 * i) & 63) as usize] as char } else { '=' });
        }
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_rfc4648_vectors() {
        for (i, o) in [("", ""), ("f", "Zg=="), ("fo", "Zm8="), ("foo", "Zm9v"), ("foob", "Zm9vYg=="), ("fooba", "Zm9vYmE="), ("foobar", "Zm9vYmFy")] {
            assert_eq!(base64(i.as_bytes()), o);
        }
    }

    #[test]
    fn ids_cannot_escape_the_folder() {
        for bad in ["", "..", "../x", "a/b", "a\\b", "c:", "x.nwstate"] { assert!(!valid_id(bad), "{bad}"); }
        assert!(valid_id("20261018-153012-123"));
    }

    #[test]
    fn listing_reads_metadata_and_sorts_newest_first() {
        let dir = std::env::temp_dir().join(format!("crash-states-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("old.nwstate"), b"x").unwrap();
        fs::write(dir.join("old.json"), br#"{"format":1,"saved_ms":1000,"dol_sha256":"aa"}"#).unwrap();
        fs::write(dir.join("new.nwstate"), b"x").unwrap();
        fs::write(dir.join("new.json"), br#"{"format":1,"saved_ms":2000,"dol_sha256":"bb","note":"Wumpa Island"}"#).unwrap();
        fs::write(dir.join("new.bmp"), b"BMxx").unwrap();
        let l = list(&dir, "aa");
        assert_eq!(l.iter().map(|s| s.id.as_str()).collect::<Vec<_>>(), vec!["new", "old"]);
        assert!(!l[0].compatible && l[1].compatible);
        assert!(l[0].thumbnail.starts_with("data:image/bmp;base64,"));
        assert_eq!(l[0].note, "Wumpa Island");
        delete(&dir, "new").unwrap();
        assert!(!dir.join("new.bmp").exists());
        assert!(state_file(&dir, "old").is_some() && state_file(&dir, "new").is_none());
        fs::remove_dir_all(&dir).unwrap();
    }
}

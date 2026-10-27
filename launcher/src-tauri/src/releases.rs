//! Disc releases the launcher can prepare and build. The builder pack holds each release's data under `data/` (the PAL
//! reference) or `data/regions/<game id>/`; the values here must match those files (checked by the tests below).

pub struct Release {
    pub game_id: &'static str,
    pub label: &'static str,
    /// SHA-256 of the release's main.dol: the native build is only valid for this exact executable.
    pub dol_sha256: &'static str,
    /// Function index inside the builder (`native-builder/`).
    pub index: &'static str,
    /// Runtime HLE settings: the SDK's __OSDispatchInterrupt and the scheduler-disable counter's small-data offset.
    pub ext_interrupt_dispatch: u32,
    pub sched_lock_offset: u32,
    /// Size of `package\cdd70a8c.p3d` (the frontend package holding the title logo) in default.rcf.
    pub title_package_len: u32,
}

pub const RELEASES: &[Release] = &[
    Release {
        game_id: "RQJP7D",
        label: "PAL",
        dol_sha256: "a6119a43aa2bf2ce88c54142f106c04b65c9d3abd8d9258729d0adda7b0a676a",
        index: "data/function-index.tsv",
        ext_interrupt_dispatch: 0x803B_9574,
        sched_lock_offset: 0x2950,
        title_package_len: 1_632_026,
    },
    Release {
        game_id: "RQJE7D",
        label: "USA",
        dol_sha256: "7e562fcce7113b7fe55dff35c1872d8289ce7db3cf4e117067001e6e06875e8d",
        index: "data/regions/RQJE7D/function-index.tsv",
        ext_interrupt_dispatch: 0x803B_7EE4,
        sched_lock_offset: 0x2A28,
        title_package_len: 1_603_285,
    },
];

/// The release a fresh install (or one prepared before region support) uses.
pub const DEFAULT: &str = "RQJP7D";

pub fn by_game_id(id: &str) -> Option<&'static Release> { RELEASES.iter().find(|r| r.game_id == id) }

/// "PAL (RQJP7D) or USA (RQJE7D)" for messages.
pub fn supported_list() -> String {
    RELEASES.iter().map(|r| format!("{} ({})", r.label, r.game_id)).collect::<Vec<_>>().join(" or ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, path::Path};

    fn port() -> &'static Path { Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../..")) }

    fn toml_hex(text: &str, key: &str) -> u32 {
        let line = text.lines().find(|l| l.trim_start().starts_with(key)).unwrap_or_else(|| panic!("{key} missing"));
        let value = line.split('=').nth(1).unwrap().trim().trim_matches('"');
        u32::from_str_radix(value.trim_start_matches("0x").trim_start_matches("0X"), 16).unwrap()
    }

    #[test]
    fn table_matches_builder_data() {
        let pal = by_game_id("RQJP7D").unwrap();
        assert_eq!(fs::read_to_string(port().join("data/main.dol.sha256")).unwrap().trim(), pal.dol_sha256);
        let toml = fs::read_to_string(port().join("runtime.toml")).unwrap();
        assert_eq!(toml_hex(&toml, "ext_interrupt_dispatch"), pal.ext_interrupt_dispatch);
        assert_eq!(toml_hex(&toml, "sched_lock_offset"), pal.sched_lock_offset);
        for r in RELEASES.iter().filter(|r| r.game_id != "RQJP7D") {
            let json = fs::read_to_string(port().join(format!("data/regions/{}/release.json", r.game_id))).unwrap();
            let info: serde_json::Value = serde_json::from_str(&json).unwrap();
            assert_eq!(info["game_id"], r.game_id);
            assert_eq!(info["dol_sha256"], r.dol_sha256);
            let hex = |k: &str| u32::from_str_radix(info["hle"][k].as_str().unwrap().trim_start_matches("0x"), 16).unwrap();
            assert_eq!(hex("ext_interrupt_dispatch"), r.ext_interrupt_dispatch);
            assert_eq!(hex("sched_lock_offset"), r.sched_lock_offset);
        }
        for r in RELEASES { assert!(port().join(r.index).is_file(), "{} missing", r.index); }
    }
}

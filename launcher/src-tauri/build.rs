// Embeds the native-port runtime (compressed) and pins the DOL it was recompiled from.
use sha2::{Digest, Sha256};
use std::{env, fs, path::PathBuf};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn main() {
    println!("cargo:rerun-if-env-changed=CRASH_RUNTIME_EXE");
    println!("cargo:rerun-if-env-changed=CRASH_DOL_SHA256");
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    // Publishable builds embed nothing: the recompiled runtime contains translated game code and must never ship inside the launcher.
    // Set CRASH_EMBED_RUNTIME=1 only for a private personal build.
    println!("cargo:rerun-if-env-changed=CRASH_EMBED_RUNTIME");
    println!("cargo:rerun-if-env-changed=CRASH_BUILDER_MANIFEST_URL");
    let embed = env::var("CRASH_EMBED_RUNTIME").map(|v| v == "1").unwrap_or(false);
    let exe = env::var("CRASH_RUNTIME_EXE").ok().map(PathBuf::from).filter(|p| p.exists());
    let (blob, sha, size) = match exe {
        Some(path) if embed => {
            println!("cargo:rerun-if-changed={}", path.display());
            let data = fs::read(&path).expect("read runtime");
            let sha = hex(&Sha256::digest(&data));
            let mut enc = zstd::Encoder::new(Vec::new(), 12).unwrap();
            enc.multithread(8).ok();
            std::io::copy(&mut &data[..], &mut enc).unwrap();
            (enc.finish().unwrap(), sha, data.len())
        }
        _ => (Vec::new(), String::new(), 0),
    };
    fs::write(out.join("runtime.zst"), blob).unwrap();
    // DOL pin: the recompiled code is only valid for the exact main.dol it was generated from.
    // The stages are qualified against one main.dol; data/main.dol.sha256 records its hash (no game data).
    let dol_sha = env::var("CRASH_DOL_SHA256").ok().filter(|s| s.len() == 64).or_else(|| {
        let pin = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../../data/main.dol.sha256");
        println!("cargo:rerun-if-changed={}", pin.display());
        fs::read_to_string(pin).ok().map(|s| s.trim().to_string()).filter(|s| s.len() == 64)
    }).unwrap_or_default();
    fs::write(out.join("embedded.rs"), format!(
        "pub const RUNTIME_SHA256: &str = \"{sha}\";\npub const RUNTIME_SIZE: usize = {size};\npub const DOL_SHA256: &str = \"{dol_sha}\";\n"
    )).unwrap();
    let revision = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../../builder-revision.json");
    println!("cargo:rerun-if-changed={}", revision.display());
    let patch_json=fs::read_to_string(revision).expect("Refresh the builder pack before building the launcher");
    fs::write(out.join("builder_identity.rs"),format!("pub const PATCHSET_JSON: &str = {patch_json:?};\n")).unwrap();
    tauri_build::build();
}

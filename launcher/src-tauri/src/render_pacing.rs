use std::{fs, path::{Path, PathBuf}, process::Command, sync::atomic::{AtomicU64, Ordering}};

pub fn control_path(profile: &Path) -> PathBuf { profile.join("render-wait.txt") }

/// Publish one complete command; the runtime keeps the last valid value on read errors.
pub fn write(profile: &Path, enabled: bool) -> Result<PathBuf, String> {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    fs::create_dir_all(profile).map_err(|e| e.to_string())?;
    let path = control_path(profile);
    let temp = profile.join(format!(".render-wait-{}-{}.tmp", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
    let result = fs::write(&temp, if enabled { "timer\n" } else { "cv\n" }).and_then(|_| {
        // A Windows CRT reader can briefly hold the old file without delete sharing.
        // Keep replacement atomic and retry that short overlap on the launcher thread.
        for attempt in 0..10 {
            match fs::rename(&temp, &path) {
                Err(error) if cfg!(windows) && error.kind() == std::io::ErrorKind::PermissionDenied && attempt < 9 =>
                    std::thread::sleep(std::time::Duration::from_millis(2)),
                result => return result,
            }
        }
        unreachable!()
    });
    if result.is_err() { let _ = fs::remove_file(&temp); }
    result.map_err(|e| format!("Could not update frame pacing: {e}"))?;
    Ok(path)
}

/// Always set a mode so an inherited environment cannot override the saved switch.
/// The runtime retains its CV fallback if the Windows timer cannot be created.
pub fn apply(command: &mut Command, enabled: bool, control: &Path) {
    command.env("NWII_RENDER_WAIT", if enabled { "timer" } else { "cv" });
    command.env("NWII_RENDER_WAIT_FILE", control);
    // An explicit legacy-drain rollback still wins inside the runtime.
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    #[test]
    fn saved_choice_overrides_environment_and_preserves_rollback() {
        let mut command = Command::new("unused");
        command.env("NWII_RENDER_WAIT", "external");
        command.env("NWII_LEGACY_DRAIN_WAKE", "1");
        for (enabled, mode) in [(true, "timer"), (false, "cv"), (true, "timer")] {
            apply(&mut command, enabled, Path::new("profile/render-wait.txt"));
            assert_eq!(command.get_envs().find(|(k, _)| *k == OsStr::new("NWII_RENDER_WAIT")).unwrap().1, Some(OsStr::new(mode)));
            assert_eq!(command.get_envs().find(|(k, _)| *k == OsStr::new("NWII_LEGACY_DRAIN_WAKE")).unwrap().1, Some(OsStr::new("1")));
            assert_eq!(command.get_envs().find(|(k, _)| *k == OsStr::new("NWII_RENDER_WAIT_FILE")).unwrap().1, Some(OsStr::new("profile/render-wait.txt")));
        }
    }

    #[test]
    fn live_commands_replace_the_existing_file_completely() {
        let dir = std::env::temp_dir().join(format!("crash-render-pacing-{}", std::process::id()));
        let path = write(&dir, true).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "timer\n");
        write(&dir, false).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "cv\n");
        write(&dir, true).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "timer\n");
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 1);
        fs::remove_file(path).unwrap(); fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn old_settings_enable_pacing_and_saved_off_survives_reload() {
        let mut settings: crate::Settings = serde_json::from_str("{}").unwrap();
        assert!(settings.frame_pacing);
        settings.frame_pacing = false;
        let saved = serde_json::to_string(&settings).unwrap();
        assert!(!serde_json::from_str::<crate::Settings>(&saved).unwrap().frame_pacing);
    }
}

use std::process::Command;

/// Longest accepted camera glide. Half a second is already a very noticeable
/// glide; longer values make the camera feel detached from the player.
pub const MAX_MS: u32 = 500;

/// Duration used when an enabled profile has no usable value (the runtime reads
/// a zero as "disabled", so an enabled setting must never publish one).
pub const DEFAULT_MS: u32 = 200;

/// Keep a profile or UI value inside the range the runtime accepts.
pub fn clamp_milliseconds(milliseconds: u32) -> u32 {
    milliseconds.min(MAX_MS)
}

/// Enabled publishes the duration and clears the rollback switch; disabled
/// publishes the rollback switch and clears the duration, so an inherited
/// environment cannot override the saved choice.
pub fn apply(command: &mut Command, enabled: bool, milliseconds: u32) {
    if enabled {
        let milliseconds = clamp_milliseconds(milliseconds);
        let milliseconds = if milliseconds == 0 { DEFAULT_MS } else { milliseconds };
        command.env("NWII_CAMERA_GLIDE_MS", milliseconds.to_string());
        command.env_remove("NWII_NO_CAMERA_GLIDE");
    } else {
        command.env("NWII_NO_CAMERA_GLIDE", "1");
        command.env_remove("NWII_CAMERA_GLIDE_MS");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    fn env<'a>(command: &'a Command, key: &str) -> Option<Option<&'a OsStr>> {
        command.get_envs().find(|(k, _)| *k == OsStr::new(key)).map(|(_, v)| v)
    }

    #[test]
    fn enabled_publishes_duration_and_disabled_publishes_rollback() {
        let mut command = Command::new("unused");
        command.env("NWII_CAMERA_GLIDE_MS", "999").env("NWII_NO_CAMERA_GLIDE", "1");

        apply(&mut command, true, 200);
        assert_eq!(env(&command, "NWII_CAMERA_GLIDE_MS"), Some(Some(OsStr::new("200"))));
        assert_eq!(env(&command, "NWII_NO_CAMERA_GLIDE"), Some(None));

        apply(&mut command, false, 200);
        assert_eq!(env(&command, "NWII_CAMERA_GLIDE_MS"), Some(None));
        assert_eq!(env(&command, "NWII_NO_CAMERA_GLIDE"), Some(Some(OsStr::new("1"))));

        apply(&mut command, true, 200);
        assert_eq!(env(&command, "NWII_CAMERA_GLIDE_MS"), Some(Some(OsStr::new("200"))));
        assert_eq!(env(&command, "NWII_NO_CAMERA_GLIDE"), Some(None));
    }

    #[test]
    fn duration_is_clamped() {
        assert_eq!(clamp_milliseconds(0), 0);
        assert_eq!(clamp_milliseconds(200), 200);
        assert_eq!(clamp_milliseconds(501), MAX_MS);
        assert_eq!(clamp_milliseconds(u32::MAX), MAX_MS);

        let mut command = Command::new("unused");
        apply(&mut command, true, u32::MAX);
        assert_eq!(env(&command, "NWII_CAMERA_GLIDE_MS"), Some(Some(OsStr::new("500"))));

        // An enabled profile must never publish zero: the runtime reads that as "off".
        let mut command = Command::new("unused");
        apply(&mut command, true, 0);
        assert_eq!(env(&command, "NWII_CAMERA_GLIDE_MS"), Some(Some(OsStr::new("200"))));
    }
}

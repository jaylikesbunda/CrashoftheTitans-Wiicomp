use std::process::Command;

pub fn apply(cmd: &mut Command, enabled: bool) {
    if enabled {
        cmd.env("NWII_DIRECT_FIFO", "1");
    } else {
        cmd.env_remove("NWII_DIRECT_FIFO");
    }
    // Preserve an inherited NWII_NO_DIRECT_FIFO override in either mode.
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    #[test]
    fn toggle_and_rollback_environment() {
        let mut cmd = Command::new("unused");
        cmd.env("NWII_NO_DIRECT_FIFO", "1");
        apply(&mut cmd, true);
        assert_eq!(cmd.get_envs().find(|(k, _)| *k == OsStr::new("NWII_DIRECT_FIFO")).unwrap().1, Some(OsStr::new("1")));
        assert_eq!(cmd.get_envs().find(|(k, _)| *k == OsStr::new("NWII_NO_DIRECT_FIFO")).unwrap().1, Some(OsStr::new("1")));
        apply(&mut cmd, false);
        assert_eq!(cmd.get_envs().find(|(k, _)| *k == OsStr::new("NWII_DIRECT_FIFO")).unwrap().1, None);
    }
}

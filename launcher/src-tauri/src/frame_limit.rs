use std::process::Command;
pub fn sanitize(value: Option<f64>) -> Option<f64> {
    value.filter(|fps| fps.is_finite() && (10.0..=1000.0).contains(fps))
}
pub fn apply(command: &mut Command, value: Option<f64>) {
    if let Some(fps) = sanitize(value) { command.env("NWII_FPS_LIMIT", fps.to_string()).env("NWII_SINGLE_VI", "1"); }
    else { command.env_remove("NWII_FPS_LIMIT").env_remove("NWII_SINGLE_VI"); }
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn limits_and_old_settings() {
        assert_eq!(sanitize(None), None);
        for invalid in [0.0, -60.0, 9.9, 1000.1, f64::NAN, f64::INFINITY] { assert_eq!(sanitize(Some(invalid)), None); }
        for valid in [10.0, 50.0, 59.94, 60.0, 144.0, 1000.0] { assert_eq!(sanitize(Some(valid)), Some(valid)); }
        // New profiles default to a custom 120 FPS cap; a saved Regional choice (null) stays regional.
        let settings: crate::Settings = serde_json::from_str("{}").unwrap(); assert_eq!(settings.custom_fps, Some(120.0));
        assert_eq!(serde_json::from_str::<crate::Settings>(r#"{"custom_fps":null}"#).unwrap().custom_fps, None);
        let mut settings = settings; settings.custom_fps = Some(59.94);
        let saved = serde_json::to_string(&settings).unwrap();
        assert_eq!(serde_json::from_str::<crate::Settings>(&saved).unwrap().custom_fps, Some(59.94));
    }
    #[test] fn command_custom_and_restore_regional() {
        let mut cmd = Command::new("unused"); apply(&mut cmd, Some(144.0));
        assert!(cmd.get_envs().any(|(key,value)| key=="NWII_FPS_LIMIT" && value==Some(std::ffi::OsStr::new("144"))));
        apply(&mut cmd,None); assert!(cmd.get_envs().any(|(key,value)| key=="NWII_FPS_LIMIT" && value.is_none()));
    }
}

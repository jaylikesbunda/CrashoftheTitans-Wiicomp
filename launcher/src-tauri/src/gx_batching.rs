use std::process::Command;

pub fn normalize(value: &str) -> &str {
    match value {"adaptive4" | "adaptive8" | "fixed4" | "fixed8" => value, _ => "legacy"}
}
pub fn apply(cmd: &mut Command, value: &str) {
    let mode=normalize(value);
    if mode=="legacy" {cmd.env_remove("NWII_GX_BATCHING");} else {cmd.env("NWII_GX_BATCHING",mode);}
    // Explicit NWII_NO_ADAPTIVE_GX_BATCHING and early-drain rollback remain inherited.
}
#[cfg(test)] mod tests {
    use super::*;
    use std::ffi::OsStr;
    #[test] fn modes_and_rollback(){
        assert_eq!(normalize("bad"),"legacy");
        for mode in ["adaptive4","adaptive8","fixed4","fixed8"] {
            let mut c=Command::new("unused");c.env("NWII_NO_ADAPTIVE_GX_BATCHING","1");apply(&mut c,mode);
            assert_eq!(c.get_envs().find(|(k,_)|*k==OsStr::new("NWII_GX_BATCHING")).unwrap().1,Some(OsStr::new(mode)));
            assert_eq!(c.get_envs().find(|(k,_)|*k==OsStr::new("NWII_NO_ADAPTIVE_GX_BATCHING")).unwrap().1,Some(OsStr::new("1")));
            apply(&mut c,"legacy");assert_eq!(c.get_envs().find(|(k,_)|*k==OsStr::new("NWII_GX_BATCHING")).unwrap().1,None);
        }
    }
}

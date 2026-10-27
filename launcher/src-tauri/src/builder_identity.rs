use serde::{Deserialize, Serialize};
use sha2::{Digest,Sha256};
use std::{collections::BTreeMap,fs,path::Path};
include!(concat!(env!("OUT_DIR"),"/builder_identity.rs"));

#[derive(Deserialize, PartialEq)]
struct Patchset {schema_version:u32,revision:String,required_inputs:BTreeMap<String,String>}
pub fn revision()->String {serde_json::from_str::<Patchset>(PATCHSET_JSON).expect("compiled patch identity").revision}
/// One hash of the whole compiled patch set, as tools/builder_revision.py `digest` computes it (JSON with sorted keys, no spaces).
/// A runtime built from other inputs is out of date even if the revision string matches.
pub fn inputs_digest()->String {
 let p=serde_json::from_str::<Patchset>(PATCHSET_JSON).expect("compiled patch identity");
 let s=|v:&str|serde_json::to_string(v).unwrap();
 let inputs=p.required_inputs.iter().map(|(k,v)|format!("{}:{}",s(k),s(v))).collect::<Vec<_>>().join(",");
 let text=format!("{{\"required_inputs\":{{{inputs}}},\"revision\":{},\"schema_version\":{}}}",s(&p.revision),p.schema_version);
 format!("{:x}",Sha256::digest(text.as_bytes()))
}
/// The folder a runtime for this disc and patch set is built in.
pub fn build_dir_name(dol_sha:&str)->String { format!("clean-room-{}-{}-{}",&dol_sha[..12.min(dol_sha.len())],revision(),&inputs_digest()[..8]) }
/// Hashing every required input reads about 40 MB, and the UI asks for builder status on every state refresh. The result is
/// cached per root until its builder-revision.json changes (size or modification time).
pub fn valid_builder(root:&Path)->bool {
 use std::{collections::HashMap,sync::{Mutex,OnceLock},time::SystemTime};
 static CACHE:OnceLock<Mutex<HashMap<std::path::PathBuf,(Option<SystemTime>,u64,bool)>>>=OnceLock::new();
 let Ok(meta)=fs::metadata(root.join("builder-revision.json")) else{return false};
 let key=(meta.modified().ok(),meta.len());
 let cache=CACHE.get_or_init(Default::default);
 if let Some(&(m,l,ok))=cache.lock().unwrap().get(root){ if (m,l)==key {return ok;} }
 let ok=valid_builder_uncached(root);
 cache.lock().unwrap().insert(root.to_path_buf(),(key.0,key.1,ok));
 ok
}
fn valid_builder_uncached(root:&Path)->bool {
 let Ok(actual)=fs::read(root.join("builder-revision.json")) else{return false};
 let Ok(actual)=serde_json::from_slice::<Patchset>(&actual) else{return false};
 let Ok(expected)=serde_json::from_str::<Patchset>(PATCHSET_JSON) else{return false};
 if actual!=expected{return false;}
 actual.required_inputs.iter().all(|(relative,hash)|fs::read(root.join(relative)).map(|b|format!("{:x}",Sha256::digest(b))==*hash).unwrap_or(false))
}
#[derive(Serialize,Deserialize)]
pub struct RuntimeBuild {pub builder_revision:String,#[serde(default)] pub builder_inputs:String,pub dol_sha256:String,pub executable_sha256:String}
pub fn runtime_matches(exe:&Path,dol_sha:&str)->bool {
 if !exe.is_file(){return false;}
 fs::read(exe.with_file_name("runtime-build.json")).ok().and_then(|b|serde_json::from_slice::<RuntimeBuild>(&b).ok())
  .map(|m|m.builder_revision==revision() && m.builder_inputs==inputs_digest() && m.dol_sha256==dol_sha && m.executable_sha256.len()==64).unwrap_or(false)
}
#[cfg(test)] mod tests {
 use super::*;
 #[test] fn runtime_requires_current_patchset_and_disc(){
  let dir=std::env::temp_dir().join(format!("crash-runtime-identity-{}",std::process::id()));fs::create_dir_all(&dir).unwrap();
  let exe=dir.join("CrashNative.exe");fs::write(&exe,b"fixture").unwrap();assert!(!runtime_matches(&exe,"disc-a"));
  let marker=exe.with_file_name("runtime-build.json");
  let mut meta=RuntimeBuild{builder_revision:"old".into(),builder_inputs:inputs_digest(),dol_sha256:"disc-a".into(),executable_sha256:"0".repeat(64)};
  fs::write(&marker,serde_json::to_vec(&meta).unwrap()).unwrap();assert!(!runtime_matches(&exe,"disc-a"));
  meta.builder_revision=revision();fs::write(&marker,serde_json::to_vec(&meta).unwrap()).unwrap();
  assert!(runtime_matches(&exe,"disc-a"));assert!(!runtime_matches(&exe,"disc-b"));
  // Same revision string, different builder inputs: out of date.
  meta.builder_inputs="0".repeat(64);fs::write(&marker,serde_json::to_vec(&meta).unwrap()).unwrap();assert!(!runtime_matches(&exe,"disc-a"));
  fs::remove_file(exe).unwrap();fs::remove_file(marker).unwrap();fs::remove_dir(dir).unwrap();
 }
 #[test] fn packaged_builder_matches_declared_inputs(){
  let root=Path::new(env!("CARGO_MANIFEST_DIR")).join("builder-pack/native-builder");
  assert!(valid_builder(&root));
 }
 #[test] fn inputs_digest_matches_the_builder_tools(){
  // The same value tools/builder_revision.py records, from the repository's builder-revision.json.
  let repo=Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
  let out=std::process::Command::new("python").current_dir(&repo)
   .args(["-c","import json,sys;sys.path.insert(0,'tools');from builder_revision import digest;print(digest(json.load(open('builder-revision.json'))))"]).output();
  let Ok(out)=out else{return};   // no Python on this machine
  if !out.status.success(){return;}
  assert_eq!(String::from_utf8_lossy(&out.stdout).trim(),inputs_digest());
 }
}

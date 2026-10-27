"""Refresh an existing toolchain pack's patch sources in place; never delete tools or game data."""
from pathlib import Path
import argparse,shutil,sys
p=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(p/'tools'))
from builder_revision import identity,validate
import json
ap=argparse.ArgumentParser();ap.add_argument('--pack',type=Path,default=p/'launcher/src-tauri/builder-pack');args=ap.parse_args()
pack=args.pack.resolve();dest=pack/'native-builder'
if not (dest/'vendor/NWiiRecomp/PINNED_COMMIT').is_file() or not (pack/'toolchain/python/python.exe').is_file():raise SystemExit('Expected an existing complete builder pack: '+str(pack))
GAME_CODE=('bounded-blocks','leaf-before-*.cpp','hot-before-*.cpp','hot_fp_bank_candidate.cpp','sdk_sqrt_baseline.cpp','sdk_sqrt_local_loops.inc')
LOCAL=('*-before*','*_baseline.*')   # machine-local stage snapshots (see .gitignore); also swept out of an older pack below
for tree in ('tools','compiler','runtime-overlays','patches','data'):
 shutil.copytree(p/tree,dest/tree,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.log',*GAME_CODE,*(LOCAL if tree=='runtime-overlays' else ())))
for name in GAME_CODE+LOCAL:
 for old in (dest/'runtime-overlays').glob(name):shutil.rmtree(old) if old.is_dir() else old.unlink()
shutil.rmtree(dest/'reference/user',ignore_errors=True)
shutil.copytree(p/'reference/backend-reports',dest/'reference/backend-reports',dirs_exist_ok=True)
shutil.copytree(p/'reference/guest-regions',dest/'reference/guest-regions',dirs_exist_ok=True)
if (p/'reference/geometry').exists():shutil.copytree(p/'reference/geometry',dest/'reference/geometry',dirs_exist_ok=True)
value=identity(p)
for root in (p,dest):
 target=root/'builder-revision.json';text=json.dumps(value,indent=2)+'\n'
 if not target.exists() or target.read_text()!=text:target.write_text(text)
validate(dest)
print('Refreshed native patch sources and verified revision '+value['revision']+' at '+str(pack))

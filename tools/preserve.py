"""Read-only snapshot of the decomp; verify with --check. Never restores automatically."""
import hashlib, json, subprocess, sys, zipfile
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'native-port/preservation'
def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()
def inventory():
    paths = []
    for folder in ('src/c','src/external','src/radical','src/master_match','scripts/match','docs','include','config'):
        base = ROOT/folder
        if base.exists():
            paths.extend(p for p in base.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.o','.pyc') and 'cache' not in p.name and 'treemap' not in p.name)
    paths += [ROOT/p for p in ('AGENTS.md','README.md','export/index.tsv','orig/main.dol')]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(set(paths)) if p.exists()}
OUT.mkdir(parents=True, exist_ok=True)
snapshot = OUT/'baseline.json'
if '--check' in sys.argv:
    old = json.loads(snapshot.read_text())['files']; now = inventory()
    changed = [p for p in old if now.get(p) != old[p]]
    added = sorted(set(now)-set(old))
    result = {'changed_or_missing': changed, 'added': added, 'pass': not changed and not added}
    (OUT/'integrity_check.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result)); sys.exit(0 if result['pass'] else 1)
if snapshot.exists(): raise SystemExit('Baseline exists; refusing to replace it.')
files=inventory()
status=subprocess.check_output(['git','status','--porcelain=v1','-z'],cwd=ROOT)
(OUT/'git-status-before.bin').write_bytes(status)
with zipfile.ZipFile(OUT/'decomp-recovery.zip','w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
    for p in files:
        if p != 'orig/main.dol': z.write(ROOT/p,p)
snapshot.write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'files':files},indent=2))
print(f'Preserved {len(files)} files; recovery ZIP and SHA-256 inventory written.')

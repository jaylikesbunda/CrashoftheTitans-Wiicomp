"""Aggregate full-port provenance; never imports anything into matching corpus."""
from pathlib import Path
import csv, hashlib, json, os, re
from port_paths import PORT,INDEX,default_dol
DOL=Path(os.environ.get('NWII_INPUT_DOL') or default_dol());MAP=Path(os.environ.get('NWII_INPUT_INDEX') or INDEX)
generated=PORT/'build/crash-generated'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
with MAP.open(newline='') as f:
    rows=list(csv.reader(f,delimiter='\t'))[1:]
addresses={int(r[0],16) for r in rows if len(r)>=2}
declared={int(a,16) for a in re.findall(r'void func_([0-9A-Fa-f]+)\(', (generated/'functions.h').read_text())}
defined=set();chunks=list(generated.glob('output_*.cpp'))
for p in chunks:
    defined.update(int(a,16) for a in re.findall(r'void func_([0-9A-Fa-f]+)\(',p.read_text()))
result={'scope':'Full isolated native port, no strict admissions or benchmark target analysis',
        'mapped_functions':len(addresses),'declared_functions':len(declared),'defined_functions':len(defined),
        'complete_map_import':addresses==defined==declared,'translation_chunks':len(chunks),
        'generated_cpp_bytes':sum(p.stat().st_size for p in chunks)+(generated/'main_output.cpp').stat().st_size,
        'original_dol_sha256':sha(DOL),'function_map_sha256':sha(MAP),
        'patched_runtime_cpu_header_sha256':sha(PORT/'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h')}
(PORT/'reports/translation-inventory.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
if not result['complete_map_import']:raise SystemExit('Incomplete map import')

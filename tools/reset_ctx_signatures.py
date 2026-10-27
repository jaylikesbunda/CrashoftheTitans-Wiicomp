"""Undo fix_ctx_restrict_variant.py's signature rewrite so every earlier stage sees `void func_X(CPUContext& ctx) {` on a re-run.

The stage list is applied to chunks that persist between runs; most early stages match the original signature text. The
restrict stage re-applies the rewrite later in the same pass. Idempotent and behaviour-neutral (NWII_CTX_PARAM expands to CPUContext&).
"""
from pathlib import Path
import re
gen=Path(__file__).resolve().parents[1]/'build/crash-generated'
pattern=re.compile(r'^(void func_[0-9A-F]{8}\()NWII_CTX_PARAM( ctx\) \{)$',re.M)
total=0
for f in sorted(gen.glob('output_*.cpp'))+[gen/'main_output.cpp']:
    s=f.read_text(errors='surrogateescape');t,n=pattern.subn(r'\1CPUContext&\2',s)
    if n:f.write_text(t,errors='surrogateescape');total+=n
print(f'{total} restrict-style signatures restored')

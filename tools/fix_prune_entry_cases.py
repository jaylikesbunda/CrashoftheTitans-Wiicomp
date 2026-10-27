"""Prune the per-instruction entry switch of multi-entry generated functions down to the addresses that can actually be entered.

Every generated function that has more than one possible entry starts with
    if (ctx.pc != START) { switch (ctx.pc) { case 0xA: goto loc_A; case 0xB: goto loc_B; ... default: interpret_step(ctx); return; } }
with one case for EVERY instruction. The compiler must therefore assume that any instruction boundary can be entered from memory state,
so no guest register can stay in a host register across instructions, every label is a jump-table target (padded and aligned), and
the machine code is ~120 bytes per guest instruction (.text is 139 MB for a few MB of PowerPC code; Zen 2 has a 32 KiB L1I).

A guest pc can only be entered mid-function when it is one of:
  * a constant the translated code itself produces: ctx.pc = ADDR (calls, tail jumps, poll sites, fall-through, sc), ctx.lr = ADDR
    (bl return addresses), require_fpu(ADDR) / any other 0x8xxxxxxx literal in executable statements;
  * a pointer-sized word in a DOL data section that lands inside a function (switch jump tables, vtables, callback tables).
Function starts never need a case. Everything else keeps `default: interpret_step(ctx)`, so a missed entry is still executed correctly
(slowly, and visible in the [INTERP] histogram) rather than wrongly. output_74.cpp (pinned bounded blocks) is not edited.

Run on the generated chunks; idempotent (already-pruned switches have fewer cases and are re-pruned to the same set).
"""
from pathlib import Path
import re,struct,sys
p=Path(__file__).resolve().parents[1];gen=p/'build/crash-generated'
repo=p.parent
LO,HI=0x80000000,0x81800000
CASE=re.compile(r'^( +)case 0x([0-9A-F]{8}): goto loc_([0-9A-F]{8});\n',re.M)
LITERAL=re.compile(r'0x(8[0-9A-Fa-f]{7})\b')
def executable_literals(text):
    out=set()
    for line in text.split('\n'):
        stripped=line.lstrip()
        if stripped.startswith('//') or not stripped:continue
        if stripped.startswith('case 0x') and ': goto loc_' in stripped:continue
        if stripped.startswith('if (ctx.pc != 0x') or 'trace_call(0x' in stripped:continue
        code=line.split('//',1)[0]
        for m in LITERAL.finditer(code):
            v=int(m[1],16)
            if LO<=v<HI and v%4==0:out.add(v)
    return out
def dol_data_targets(dol):
    data=dol.read_bytes();targets=set()
    text_off=struct.unpack('>18I',data[0:0x48]);addr=struct.unpack('>18I',data[0x48:0x90]);size=struct.unpack('>18I',data[0x90:0xD8])
    for i in range(7,18):
        if not size[i]:continue
        blob=data[text_off[i]:text_off[i]+size[i]]
        for o in range(0,len(blob)-3,4):
            v=struct.unpack_from('>I',blob,o)[0]
            if LO<=v<HI and v%4==0:targets.add(v)
    return targets
def collect(files):
    t=set()
    for f in files:t|=executable_literals(f.read_text(errors='surrogateescape'))
    return t
def prune(text,targets):
    kept=removed=0
    def sub(m):
        nonlocal kept,removed
        if int(m[2],16) in targets:kept+=1;return m[0]
        removed+=1;return ''
    return CASE.sub(sub,text),kept,removed
if __name__=='__main__':
    files=sorted(gen.glob('output_*.cpp'))+[gen/'main_output.cpp']
    targets=collect(files)
    dol=repo/'orig/main.dol'
    data_targets=dol_data_targets(dol) if dol.exists() else None
    assert data_targets is not None,'orig/main.dol is required to find jump-table and vtable targets'
    targets|=data_targets
    total_kept=total_removed=0;changed=0
    for f in files:
        if f.name=='output_74.cpp':continue
        s=f.read_text(errors='surrogateescape');t,k,r=prune(s,targets)
        total_kept+=k;total_removed+=r
        if t!=s:f.write_text(t,errors='surrogateescape');changed+=1
    print(f'Entry pruning: {len(targets)} possible entry addresses; {total_removed} switch cases removed, {total_kept} kept, {changed} chunks rewritten.')

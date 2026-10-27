"""Transitive guest-function write-sets from the DOL (Tier 2 phase 1).

visible_writes(F): resources a caller can observe changed after F returns normally =
  (writes of F's own instructions  U  visible_writes of its direct callees  U  everything for indirect calls)
  minus the non-volatile registers F preserves (stored relative to r1 / via save millicode and reloaded from it).
Millicode (_savegpr/_restgpr style, one function per instruction falling through) is recognised by shape.
"""
import json, re, struct, collections
from pathlib import Path
from ppc_effects import effects, EVERYTHING

NONVOL = {f'r{i}' for i in range(14, 32)} | {f'f{i}' for i in range(14, 32)} | {'cr2', 'cr3', 'cr4'}


def load_text(dol):
    d = Path(dol).read_bytes(); words = {}
    for s in range(7):
        off, addr, size = [struct.unpack_from('>I', d, o + 4 * s)[0] for o in (0, 0x48, 0x90)]
        for i in range(0, size, 4):
            words[addr + i] = struct.unpack_from('>I', d, off + i)[0]
    return words


def load_bounds(text):
    return sorted({(int(a, 16), int(b, 16)) for a, b in re.findall(r'\{0x([0-9A-F]{8}), 0x([0-9A-F]{8}), func_[0-9A-F]{8}\}', text)})


def analyse(dol,main_text):
    words = load_text(dol)
    bounds = load_bounds(main_text)
    funcs = {}
    for start, end in bounds:
        ins = [(pc, words[pc]) for pc in range(start, end, 4) if pc in words]
        funcs[start] = dict(end=end, ins=ins)
    # millicode: every instruction a r11-relative store (save) or load (restore) until blr
    save_mc, rest_mc = {}, {}
    for start, fn in funcs.items():
        regs_s, regs_l, ok = set(), set(), True
        for pc, w in fn['ins']:
            op = w >> 26; ra = (w >> 16) & 31; rd = (w >> 21) & 31
            if w == 0x4E800020: break
            if op in (36, 54) and ra == 11: regs_s.add(('r' if op == 36 else 'f') + str(rd))
            elif op in (32, 50) and ra == 11: regs_l.add(('r' if op == 32 else 'f') + str(rd))
            else: ok = False; break
        if ok and regs_s and not regs_l: save_mc[start] = regs_s
        if ok and regs_l and not regs_s: rest_mc[start] = regs_l
    direct, calls, indirect, preserved = {}, collections.defaultdict(set), set(), {}
    for start, fn in funcs.items():
        W = set(); saved, restored = set(), set()
        for pc, w in fn['ins']:
            e = effects(pc, w)
            op = w >> 26; ra = (w >> 16) & 31; rd = (w >> 21) & 31
            if e.kind in ('branch', 'cbranch') and e.target is not None and not (start <= e.target < fn['end']):
                calls[start].add(e.target)  # tail call / cross-function branch
            if e.kind == 'call' and e.target is not None:
                calls[start].add(e.target)
                if e.target in save_mc: saved |= save_mc[e.target]
                if e.target in rest_mc: restored |= rest_mc[e.target]
            elif e.kind == 'indirect_call':
                indirect.add(start)
            if ra == 1 and op in (36, 47, 54, 60):  # stw stmw stfd psq_st off(r1)
                saved |= {f'r{i}' for i in range(rd, 32)} if op == 47 else {('r' if op == 36 else 'f') + str(rd)}
            if ra == 1 and op in (32, 46, 50, 56):  # lwz lmw lfd psq_l off(r1)
                restored |= {f'r{i}' for i in range(rd, 32)} if op == 46 else {('r' if op == 32 else 'f') + str(rd)}
            W |= e.writes
        if fn['ins']:
            last = effects(*fn['ins'][-1])
            if last.kind not in ('branch', 'return', 'indirect_branch') and fn['end'] in funcs:
                calls[start].add(fn['end'])  # falls through into the next function (e.g. _restgpr_N chains)
        if len(fn['ins']) != (fn['end']-start)//4: W |= EVERYTHING
        direct[start] = W
        preserved[start] = (saved & restored) & NONVOL
    # mfcr/mtcrf save of cr2-4 is the usual nonvolatile CR pattern; treat cr2-4 written-and-mtcrf-restored as preserved
    starts = sorted(funcs)
    import bisect
    def owner_of(t):
        i = bisect.bisect_right(starts, t) - 1
        return starts[i] if i >= 0 and t < funcs[starts[i]]['end'] else None
    summary = {s: set(direct[s]) for s in funcs}
    for s in indirect:
        summary[s] |= EVERYTHING
    changed = True; rounds = 0
    while changed:
        changed = False; rounds += 1
        for s in funcs:
            acc = set(summary[s])
            for t in calls[s]:
                if t in summary: acc |= summary[t]
                else:
                    owner = owner_of(t)
                    acc |= summary[owner] if owner is not None else EVERYTHING
            acc -= preserved[s]
            if acc != summary[s]:
                summary[s] = acc; changed = True
    return funcs, summary, preserved, save_mc, rest_mc, rounds



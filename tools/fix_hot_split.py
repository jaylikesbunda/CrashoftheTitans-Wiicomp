"""Move the measured-hot guest functions into their own, promoted, hotness-ordered translation units.

Why: the profile (frame-cost-20261006-221021) is a flat tail, but 90% of the guest-thread samples are in ~390 functions (4.9 MiB of code)
and 99% in ~640 (6.7 MiB), spread over ~89 MB of .text in guest-address order, and every chunk is compiled -Os. Moving the hot set into
a few contiguous units (hottest first) packs it for the instruction cache / TLB, lets it be compiled with its own flags, and lets
tools/promote_regs.py keep the guest register file in host registers inside those functions.

The pristine chunks in build/crash-generated are never edited (bounded-block regions are hash-pinned there). Each pass this stage derives:
  build/crash-split/output_N.cpp   chunk N with its hot functions cut out (only chunks that have any)
  build/crash-hot/hot_KKK.cpp      hot functions (promoted when promote_regs accepts them, original text otherwise), hottest first
and points CMakeLists.txt at them. Hot list: tools/hot_functions.txt (one func_XXXXXXXX per line, hottest first).
Environment: NWII_HOT_SPLIT=0 disables (CMake keeps pointing at crash-generated), NWII_HOT_OPT=O2|Os|O1 picks the hot units' level
(default Os: measured 5% faster than O2 on the promoted functions), NWII_HOT_FMA=0 drops -mfma (hardware FMA3 is required with it), NWII_NEST_INDIRECT=0 keeps indirect calls returning to the dispatcher, NWII_PROMOTE=0 keeps the hot units unpromoted, NWII_PROMOTE_FP=0 promotes integer state only.
NWII_BUILD_CALLEE_EFFECTS=1 enables the source-bound effects experiment (default off: paired actual-callee tests regressed).
Runs after the other chunk stages (it reads their final text) and before fix_chunk_size_flags.py. Idempotent.
"""
from pathlib import Path
import os, re, shutil, sys
p = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(p / 'tools'))
import promote_regs as P

gen = p / 'build/crash-generated'
split = p / 'build/crash-split'
hot_dir = p / 'build/crash-hot'
cm = p / 'build/nwii-full-source/CMakeLists.txt'
MARK = '# NATIVE_HOT_SPLIT_V1'
CHUNK_MARK = '# NATIVE_CHUNK_SIZE_FLAGS_V1'
NL = '\n'
HEADER_LINE = re.compile(r'^#include ".*(?:bounded-blocks/output_\d+\.h|native_plain_leaf_support\.h|native_guest_fill_block\.h)"$')

def write_generated(path,text):
    # Stable source timestamps preserve valid object files on an idempotent pass.
    if not path.exists() or path.read_text(errors='surrogateescape')!=text:
        path.write_text(text,errors='surrogateescape')


def make_nest_predicate():
    """nest(func, ret): would run_game's dispatch of the return address `ret` enter `func` again (so an indirect call can be made inline)?
    Replicates find_dispatch_target: exact-pc override first, else the first g_func_bounds entry whose prefix-maximum end passes the pc."""
    import bisect
    m = (gen / 'main_output.cpp').read_text(errors='surrogateescape')
    bounds = [(int(a, 16), int(b, 16), n) for a, b, n in re.findall(r'^\s*\{(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),\s*(func_[0-9A-F]{8})\},', m, re.M)]
    overrides = {int(a, 16): (n, h == 'true') for a, n, h in re.findall(r'^\s*\{(0x[0-9A-Fa-f]+),\s*(func_[0-9A-F]{8}),\s*(false|true)\},', m, re.M)}
    ends = []
    high = 0
    for _, e, _ in bounds:
        high = max(high, e)
        ends.append(high)
    assert len(bounds) > 1000 and len(overrides) > 10, (len(bounds), len(overrides))

    def nest(func, ret):
        if 0x803EC364 <= ret <= 0x803EEF0C:
            return False                    # native_wpad_dispatch range: leave to the dispatcher
        if ret in overrides:
            return overrides[ret][0] == func and not overrides[ret][1]
        lo = bisect.bisect_right(ends, ret)
        if lo >= len(bounds) or bounds[lo][0] > ret:
            return False
        return bounds[lo][2] == func
    return nest


def cmake_clean(text):
    """Drop a previous block of ours (up to the next marker block) and restore chunk paths."""
    if MARK in text:
        a = text.index(MARK)
        b = text.find(NL + '# NATIVE_', a + len(MARK))
        text = text[:a].rstrip(NL) + NL + (text[b:] if b >= 0 else '')
    return text


def function_span(text, name):
    m = re.search(r'^void ' + name + r'\(NWII_CTX_PARAM ctx\) \{$', text, re.M)
    if not m:
        return None
    end = text.find(NL + '}' + NL, m.start())
    if end < 0:
        end = len(text) - 2
    return m.start(), end + len(NL + '}')


def main():
    for target in (split,hot_dir):
        assert target.resolve().parent==(p/'build').resolve() and target.name in ('crash-split','crash-hot'), target
    enabled = os.environ.get('NWII_HOT_SPLIT', '1') != '0'
    t = cm.read_text()
    t = cmake_clean(t)
    # undo earlier path rewrites so the swap below always starts from the pristine layout
    t = t.replace('/build/crash-split/', '/build/crash-generated/')
    if not enabled:
        cm.write_text(t)
        shutil.rmtree(split, ignore_errors=True)
        shutil.rmtree(hot_dir, ignore_errors=True)
        print('Hot split disabled (NWII_HOT_SPLIT=0).')
        return
    hot = [l.strip() for l in (p / 'tools/hot_functions.txt').read_text().split(NL) if l.strip().startswith('func_')]
    if os.environ.get('NWII_HOT_CLOSURE','1')!='0':
        from expand_hot_closure import expand
        hot+=expand(p,hot)
    rank = {n: i for i, n in enumerate(hot)}
    do_promote = os.environ.get('NWII_PROMOTE', '1') != '0'
    nest = make_nest_predicate() if os.environ.get('NWII_NEST_INDIRECT', '1') != '0' else None
    do_fp = os.environ.get('NWII_PROMOTE_FP', '1') != '0'
    effect_promoter = None
    if do_promote and os.environ.get('NWII_BUILD_CALLEE_EFFECTS', '0') != '0':
        from prepare_callee_effects import prepare
        effect_promoter = prepare(p)
    entry_specializer = None
    typed_leaves = {}
    if do_promote and do_fp and effect_promoter is None and os.environ.get('NWII_BUILD_ENTRY_SPECIALIZATION','1')!='0':
        from guest_entry_specialize import specialize
        import json
        entry_excluded=set(json.loads((p/'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
        entry_specializer=specialize
        if os.environ.get('NWII_BUILD_GUEST_BATCH','1')!='0':
            from guest_typed_leaves import collect
            typed_leaves=collect(sorted(gen.glob('output_*.cpp')),entry_excluded)
    # NATIVE_ALWAYS_PRUNE_FPU_V1: run the existing, sound prune_fpu must-analysis on every promoted
    # hot body too, not only inside successful entry specialisation (the frozen/blocked functions
    # otherwise keep all of their provably-redundant MSR.FP guards).
    always_prune=None
    if do_promote and os.environ.get('NWII_BUILD_ALWAYS_PRUNE_FPU','1')!='0':
        from guest_entry_specialize import prune_fpu as always_prune
        header=p/'build/nwii-full-source/nWiiRuntime/include/runtime/native_guest_entry.h'
        value=(p/'runtime-overlays/native_guest_entry.h').read_text()
        if not header.exists() or header.read_text()!=value:header.write_text(value)
        header=p/'build/nwii-full-source/nWiiRuntime/include/runtime/native_loop_span.h'
        value=(p/'runtime-overlays/native_loop_span.h').read_text()
        if not header.exists() or header.read_text()!=value:header.write_text(value)
        header=p/'build/nwii-full-source/nWiiRuntime/include/runtime/native_linked_region.h'
        value=(p/'runtime-overlays/native_linked_region.h').read_text()
        if not header.exists() or header.read_text()!=value:header.write_text(value)
    opt = os.environ.get('NWII_HOT_OPT', 'Os')
    # std::fma is a libm call without hardware FMA (174 call sites in the first hot unit alone); FMA3 is correctly rounded, so results are bit-identical
    fma = '-mfma;' if os.environ.get('NWII_HOT_FMA', '1') != '0' else ''
    assert opt in ('O2', 'Os', 'O1'), opt
    split.mkdir(parents=True,exist_ok=True)
    hot_dir.mkdir(parents=True,exist_ok=True)
    chunks = [f for f in sorted(gen.glob('output_*.cpp')) if f.name != 'output_74.cpp']
    found = {}      # name -> (chunk name, prelude lines, function text)
    changed_chunks = {}
    all_defs = 0
    for f in chunks:
        s = f.read_text(errors='surrogateescape')
        first = s.find(NL + 'void func_')
        prelude = s[:first].split(NL) if first > 0 else []
        out = s
        # file-local statics defined between the functions (e.g. loop_XXXXXXXX helpers): a function that uses one has to stay in its chunk
        local_names = set(re.findall(r'^static\b[^\n]*?\b(\w+)\s*[(=\[{;]', s, re.M)) - {'inline', 'const', 'constexpr', 'void', 'bool', 'int'}
        local_pat = re.compile(r'\b(?:' + '|'.join(sorted(map(re.escape, local_names))) + r')\b') if local_names else None
        for name in re.findall(r'^void (func_[0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{$', s, re.M):
            all_defs += 1
            if name not in rank:
                continue
            span = function_span(out, name)
            assert span, name
            a, b = span
            if local_pat and local_pat.search(out[a:b]):
                continue
            if not re.match(r'\n*(?:void func_|\Z)', out[b:]):
                continue        # something other than another function follows: leave it in its chunk
            found[name] = (f.name, prelude, out[a:b])
            out = out[:a] + out[b:]
        if out != s:
            changed_chunks[f.name] = out
    for name, text in changed_chunks.items():
        write_generated(split / name,text)
    stats = dict(hot=len(found), promoted=0, promoted_fp=0, fallback=0, skipped={})
    ordered = sorted(found, key=lambda n: rank[n])
    # group into units of ~30k lines
    units = []
    cur = []
    cur_lines = 0
    typed_used = {}
    for n in ordered:
        body = found[n][2]
        if do_promote:
            new, st = effect_promoter.promote(body, True, do_fp, nest) if effect_promoter else P.promote(body, True, do_fp, nest)
            if effect_promoter and (new is None or not st.get('effect_calls')):
                new, st = P.promote(body, True, do_fp, nest)
            if new is not None:
                for key in ('effect_calls','effect_spill_removed','effect_reload_removed'):
                    stats[key] = stats.get(key,0) + st.get(key,0)
            if entry_specializer and n[5:] not in entry_excluded:
                specialized,special_stats=entry_specializer(body,nest,typed_leaves=typed_leaves)
                if specialized is not None and special_stats.get('entry_specialized'):
                    new,st=specialized,special_stats
                    typed_used[n]=st.get('typed_leaf_ids',[])
                    stats['typed_leaf_sites']=stats.get('typed_leaf_sites',0)+st.get('typed_leaf_sites',0)
                    for key in ('entry_specialized','fpu_checks_removed','static_sync_bits_removed','fast_continuations','loop_fastpaths','linked_roots','linked_sites'):
                        stats[key]=stats.get(key,0)+int(st.get(key,0))
            if new is not None:
                stats['nested'] = stats.get('nested', 0) + st.get('nested', 0)
            if new is None:
                stats['fallback'] += 1
                stats['skipped'][st] = stats['skipped'].get(st, 0) + 1
            else:
                body = new
                if always_prune is not None:
                    try:
                        reduced, removed = always_prune(body)
                        if removed:
                            body = reduced
                            stats['always_fpu_pruned'] = stats.get('always_fpu_pruned', 0) + removed
                    except Exception:
                        pass
                stats['promoted'] += 1
                if re.search(r'^    double ', new, re.M):
                    stats['promoted_fp'] += 1
        found[n] = found[n][:2] + (body,)
        n_lines = body.count(NL)
        if cur and cur_lines + n_lines > 30000:
            units.append(cur)
            cur = []
            cur_lines = 0
        cur.append(n)
        cur_lines += n_lines
    if cur:
        units.append(cur)
    hot_files = []
    for k, names in enumerate(units):
        prelude0 = found[names[0]][1]
        headers = []
        for n in names:
            for l in found[n][1]:
                if HEADER_LINE.match(l) and l not in headers:
                    headers.append(l)
        lines = [l for l in prelude0 if not HEADER_LINE.match(l)] + headers
        body_text = ''.join(found[n][2] for n in names)
        # A hot body that uses a header must include it even when its first function's chunk does not (release-independent).
        if 'native_frame_limit::' in body_text and '#include "runtime/native_frame_limit.h"' not in lines: lines.append('#include "runtime/native_frame_limit.h"')
        if effect_promoter: lines.append('#include "runtime/native_callee_effects.h"')
        if entry_specializer:
            lines.append('#include "runtime/native_guest_entry.h"')
            lines.append('#include "runtime/native_loop_span.h"')
            lines.append('#include "runtime/native_linked_region.h"')
        helpers=NL.join(typed_leaves[address].code for address in sorted({address for n in names for address in typed_used.get(n,[])}))
        text = NL.join(lines) + NL + helpers + NL + (NL + NL).join(found[n][2] for n in names) + NL
        hf = hot_dir / ('hot_%03d.cpp' % k)
        write_generated(hf,text)
        hot_files.append(hf)
    import json
    (p/'reports/hot-effect-coverage.json').write_text(json.dumps(stats,indent=2)+'\n')
    # no function may be lost or duplicated
    defs = 0
    for f in list(hot_files) + [split / n for n in changed_chunks] + [c for c in chunks if c.name not in changed_chunks]:
        defs += len(re.findall(r'^void func_[0-9A-F]{8}\(NWII_CTX_PARAM ctx\) \{$', f.read_text(errors='surrogateescape'), re.M))
    assert defs == all_defs, (defs, all_defs)
    # CMake: swap the changed chunks, add the hot units
    for name in changed_chunks:
        t = t.replace('/build/crash-generated/' + name + '"', '/build/crash-split/' + name + '"')
    block = NL + MARK + NL + 'target_include_directories(CrashNative PRIVATE "' + gen.as_posix() + '")' + NL + 'target_sources(CrashNative PRIVATE' + NL + ''.join('    "' + f.as_posix() + '"' + NL for f in hot_files) + ')' + NL
    block += 'set_source_files_properties(' + NL + ''.join('    "' + f.as_posix() + '"' + NL for f in hot_files) + '    PROPERTIES COMPILE_OPTIONS "-' + opt + ';' + fma + '-march=x86-64-v3;-fwrapv;-fno-strict-aliasing;-ffp-contract=off")' + NL
    if CHUNK_MARK in t:
        a = t.index(CHUNK_MARK)
        t = t[:a].rstrip(NL) + NL + block + NL + t[a:]
    else:
        t = t.rstrip(NL) + NL + block
    write_generated(cm,t)
    # These names are owned outputs of this stage; leave all other files alone.
    for old in hot_dir.glob('hot_*.cpp'):
        if old not in hot_files:old.unlink()
    for old in split.glob('output_*.cpp'):
        if old.name not in changed_chunks:old.unlink()
    print('Hot split: %d hot functions in %d units (%d promoted, %d with FP, %d unpromoted: %s); %d indirect calls made inline; %d chunks rewritten; hot units at -%s'
          % (stats['hot'], len(units), stats['promoted'], stats['promoted_fp'], stats['fallback'], stats['skipped'], stats.get('nested', 0), len(changed_chunks), opt))


if __name__ == '__main__':
    main()

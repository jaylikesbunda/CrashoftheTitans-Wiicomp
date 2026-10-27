"""NATIVE_INDIRECT_SITE_CACHE_V1 / NATIVE_RETURN_SITE_CACHE_V1: per-site caches for the two ways
translated code re-enters the dispatcher without a direct call.

INDIRECT CALLS.  Nested bctrl sites are already made inline (tools/promote_regs.py nest_indirect,
also driven by tools/guest_entry_specialize.py).  Each promoted site reads

    ctx.lr = RET;
    if (ctx.ctr) { ctx.pc = ctx.ctr; nwii::runtime::native_call_indirect(ctx);
                   if (ctx.pc != RET) return; process_pending_callbacks(ctx);
                   if (ctx.pc != RET) return; } else nwii::runtime::note_null_call(...);

native_call_indirect is one run_game iteration for the call target: process_pending_callbacks, the
C0/D0 -> 80/90 alias normalisation, ++inst_count, one range-guarded native_wpad_dispatch hook,
find_dispatch_target, the call, and the HLE `pc = lr` fixup.  find_dispatch_target is a pure lookup
over the immutable function-bound / override tables, so the resolved (func, hle) belongs to the
target alone.  We rewrite each site to

    nwii::runtime::native_call_indirect_cached(ctx, SITE_INDEX);

with one static slot per call site defined at the end of main_output.cpp.  A hit calls the cached
func directly (identical pc / lr / hle handling, callbacks and inst_count); a miss runs the identical
resolution and refills the slot.  interpret_step results (func == nullptr) are never cached.

RETURNS.  A blr returns to the dispatcher.  In promoted (hot) units the return is

    ctx.pc = ctx.lr; goto exit_N;      ...      exit_N: ; <spill> return;

exit_N is shared with the MSR.FP guard exits (`if (__builtin_expect(ctx.require_fpu(..),0)) goto
exit_N;`), which must keep returning to run_game unchanged, so the epilogue cannot be rewritten in
place.  Instead each blr site inlines its own copy of that epilogue's spill and then runs one
dispatcher iteration for the return target before returning to run_game:

    ctx.pc = ctx.lr; <spill of exit_N> nwii::runtime::native_return_cached(ctx); /* exit_N */ return;

The helper mirrors run_game's iteration for ctx.pc; pc==0 and pc==0xFFFFFFFC are left whole to
run_game (they need its idle sleep / backup restore), and process_pending_callbacks runs first as in
the loop, so the callbacks order, ++inst_count, native_wpad_dispatch and dispatch.hle are unchanged.
The cache is a direct-mapped target->func table keyed on ctx.lr, which is the return target of the
site.  A hit skips find_dispatch_target and calls the func; a miss resolves and refills.

Both transforms are idempotent (MARK-guarded; files are written only when the bytes change, so
preserved mtimes survive) and reversible with --restore.  NWII_BUILD_INDIRECT_CACHE=0 and
NWII_BUILD_RETURN_CACHE=0 disable them independently.  tools/check_indirect_call_cache.py and
tools/check_return_cache.py run the helpers differentially against the code they replace.
"""
from pathlib import Path
import argparse, os, re

INDIRECT_MARK = 'NATIVE_INDIRECT_SITE_CACHE_V1'
RETURN_MARK = 'NATIVE_RETURN_SITE_CACHE_V1'
IND_BEGIN = '// ' + INDIRECT_MARK + ' begin\n'
IND_END = '// ' + INDIRECT_MARK + ' end\n'
RET_BEGIN = '// ' + RETURN_MARK + ' begin\n'
RET_END = '// ' + RETURN_MARK + ' end\n'
IND_ENV = 'NWII_BUILD_INDIRECT_CACHE'
RET_ENV = 'NWII_BUILD_RETURN_CACHE'
IND_DECL = ('namespace nwii::runtime { void native_call_indirect_cached(CPUContext& ctx, uint32_t site_index); } // '
            + INDIRECT_MARK + '\n')
RET_DECL = ('namespace nwii::runtime { void native_return_cached(CPUContext& ctx); } // ' + RETURN_MARK + '\n')
ANCHOR = '#include "runtime/cpu_context.h"\n'
WRAPPER = '// --- Entry Point Wrapper ---\n'
TREES = ('crash-hot', 'crash-split', 'crash-generated')

# if (ctx.ctr) { ctx.pc = ctx.ctr; <call>; if (ctx.pc != RET) return; process_pending_callbacks(ctx); if (ctx.pc != RET) return; }
SITE = re.compile(
    r'(if \(ctx\.ctr\) \{ ctx\.pc = ctx\.ctr; )'
    r'(nwii::runtime::native_call_indirect(?:_cached)?\(ctx(?:, \d+)?\);)'
    r'( if \(ctx\.pc != (0x[0-9A-F]{8})\) return; process_pending_callbacks\(ctx\);'
    r' if \(ctx\.pc != \4\) return; \})')
IND_CACHED = re.compile(r'nwii::runtime::native_call_indirect_cached\(ctx, (\d+)\);')
IND_BARE = 'nwii::runtime::native_call_indirect(ctx);'
IND_DIRECT = re.compile(r'(if \(ctx\.ctr\) \{ ctx\.pc = ctx\.ctr; )'
                        r'nwii::runtime::native_call_indirect_cached\(ctx, (\d+)\);')

# promoted blr return site and the epilogue it jumps to
RET_GOTO = re.compile(r'(ctx\.pc = ctx\.lr; )goto (exit_\d+);')
RET_GOTO_UNDO = re.compile(r'(ctx\.pc = ctx\.lr; ).*?nwii::runtime::native_return_cached\(ctx\); /\* '
                           + RETURN_MARK + r' (exit_\d+) \*/ return;')
RET_RAW = re.compile(r'(ctx\.pc = ctx\.lr; )return;')
RET_RAW_UNDO = re.compile(r'(ctx\.pc = ctx\.lr; )nwii::runtime::native_return_cached\(ctx\); /\* '
                          + RETURN_MARK + r' \*/ return;')


def read(path):
    return path.read_text(errors='surrogateescape')


def write(path, text):
    if not path.exists() or read(path) != text:
        path.write_text(text, errors='surrogateescape')


def chunk_files(port):
    out = []
    for tree in TREES:
        base = port / 'build' / tree
        if base.is_dir():
            out += sorted(base.glob('*.cpp'))
    return out


def hot_files(port):
    base = port / 'build' / 'crash-hot'
    return sorted(base.glob('*.cpp')) if base.is_dir() else []


def indirect_block(count):
    return IND_BEGIN + '''namespace nwii::runtime {
struct NativeIndirectSite {
    uint32_t pc = 0;
    void (*func)(CPUContext&) = nullptr;
    bool hle = false;
};
// One direct slot per inline call site, indexed by the literal the call site passes.
static NativeIndirectSite g_indirect_sites[%d];
void native_call_indirect_cached(CPUContext& ctx, uint32_t site_index) {
    static const bool scripted_wpad = std::getenv("NWII_WPAD_SCRIPT") || std::getenv("NWII_WPAD_LIVE");
    if (!ctx.is_running || ctx.pc == 0) return;
    process_pending_callbacks(ctx);
    if (ctx.pc == 0 || ctx.pc == 0xFFFFFFFC) return;
    if ((ctx.pc & 0xF0000000) == 0xC0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x80000000;
    else if ((ctx.pc & 0xF0000000) == 0xD0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x90000000;
    else if (ctx.pc < 0x80000000) ctx.pc |= 0x80000000;
    const uint32_t target = ctx.pc;
    ++ctx.inst_count;
    if ((target-0x803EC364u<=0x803EEF0Cu-0x803EC364u || scripted_wpad) && native_wpad_dispatch(ctx,target)) return;
    NativeIndirectSite& site = g_indirect_sites[site_index];
    if (site.func && site.pc == target) {
        ctx.pc = target;
        site.func(ctx);
        if (site.hle) ctx.pc = ctx.lr;
        return;
    }
    const auto dispatch = find_dispatch_target(target);
    if (dispatch.func) {
        site.pc = target; site.func = dispatch.func; site.hle = dispatch.hle;
        ctx.pc = target;
        dispatch.func(ctx);
        if (dispatch.hle) ctx.pc = ctx.lr;
    } else {
        interpret_step(ctx);
    }
}
}
''' % count + IND_END


def return_block():
    return RET_BEGIN + '''namespace nwii::runtime {
struct NativeReturnSite {
    uint32_t pc = 0;
    void (*func)(CPUContext&) = nullptr;
    bool hle = false;
};
// Return-target cache keyed on the target itself (an address is unique to one dispatch decision).
static NativeReturnSite g_return_sites[8192];
void native_return_cached(CPUContext& ctx) {
    static const bool scripted_wpad = std::getenv("NWII_WPAD_SCRIPT") || std::getenv("NWII_WPAD_LIVE");
    // pc==0 (idle sleep) and pc==0xFFFFFFFC (callback-return restore) need run_game's loop body,
    // so leave them whole: run_game performs its one callbacks call on the next iteration.
    if (!ctx.is_running) return;
    if (ctx.pc == 0 || ctx.pc == 0xFFFFFFFC) return;
    process_pending_callbacks(ctx);
    if (ctx.pc == 0 || ctx.pc == 0xFFFFFFFC) return;
    if ((ctx.pc & 0xF0000000) == 0xC0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x80000000;
    else if ((ctx.pc & 0xF0000000) == 0xD0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x90000000;
    else if (ctx.pc < 0x80000000) ctx.pc |= 0x80000000;
    const uint32_t target = ctx.pc;
    ++ctx.inst_count;
    if ((target-0x803EC364u<=0x803EEF0Cu-0x803EC364u || scripted_wpad) && native_wpad_dispatch(ctx,target)) return;
    NativeReturnSite& site = g_return_sites[(target>>2) & 8191u];
    if (site.func && site.pc == target) {
        ctx.pc = target;
        site.func(ctx);
        if (site.hle) ctx.pc = ctx.lr;
        return;
    }
    const auto dispatch = find_dispatch_target(target);
    if (dispatch.func) {
        site.pc = target; site.func = dispatch.func; site.hle = dispatch.hle;
        ctx.pc = target;
        dispatch.func(ctx);
        if (dispatch.hle) ctx.pc = ctx.lr;
    } else {
        interpret_step(ctx);
    }
}
}
''' + RET_END


def _collect_indirect(port):
    found = []
    for path in chunk_files(port):
        source = read(path)
        for m in SITE.finditer(source):
            found.append((path, m))
    return found


def patch_indirect(port):
    found = _collect_indirect(port)
    total = 0
    for path in chunk_files(port):
        total += read(path).count('nwii::runtime::native_call_indirect')
    if total != len(found):
        raise SystemExit('unrecognised native_call_indirect occurrence(s): %d sites, %d references' % (len(found), total))
    per_file = {}
    for index, (path, m) in enumerate(found):
        per_file.setdefault(path, []).append((m.span(2), index))
    changed = 0
    for path, sites in per_file.items():
        source = read(path)
        for (a, b), index in sorted(sites, reverse=True):
            source = source[:a] + 'nwii::runtime::native_call_indirect_cached(ctx, %d);' % index + source[b:]
        source = _add_decl(source, IND_DECL, INDIRECT_MARK, path)
        write(path, source)
        changed += 1
    return len(found), changed


def _spill_of_exit(source):
    """exit label -> the spill statements written just before its `return;` (single line)."""
    out = {}
    for m in re.finditer(r'^exit_(\d+): ;\n(.*?)return;', source, re.M):
        out['exit_' + m.group(1)] = m.group(2).strip()
    return out


def patch_returns(port):
    changed = 0
    sites = 0
    for path in hot_files(port):
        source = read(path)

        def handle(block):
            spills = _spill_of_exit(block)

            def site_repl(m):
                if m.group(2) not in spills:
                    raise SystemExit('%s: blr site without epilogue %s' % (path, m.group(2)))
                spill = spills[m.group(2)]
                body = (spill + ' ' if spill else '') + \
                    'nwii::runtime::native_return_cached(ctx); /* ' + RETURN_MARK + ' ' + m.group(2) + ' */ return;'
                return m.group(1) + body

            new, _ = RET_GOTO.subn(site_repl, block)
            # Raw (unpromoted) blr returns in the same unit.
            new, _ = RET_RAW.subn(r'\1nwii::runtime::native_return_cached(ctx); /* ' + RETURN_MARK + r' */ return;', new)
            return new

        # Exit labels are per function, so transform one top-level definition at a time.
        pieces = []
        last = 0
        for m in re.finditer(r'(?m)^\}$\n', source):
            pieces.append(handle(source[last:m.end()]))
            last = m.end()
        pieces.append(source[last:])
        new = ''.join(pieces)
        sites += new.count('nwii::runtime::native_return_cached(ctx);')
        if new != source:
            new = _add_decl(new, RET_DECL, RETURN_MARK, path)
            write(path, new)
            changed += 1
    return sites, changed


def _add_decl(source, decl, mark, path):
    if decl in source:
        return source
    if source.count(ANCHOR) != 1:
        raise SystemExit('%s: expected exactly one cpu_context.h include' % path)
    return source.replace(ANCHOR, ANCHOR + decl, 1)


def _strip_main_block(source, begin, end):
    if begin in source:
        a = source.index(begin)
        b = source.index(end) + len(end)
        return source[:a] + source[b:]
    return source


def patch_main(port, indirect_count, return_sites):
    path = port / 'build' / 'crash-generated' / 'main_output.cpp'
    if not path.exists():
        print('%s/%s: no generated entry point to patch' % (INDIRECT_MARK, RETURN_MARK))
        return
    source = read(path)
    source = _strip_main_block(source, IND_BEGIN, IND_END)
    source = _strip_main_block(source, RET_BEGIN, RET_END)
    source = source.rstrip('\n') + '\n'
    block = ''
    if indirect_count:
        block += indirect_block(indirect_count) + '\n'
    if return_sites:
        block += return_block()
    if block:
        # Keep host-only helpers out of the resolver-only fixtures (dense/switch extract up to the
        # entry-point anchor); append after run_game instead.
        assert WRAPPER in source, 'entry-point anchor missing'
        source = source + '\n' + block
    write(path, source)


def restore(port):
    main = port / 'build' / 'crash-generated' / 'main_output.cpp'
    if not main.exists():
        # A fresh storage stages into an empty tree; the restore phase runs before the
        # generator produces the chunks, so there is nothing to restore yet.
        print('%s/%s: no generated tree to restore' % (INDIRECT_MARK, RETURN_MARK))
        return
    source = read(main)
    source = _strip_main_block(source, IND_BEGIN, IND_END)
    source = _strip_main_block(source, RET_BEGIN, RET_END)
    write(main, source.rstrip('\n') + '\n')
    reverted = 0
    for path in chunk_files(port):
        source = read(path)
        new = IND_CACHED.sub(IND_BARE, source).replace(IND_DECL, '').replace(RET_DECL, '')
        new = RET_GOTO_UNDO.sub(r'\1goto \2;', new)
        new = RET_RAW_UNDO.sub(r'\1return;', new)
        if new != source:
            write(path, new)
            reverted += 1
    print('%s/%s restored in %d translation unit(s)' % (INDIRECT_MARK, RETURN_MARK, reverted))


def apply(port):
    indirect = 0
    if os.environ.get(IND_ENV, '1') != '0':
        indirect, _ = patch_indirect(port)
    else:
        print('%s disabled by %s=0' % (INDIRECT_MARK, IND_ENV))
    # NATIVE_RETURN_SITE_CACHE_V1 defaults OFF: it is unsound, not merely slow.
    # The helper dispatches the return target *inline in the returning function's frame*
    # (site.func(ctx)), so a guest loop that contains a call never unwinds the C stack:
    # A -> B -> A -> B ... adds two C frames per iteration. The indirect (call) cache does
    # not have this property because its callee still returns to run_game, so only the
    # return half is disabled here.
    # run_game installs NATIVE_DISPATCH_RECOVERY_SCOPE_V1 (_setjmp -> loop head), so the
    # overflow is not a crash: it longjmps back and retries the same work, producing an
    # unbounded retry loop -- a tight loop calling one guest function with zero frame
    # progress, which is exactly the "Licensed by Nintendo" hang observed in 2026.10.14-1.
    # Re-enable with NWII_BUILD_RETURN_CACHE=1 only after the stack depth is bounded
    # (e.g. a depth counter that forces a real return to run_game every N frames).
    returns = 0
    if os.environ.get(RET_ENV, '0') != '0':
        returns, _ = patch_returns(port)
    else:
        print('%s disabled (default; see apply()); set %s=1 to opt in' % (RETURN_MARK, RET_ENV))
    patch_main(port, indirect, returns)
    print('%s: %d indirect site(s); %s: %d return site(s)' % (INDIRECT_MARK, indirect, RETURN_MARK, returns))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--restore', action='store_true')
    args = ap.parse_args()
    port = Path(__file__).resolve().parents[1]
    (restore if args.restore else apply)(port)

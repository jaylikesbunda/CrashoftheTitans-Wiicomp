"""nwii::runtime::native_call_indirect: one dispatcher iteration, callable from inside a translated function.

An indirect call (bctrl) used to leave the translated function (`ctx.pc = ctx.ctr; return;`); run_game's loop dispatched the target, the
callee returned to the loop, and the loop dispatched the return address, i.e. the caller was entered again through its entry switch (a
callback poll, two table lookups, a 64-bit modulo and, in promoted code, a reload of every live register per re-entry).
tools/promote_regs.py can now emit the call inline: `ctx.pc = ctx.ctr; native_call_indirect(ctx); if (ctx.pc != RET) return; ...`, the same
protocol the direct calls already use. This function is one iteration of the run_game loop body, verbatim:
  * pc == 0, the 0xFFFFFFFC callback-return marker and a stopped context are left to the real loop (the caller sees pc != RET and returns);
  * process_pending_callbacks runs first, as in the loop, and a callback it starts is dispatched here exactly like the loop would;
  * the C0/D0 -> 80/90 address mapping, ++inst_count, one range-guarded native_wpad_dispatch hook, the dispatch table and HLE handling are unchanged.
Instruction accounting matches the old two iterations: this call counts the target dispatch, the fall-through `++ctx.inst_count` in front of the
return label counts the re-entry the loop used to count.
Header declaration is added next to process_pending_callbacks; the definition goes in main_output.cpp (it needs the static dispatch tables).
Idempotent.
"""
from pathlib import Path
p = Path(__file__).resolve().parents[1]
H = p / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
M = p / 'build/crash-generated/main_output.cpp'
MARK = 'NATIVE_CALL_INDIRECT_V1'
h = H.read_text()
if MARK not in h:
    anchor = 'bool process_pending_callbacks(CPUContext &ctx);\n'
    assert h.count(anchor) == 1
    h = h.replace(anchor, anchor + 'void native_call_indirect(CPUContext &ctx); // ' + MARK + '\n')
    H.write_text(h)
m = M.read_text(errors='surrogateescape')
if MARK not in m:
    anchor = '// --- Entry Point Wrapper ---\n'
    assert m.count(anchor) == 1
    block = '''// NATIVE_CALL_INDIRECT_V1: one run_game iteration for an indirect call made from inside a translated function.
namespace nwii::runtime {
bool native_wpad_dispatch(CPUContext&, uint32_t);
void native_call_indirect(CPUContext& ctx) {
    if (!ctx.is_running || ctx.pc == 0) return;
    process_pending_callbacks(ctx);
    if (ctx.pc == 0 || ctx.pc == 0xFFFFFFFC) return;
    if ((ctx.pc & 0xF0000000) == 0xC0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x80000000;
    else if ((ctx.pc & 0xF0000000) == 0xD0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x90000000;
    else if (ctx.pc < 0x80000000) ctx.pc |= 0x80000000;
    const uint32_t target = ctx.pc;
    ++ctx.inst_count;
    if (target-0x803EC364u<=0x803EEF0Cu-0x803EC364u && native_wpad_dispatch(ctx,target)) return;
    const auto dispatch = find_dispatch_target(target);
    if (dispatch.func) {
        ctx.pc = target;
        dispatch.func(ctx);
        if (dispatch.hle) ctx.pc = ctx.lr;
    } else {
        interpret_step(ctx);
    }
}
}
'''
    m = m.replace(anchor, block + anchor)
    M.write_text(m, errors='surrogateescape')
print('native_call_indirect installed')

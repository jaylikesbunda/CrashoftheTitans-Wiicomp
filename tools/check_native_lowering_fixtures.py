"""Reproducer fixtures for the guest-register effect analysis.

Each fixture is a minimal piece of generated guest code that reproduces one
of the cases the analysis used to miss. The checker asserts that the analysis
now reports the observation; a fixture that stops failing means the analysis
regressed.

Run:  python tools/check_native_lowering_fixtures.py
"""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from native_lowering_effects import (blocks_of, block_successors, function_clobbered,
                                     function_read_before_write, statement_calls,
                                     statement_effects, statement_is_opaque_call)

# The register set a candidate is qualified against: the PowerPC EABI volatile
# registers, which the analysis must still be able to observe.
VOLATILE = set('gpr%s' % i for i in range(14))
VOLATILE |= set('fpr%s' % i for i in range(14))
VOLATILE |= set('cr%s' % i for i in range(8))

# --- fixture 1: a conditional branch has a fallthrough successor ------------
FIXTURE_FALLTHROUGH = """
NWII_GUEST_ABI void func_C1(NWII_CTX_PARAM ctx) {
    ctx.pc = 0xAAAA0000; func_AAAA0000(ctx);
loc_C1_1: ;
    if (ctx.cr[0].eq) goto loc_C1_2;
loc_C1_3: ;
    ctx.gpr[0] = ctx.gpr[5];
    ctx.pc = ctx.lr; return;
loc_C1_2: ;
    ctx.gpr[0] = ctx.gpr[6];
    ctx.pc = ctx.lr; return;
}
"""

# --- fixture 2: an assignment reads the register it writes -------------------
FIXTURE_RHS_READ = """
NWII_GUEST_ABI void func_C2(NWII_CTX_PARAM ctx) {
    ctx.pc = 0xAAAA0000; func_AAAA0000(ctx);
loc_C2_1: ;
    ctx.gpr[3] = ctx.gpr[3] + 1;
    ctx.pc = ctx.lr; return;
}
"""

# --- fixture 3: a read in the same block, after the call --------------------
FIXTURE_SAME_BLOCK = """
NWII_GUEST_ABI void func_C3(NWII_CTX_PARAM ctx) {
    ctx.pc = 0xAAAA0000; func_AAAA0000(ctx);
    ctx.gpr[0] = ctx.gpr[5];
    ctx.pc = ctx.lr; return;
}
"""

# --- fixture 4: a later call that receives the context ----------------------
FIXTURE_OPAQUE_CALL = """
NWII_GUEST_ABI void func_C4(NWII_CTX_PARAM ctx) {
    ctx.pc = 0xAAAA0000; func_AAAA0000(ctx);
    func_BBBB0000(ctx);
    ctx.pc = ctx.lr; return;
}
"""

# The callee of fixture 4: it reads gpr[5] before writing it.
FIXTURE_OPAQUE_CALLEE = """
NWII_GUEST_ABI void func_BBBB0000(NWII_CTX_PARAM ctx) {
loc_B1: ;
    ctx.gpr[4] = ctx.gpr[5];
    ctx.pc = ctx.lr; return;
}
"""

# --- fixture 5: the clobbered set of a function -----------------------------
FIXTURE_CLOBBERED = """
NWII_GUEST_ABI void func_C5(NWII_CTX_PARAM ctx) {
    uint32_t ea = ctx.gpr[1] + -16;
    ctx.mmu.write32(ea, ctx.gpr[1]);
    ctx.gpr[1] = ea;
    ctx.mmu.write32(ctx.gpr[1] + 8, ctx.gpr[28]);
    ctx.gpr[30] = 1;
    ctx.gpr[28] = ctx.mmu.read32(ctx.mmu, ctx.gpr[1] + 8);
    ctx.gpr[1] = ctx.gpr[1] + 16;
    ctx.pc = ctx.lr; return;
}
"""


def observed_after_call(text, address, callee_reads=None):
    """The registers a caller can observe after its call to `address`."""
    callee_reads = callee_reads or {}
    blocks = blocks_of(text)
    labels = [label for label, _ in blocks]
    known = {label: i for i, label in enumerate(labels)}
    found = set()
    for position, (label, statements) in enumerate(blocks):
        for index, statement in enumerate(statements):
            if not re.search(r'\bfunc_%s\(ctx\)' % address, statement):
                continue
            alive = set(VOLATILE)
            # The rest of this block, after the call.
            for rest in statements[index + 1:]:
                written, read = statement_effects(rest)
                found |= (read & alive)
                alive |= written
                alive -= read
                for callee in statement_calls(rest):
                    found |= (callee_reads.get(callee, set()) & alive)
                if statement_is_opaque_call(rest):
                    found |= alive
            # The blocks that follow.
            for target in block_successors((label, statements), position, labels, known):
                stack = [(target, set(alive))]
                while stack:
                    current, live = stack.pop()
                    bi = known[current]
                    blabel, blines = blocks[bi]
                    for rest in blines:
                        written, read = statement_effects(rest)
                        found |= (read & live)
                        live |= written
                        live -= read
                        for callee in statement_calls(rest):
                            found |= (callee_reads.get(callee, set()) & live)
                        if statement_is_opaque_call(rest):
                            found |= live
                    for nxt in block_successors(blocks[bi], bi, labels, known):
                        stack.append((nxt, set(live)))
    return found


def check():
    failures = []

    def want(name, condition, detail):
        if not condition:
            failures.append('%s: %s' % (name, detail))

    callee_reads = {'BBBB0000': function_read_before_write(FIXTURE_OPAQUE_CALLEE)}

    found = observed_after_call(FIXTURE_FALLTHROUGH, 'AAAA0000')
    want('fallthrough', 'gpr5' in found,
         'the fallthrough of a conditional branch was not followed (found %s)' % sorted(found))

    found = observed_after_call(FIXTURE_RHS_READ, 'AAAA0000')
    want('rhs-read', 'gpr3' in found,
         'the right hand side read of an assigned register was missed (found %s)' % sorted(found))

    found = observed_after_call(FIXTURE_SAME_BLOCK, 'AAAA0000')
    want('same-block', 'gpr5' in found,
         'a read after the call in the same block was missed (found %s)' % sorted(found))

    found = observed_after_call(FIXTURE_OPAQUE_CALL, 'AAAA0000', callee_reads)
    want('opaque-call', 'gpr5' in found,
         "a later context call did not expose the callee's reads (found %s)" % sorted(found))

    clobbered = function_clobbered(FIXTURE_CLOBBERED)
    want('clobbered', 'gpr30' in clobbered,
         'gpr30 is written and not restored, so it must be clobbered')
    want('clobbered', 'gpr28' not in clobbered,
         'gpr28 is saved and reloaded, so it must not be clobbered')
    want('clobbered', 'gpr1' not in clobbered,
         'gpr1 is restored, so it must not be clobbered')

    for failure in failures:
        print('FIXTURE FAIL ' + failure)
    print('NATIVE_LOWERING_FIXTURES %d/%d' % (6 - len(failures), 6))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(check())

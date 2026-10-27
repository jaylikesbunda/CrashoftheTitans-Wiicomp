"""Local checks for the CFG wide fact propagation of ``guest_fact_flow``.

Nothing is written into the repository: every compilation artefact lives in
the work directory that is passed on the command line.  No game is launched
and no production input is modified.

The checks cover the hand built fixtures (join disagreement, loop fixed
point, backedge invalidation, calls / polls / returns, GQR writes through
ctx, address base changes, canonical F32 copies versus arithmetic, interior
entries and the exact fallback preservation), a differential execution of the
transformed loop against the original one, the generated host code of the
same loop and the static coverage of the real production source.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import guest_fact_flow as F  # noqa: E402

RESULTS = []


def expect(name, ok, detail=''):
    ok = bool(ok)
    RESULTS.append((name, ok, detail))
    print(('PASS ' if ok else 'FAIL ') + name + ((' :: ' + detail) if detail else ''),
          flush=True)
    return ok


def fixture(name, body, head=None):
    head = head or ('void %s(NWII_CTX_PARAM ctx) {' % name)
    return head + NL + body + NL + '}'


NL = '\n'


def parse_one(text):
    parsed = F.parse_source(text)
    assert parsed, 'fixture does not parse'
    return parsed[0][2]


def flow_of(text):
    return F.FactFlow(parse_one(text)).analyse()


def block_of(flow, label):
    for b in flow.fn.blocks:
        if b.unit.label == label:
            return b
    raise AssertionError('no block for ' + label)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

JOIN_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    if (g0 == 0) goto loc_80010010;
    ctx.gqr[0] = 0x20;
    goto loc_80010020;
loc_80010010: ;
    ctx.gqr[0] = 0x31;
loc_80010020: ;
    return;
''')

AGREE_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    if (g0 == 0) goto loc_80010010;
    ctx.gqr[0] = 0x20;
    goto loc_80010020;
loc_80010010: ;
    ctx.gqr[0] = 0x30;
loc_80010020: ;
    return;
''')

LOOP_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    ctx.gqr[0] = 0;
loc_80010010: ;
    if ((ctx.gqr[0] & 0xF) == 0) goto loc_80010020;
    ctx.gqr[0] = 1;
    goto loc_80010010;
loc_80010020: ;
    return;
''')

LOOP_STABLE_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    ctx.gqr[0] = 0;
loc_80010010: ;
    if ((ctx.gqr[0] & 0xF) == 0) goto loc_80010020;
    goto loc_80010010;
loc_80010020: ;
    return;
''')

FP_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    f0 = ctx.mmu.read_f32(g1 + 0);
    f1 = f0;
    f2 = f0 + 1.0;
    f3 = f1;
loc_80010010: ;
    f4 = f0;
    return;
''')

FP_LOOP_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    f0 = ctx.mmu.read_f32(g1 + 0);
loc_80010010: ;
    f1 = f0;
    f0 = f0 + 1.0;
    goto loc_80010010;
''')

FP_BARRIER_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    f0 = ctx.mmu.read_f32(g1 + 0);
    func_80020000(ctx);
    f1 = f0;
    return;
''')

FP_POLL_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    f0 = ctx.mmu.read_f32(g1 + 0);
    if (nwii::runtime::native_poll_due()) {
    process_pending_callbacks(ctx);
    }
    f1 = f0;
    return;
''')

GQR_WRITE_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    ctx.gqr[1] = 0x1234;
    ctx.gqr[2] = g0;
    return;
''')

ADDR_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    g3 = g13 + 100;
    if (g0 == 0) goto loc_80010010;
    g3 = g13 + 200;
    goto loc_80010020;
loc_80010010: ;
    g4 = g13 + 300;
loc_80010020: ;
    g5 = g3 + 8;
    return;
''')

ADDR_BASE_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    g3 = g13 + 100;
    g5 = g3 + 8;
    g13 = g13 + 4;
    g7 = g5 + 16;
    return;
''')

ENTRY_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    f0 = ctx.mmu.read_f32(g1 + 0);
    goto loc_80010010;
loc_80010010: ;
    f1 = f0;
    return;
''', head=('void func_80010000(NWII_CTX_PARAM ctx) {\n'
           '    if (ctx.pc != 0x80010000) {\n'
           '        switch (ctx.pc) {\n'
           '            case 0x80010010: goto loc_80010010;\n'
           '            default: nwii::runtime::interpret_step(ctx); return;\n'
           '        }\n'
           '    }'))

GQR_LOOP_TEXT = fixture('func_80010000', '''
    uint32_t g1, g5, ctrv;
    uint64_t ic;
    double f0, p0;
loc_80010000: ;
    g1 = ctx.gpr[1];
    g5 = ctx.gpr[5];
    ctrv = ctx.ctr;
    if (ctrv == 0) goto loc_80010100;
loc_80010010: ;
    if((ctx.gqr[0]&0x70000u)==0){
    { auto pq_ = ctx.native_batch_psq_load_zero(g1 + 200, 0, 0); f0 = pq_.f; p0 = pq_.p; }
    }else{
    { auto pq_ = ctx.psq_load_pair(g1 + 200, 0, 0); f0 = pq_.f; p0 = pq_.p; }
    }
    ctx.mmu.write_f32(g5 + 0, (float)f0);
    ++ic;
    ctrv--;
    if (ctrv != 0) goto loc_80010010;
loc_80010100: ;
    return;
''')

GQR_LOOP_WRITE_TEXT = GQR_LOOP_TEXT.replace(
    '    ctrv--;\n', '    ctx.gqr[0] = ctx.gqr[0] + 1;\n    ctrv--;\n')

GQR_LOOP_CALL_TEXT = GQR_LOOP_TEXT.replace(
    '    ctrv--;\n', '    func_80020000(ctx);\n    ctrv--;\n')

GQR_LOOP_ENTRY_TEXT = GQR_LOOP_TEXT.replace(
    'loc_80010010: ;\n',
    'loc_80010010: ;\n    if (g0 == 0) goto loc_80010010;\n')


# ---------------------------------------------------------------------------
# fact checks
# ---------------------------------------------------------------------------

def check_join_disagreement():
    flow = flow_of(JOIN_TEXT)
    join = block_of(flow, 'loc_80010020')
    expect('join disagreement keeps no GQR fact',
           flow.gqr_test(join, 0, 0xF) == 'unknown',
           'test=%s' % flow.gqr_test(join, 0, 0xF))
    flow = flow_of(AGREE_TEXT)
    join = block_of(flow, 'loc_80010020')
    expect('join agreement keeps the GQR fact',
           flow.gqr_test(join, 0, 0xF) == 'true',
           'test=%s' % flow.gqr_test(join, 0, 0xF))
    expect('join agreement keeps the known bits',
           flow.gqr_state(join)[0].zero & 0xF == 0xF,
           'zero=%#x' % flow.gqr_state(join)[0].zero)
    expect('join agreement keeps no exact word',
           flow.gqr_state(join)[0].known_word() is None,
           'the two paths write different words, so no single word is known')


def check_loop_fixed_point():
    flow = flow_of(LOOP_TEXT)
    header = block_of(flow, 'loc_80010010')
    expect('optimistic first iteration is rejected',
           flow.gqr_test(header, 0, 0xF) == 'unknown',
           'test=%s' % flow.gqr_test(header, 0, 0xF))
    values = flow.gqr_state(header).get(0)
    expect('the fixed point contains the backedge value',
           values is not None and values.zero == 0xFFFFFFFE and values.one == 0,
           'zero=%#x one=%#x' % (values.zero, values.one))
    # the iteration has to reach a stable post fixpoint
    again = F.FactFlow(parse_one(LOOP_TEXT)).analyse()
    expect('the fixed point is stable',
           again.gqr_state(block_of(again, 'loc_80010010')).get(0) ==
           flow.gqr_state(header).get(0),
           'two independent runs agree')
    stable = flow_of(LOOP_STABLE_TEXT)
    stable_header = block_of(stable, 'loc_80010010')
    expect('a loop without a GQR write keeps the test proven',
           stable.gqr_test(stable_header, 0, 0xF) == 'true',
           'test=%s' % stable.gqr_test(stable_header, 0, 0xF))


def header_of(flow, label):
    return block_of(flow, label)


def check_backedge_invalidation():
    flow = flow_of(FP_LOOP_TEXT)
    header = block_of(flow, 'loc_80010010')
    expect('the backedge invalidates the canonical F32 fact',
           flow.fp_fact(header, 'f0') is None,
           'fact=%r' % (flow.fp_fact(header, 'f0'),))
    expect('the copy across the backedge is not canonical',
           flow.fp_fact(header, 'f1') is None,
           'fact=%r' % (flow.fp_fact(header, 'f1'),))
    flow = flow_of(FP_TEXT)
    head = block_of(flow, 'loc_80010000')
    expect('a direct read is a canonical F32',
           flow.fp_fact(head, 'f0') is not None and
           flow.fp_fact(head, 'f0').kind == 'f32' and
           flow.fp_fact(head, 'f0').direct,
           'fact=%r' % (flow.fp_fact(head, 'f0'),))
    expect('a copy keeps the canonical value',
           flow.fp_fact(head, 'f1') is not None and
           not flow.fp_fact(head, 'f1').direct and
           flow.fp_fact(head, 'f1').src == flow.fp_fact(head, 'f0').src,
           'fact=%r' % (flow.fp_fact(head, 'f1'),))
    expect('arithmetic destroys the canonical value',
           flow.fp_fact(head, 'f2') is None)
    expect('a copy of a copy keeps the canonical value',
           flow.fp_fact(head, 'f3') is not None and
           flow.fp_fact(head, 'f3').src == flow.fp_fact(head, 'f0').src)


def check_calls_polls_returns():
    flow = flow_of(FP_BARRIER_TEXT)
    head = block_of(flow, 'loc_80010000')
    expect('an unknown call destroys the canonical F32 fact',
           flow.fp_fact(head, 'f1') is None)
    flow = flow_of(FP_POLL_TEXT)
    head = block_of(flow, 'loc_80010000')
    expect('a poll destroys the canonical F32 fact',
           flow.fp_fact(head, 'f1') is None)
    flow = flow_of(GQR_WRITE_TEXT)
    head = block_of(flow, 'loc_80010000')
    expect('a literal GQR write is known exactly',
           flow.gqr_state(head)[1].known_word() == 0x1234)
    expect('a literal GQR write proves the format test false',
           flow.gqr_test(head, 1, 0xF) == 'false')
    expect('a GQR write of an unknown value proves nothing',
           flow.gqr_test(head, 2, 0xF) == 'unknown')
    # a return ends the path: nothing is claimed after it
    text = fixture('func_80010000', '''
loc_80010000: ;
    f0 = ctx.mmu.read_f32(g1 + 0);
    return;
loc_80010010: ;
    f1 = f0;
    return;
''')
    flow = flow_of(text)
    expect('a return is a barrier for the facts',
           flow.fp_fact(block_of(flow, 'loc_80010000'), 'f0') is not None)


def check_address_bases():
    flow = flow_of(ADDR_TEXT)
    join = block_of(flow, 'loc_80010020')
    expect('a base that changes on one path is not known at the join',
           flow.addr_base(join, 'g3') is None,
           'base=%r' % (flow.addr_base(join, 'g3'),))
    expect('a base that only one path defines is not known at the join',
           flow.addr_base(join, 'g4') is None,
           'base=%r' % (flow.addr_base(join, 'g4'),))
    expect('the derived base follows the opaque root',
           flow.addr_base(join, 'g5') == ('g3', 8),
           'base=%r' % (flow.addr_base(join, 'g5'),))
    flow = flow_of(ADDR_BASE_TEXT)
    head = block_of(flow, 'loc_80010000')
    states = flow.line_states(head)
    expect('an affine base is tracked',
           states[1][2].get('g3') == frozenset((('g13', 100),)),
           'state=%r' % (states[1][2].get('g3'),))
    expect('a composed base keeps the offset',
           states[2][2].get('g5') == frozenset((('g13', 108),)),
           'state=%r' % (states[2][2].get('g5'),))
    expect('writing the root invalidates the derived bases',
           states[3][2].get('g3') == frozenset() and
           states[3][2].get('g5') == frozenset(),
           'state=%r' % (states[3][2],))
    expect('the local written from itself becomes an opaque root',
           states[3][2].get('g13') == frozenset(),
           'state=%r' % (states[3][2].get('g13'),))
    expect('a base derived from an opaque root keeps that root',
           states[4][2].get('g7') == frozenset((('g5', 16),)),
           'state=%r' % (states[4][2].get('g7'),))


ADDR_LOOP_TEXT = fixture('func_80010000', '''
loc_80010000: ;
    g3 = g13 + 100;
    g5 = g3 + 8;
loc_80010010: ;
    ctx.mmu.write32(g5 + 0, g3);
    ctx.mmu.write32(g6 + 16, g3);
    g6 = g6 + 4;
    goto loc_80010010;
''')


def check_loop_bases():
    flow = flow_of(ADDR_LOOP_TEXT)
    expect('the loop is found', len(flow.loops) == 1, 'loops=%d' % len(flow.loops))
    bases = flow.loop_bases(flow.loops[0])
    expect('the base of the loop is reported',
           'g13' in bases, 'bases=%r' % (bases,))
    expect('the base of the loop is loop invariant',
           bases.get('g13', {}).get('invariant') is True,
           'bases=%r' % (bases,))
    expect('the constant offsets of the base are reported',
           bases.get('g13', {}).get('offsets') == [108],
           'bases=%r' % (bases,))
    expect('a base that the loop writes is not invariant',
           bases.get('g6', {}).get('invariant') is False,
           'bases=%r' % (bases,))


def check_interior_entries():
    flow = flow_of(ENTRY_TEXT)
    expect('the case target is a root',
           len(flow.fn.roots) == 2, 'roots=%d' % len(flow.fn.roots))
    target = block_of(flow, 'loc_80010010')
    expect('an interior entry carries no fact',
           flow.fp_fact(target, 'f0') is None,
           'fact=%r' % (flow.fp_fact(target, 'f0'),))
    expect('an interior entry carries no GQR fact',
           flow.gqr_test(target, 0, 0xF) == 'unknown')


def check_consumer():
    new, count, details = F.hoist_gqr_tests(GQR_LOOP_TEXT)
    expect('the eligible loop is hoisted', count == 1, 'count=%d' % count)
    expect('the original loop text is preserved',
           '    if((ctx.gqr[0]&0x70000u)==0){\n'
           '    { auto pq_ = ctx.native_batch_psq_load_zero(g1 + 200, 0, 0); f0 = pq_.f; p0 = pq_.p; }\n'
           '    }else{\n'
           '    { auto pq_ = ctx.psq_load_pair(g1 + 200, 0, 0); f0 = pq_.f; p0 = pq_.p; }\n'
           '    }\n' in new)
    expect('every original label survives',
           all(l in new for l in ('loc_80010000:', 'loc_80010010:', 'loc_80010100:')))
    expect('the fast path replaces the test by its true branch',
           'native_gqr_fast_loc_80010010: ;\n    {\n'
           '    { auto pq_ = ctx.native_batch_psq_load_zero(g1 + 200, 0, 0); f0 = pq_.f; p0 = pq_.p; }\n'
           '    }\n' in new)
    expect('the fast path has its own backedge',
           'if (ctrv != 0) goto native_gqr_fast_loc_80010010;' in new)
    expect('the original loop keeps its own backedge',
           'if (ctrv != 0) goto loc_80010010;' in new)
    expect('the test is evaluated once in the preheader',
           new.count('(ctx.gqr[0]&0x70000u)==0') == 2,
           'the hoisted guard and the original test')
    # the original fallback is reachable and unchanged
    expect('the original fallback keeps the slow path',
           '    }else{\n'
           '    { auto pq_ = ctx.psq_load_pair(g1 + 200, 0, 0); f0 = pq_.f; p0 = pq_.p; }\n'
           '    }\n' in new)
    # a write to the tested index declines the loop
    new2, count2, details2 = F.hoist_gqr_tests(GQR_LOOP_WRITE_TEXT)
    expect('a GQR write inside the loop declines the transform',
           count2 == 0 and any('may change' in d.get('declined', '') for d in details2),
           'details=%r' % details2)
    # an unknown call inside the loop declines the loop
    new3, count3, details3 = F.hoist_gqr_tests(GQR_LOOP_CALL_TEXT)
    expect('an unknown call inside the loop declines the transform',
           count3 == 0, 'count=%d' % count3)
    # the strict rule also declines a read only observer
    new4, count4, details4 = F.hoist_gqr_tests(GQR_LOOP_TEXT, strict_observers=True)
    expect('the strict rule declines a loop with a read only observer',
           count4 == 0 and any('observing' in d.get('declined', '') for d in details4),
           'details=%r' % details4)
    # an interior entry declines the loop
    new5, count5, details5 = F.hoist_gqr_tests(GQR_LOOP_ENTRY_TEXT)
    expect('an interior entry declines the transform',
           count5 == 0, 'count=%d details=%r' % (count5, details5))
    # a source without a format test is unchanged
    new6, count6, _ = F.hoist_gqr_tests(JOIN_TEXT)
    expect('a source without a format test is unchanged',
           count6 == 0 and new6 == JOIN_TEXT)


# ---------------------------------------------------------------------------
# differential execution and generated code
# ---------------------------------------------------------------------------

MOCK = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#define NWII_CTX_PARAM MockCtx&
struct MockPair { double f; double p; };
struct MockCtx;
struct GqrProxy {
    MockCtx* owner;
    uint32_t operator[](unsigned index) const;
};
struct MockMMU {
    float mem[4096];
    float read_f32(uint32_t a) { return mem[a & 0xFFF]; }
    void write_f32(uint32_t a, float v) { mem[a & 0xFFF] = v; }
};
struct MockCtx {
    uint32_t gpr[32];
    uint32_t gqr_value[8];
    GqrProxy gqr;
    uint32_t ctr;
    uint64_t inst_count;
    uint32_t pc, lr;
    MockMMU mmu;
    unsigned long long slow_calls;
    unsigned long long gqr_reads;
    MockCtx() : ctr(0), inst_count(0), pc(0), lr(0), slow_calls(0), gqr_reads(0) {
        std::memset(gpr, 0, sizeof(gpr));
        std::memset(gqr_value, 0, sizeof(gqr_value));
        std::memset(&mmu, 0, sizeof(mmu));
        gqr.owner = this;
    }
    MockPair psq_load_pair(uint32_t a, uint32_t w, uint32_t i) {
        ++slow_calls;
        uint32_t type = (gqr_value[i] >> 16) & 0x7;
        float v = mmu.read_f32(a);
        double scaled = (type == 0) ? (double)v : (double)v * 2.0;
        return MockPair{scaled, (double)v};
    }
    MockPair native_batch_psq_load_zero(uint32_t a, uint32_t w, uint32_t i) {
        float v = mmu.read_f32(a);
        return MockPair{(double)v, (double)v};
    }
};
inline uint32_t GqrProxy::operator[](unsigned index) const {
    ++owner->gqr_reads;
    return owner->gqr_value[index];
}
'''

DIFFERENTIAL = r'''
static void reset(MockCtx& c, unsigned trial, bool zero_type, unsigned ctr) {
    unsigned seed = trial * 2654435761u + 12345u;
    for (unsigned i = 0; i < 32; ++i) { seed = seed * 1664525u + 1013904223u; c.gpr[i] = seed; }
    for (unsigned i = 0; i < 8; ++i) { seed = seed * 1664525u + 1013904223u; c.gqr_value[i] = seed; }
    // finite values only: the payload of a NaN is not architecturally fixed
    // for the double to float conversion, which would make the comparison
    // depend on the code layout instead of on the guest semantics
    for (unsigned i = 0; i < 4096; ++i) {
        seed = seed * 1664525u + 1013904223u;
        c.mmu.mem[i] = (float)((int32_t)(seed % 0x10000u) - 0x8000) / 1024.0f;
    }
    if (zero_type) { for (unsigned i = 0; i < 8; ++i) c.gqr_value[i] &= ~0x70000u; }
    c.ctr = ctr;
    c.inst_count = trial;
    c.pc = 0x80010000;
    c.lr = 0x80020000;
    c.slow_calls = 0;
    c.gqr_reads = 0;
    c.gpr[1] = 0x80010200 & 0xFFF;
    c.gpr[5] = 0x80010400 & 0xFFF;
}
static bool same(const MockCtx& a, const MockCtx& b) {
    if (std::memcmp(a.gpr, b.gpr, sizeof(a.gpr)) != 0) return false;
    if (std::memcmp(a.gqr_value, b.gqr_value, sizeof(a.gqr_value)) != 0) return false;
    if (a.ctr != b.ctr || a.inst_count != b.inst_count) return false;
    if (std::memcmp(a.mmu.mem, b.mmu.mem, sizeof(a.mmu.mem)) != 0) return false;
    return true;
}
static int run(bool zero_type, unsigned ctr, const char* tag) {
    unsigned long long trials = 0, slow_old = 0, slow_new = 0, reads_old = 0, reads_new = 0;
    for (unsigned trial = 0; trial < 4000; ++trial) {
        MockCtx a, b;
        reset(a, trial, zero_type, ctr); reset(b, trial, zero_type, ctr);
        old_guest(a);
        new_guest(b);
        if (!same(a, b)) {
            std::printf("DIFFERENTIAL FAILURE %s trial=%u\n", tag, trial);
            return 1;
        }
        slow_old += a.slow_calls; slow_new += b.slow_calls;
        reads_old += a.gqr_reads; reads_new += b.gqr_reads;
        ++trials;
    }
    std::printf("differential %s: %llu trials, state identical; slow path calls old=%llu new=%llu; "
                "ctx.gqr reads old=%llu new=%llu\n",
                tag, trials, slow_old, slow_new, reads_old, reads_new);
    return 0;
}
int main() {
    if (run(false, 8, "random format")) return 1;
    if (run(true, 8, "type zero")) return 1;
    return 0;
}
'''


def production_flags():
    return ['-std=c++20', '-Os', '-march=x86-64-v3', '-mfma', '-fwrapv',
            '-fno-strict-aliasing', '-ffp-contract=off']


def winlibs():
    base = Path.home() / ('AppData/Local/Microsoft/WinGet/Packages/'
                          'BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/'
                          'mingw64/bin')
    if (base / 'g++.exe').is_file():
        return base
    return None


def count_code(objdump, obj):
    """Instruction count, stack references and code size of one object."""
    out = subprocess.run([str(objdump), '-d', str(obj)], capture_output=True, text=True)
    if out.returncode:
        return None
    instructions = 0
    stack_refs = 0
    labels = {}
    current = None
    body = {}
    for line in out.stdout.splitlines():
        m = re.match(r'^[0-9a-f]+ <([^>]+)>:$', line)
        if m:
            current = m.group(1)
            body[current] = []
            continue
        m = re.match(r'^\s+([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\t?(.*)$', line)
        if m and current is not None:
            body[current].append(m.group(3).strip())
    for name, lines in body.items():
        instructions += len(lines)
        for text in lines:
            if re.search(r'\b(?:rsp|rbp)\b', text):
                stack_refs += 1
    return {'functions': len(body), 'instructions': instructions, 'stack_refs': stack_refs,
            'per_function': {k: len(v) for k, v in body.items()}}


def differential_and_code(work, compiler, objdump):
    """Compile the original and the transformed loop, compare and count."""
    work.mkdir(parents=True, exist_ok=True)
    new, count, _ = F.hoist_gqr_tests(GQR_LOOP_TEXT)
    if count != 1:
        expect('the differential fixture is transformed', False, 'count=%d' % count)
        return {}
    # build a translation unit with both versions
    combined = MOCK + 'namespace oldv {\n' + \
        GQR_LOOP_TEXT.replace('func_80010000', 'old_guest') + '\n}\n' + \
        'namespace newv {\n' + \
        new.replace('func_80010000', 'new_guest') + '\n}\n' + \
        DIFFERENTIAL.replace('old_guest(a);', 'oldv::old_guest(a);') \
        .replace('new_guest(b);', 'newv::new_guest(b);')
    (work / 'differential.cpp').write_text(combined)
    flags = production_flags()
    exe = work / 'differential.exe'
    compile_log = work / 'differential.log'
    result = subprocess.run([str(compiler), *flags, str(work / 'differential.cpp'), '-o', str(exe)],
                            capture_output=True, text=True)
    compile_log.write_text(result.stdout + result.stderr)
    if result.returncode:
        expect('the differential fixture compiles', False, (result.stdout + result.stderr)[-800:])
        return {}
    expect('the differential fixture compiles', True)
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    expect('the transformed loop is behaviourally identical',
           run.returncode == 0, (run.stdout + run.stderr).strip()[-400:])
    print('    ' + run.stdout.strip())
    # the generated code of both versions
    counts = {}
    for tag, text in (('old', GQR_LOOP_TEXT), ('new', new)):
        path = work / ('%s.cpp' % tag)
        path.write_text(MOCK + text)
        obj = work / ('%s.o' % tag)
        result = subprocess.run([str(compiler), *flags, '-c', str(path), '-o', str(obj)],
                                capture_output=True, text=True)
        if result.returncode:
            expect('the %s loop compiles' % tag, False, result.stderr[-600:])
            continue
        expect('the %s loop compiles' % tag, True)
        info = count_code(objdump, obj)
        counts[tag] = info
    return counts


def production_check(work, compiler, production_root):
    """Apply the consumer to the real production unit and compile it."""
    hot = production_root / 'crash-hot' / 'hot_000.cpp'
    if not hot.is_file():
        expect('the production hot unit is available', False, str(hot))
        return {}
    text = hot.read_text(errors='surrogateescape')
    new, count, details = F.hoist_gqr_tests(text)
    expect('the consumer finds no eligible loop in the production unit',
           count == 0, 'count=%d' % count)
    expect('the consumer leaves the production unit unchanged', new == text)
    local = work / 'hot_000.cpp'
    local.write_text(new, errors='surrogateescape')
    include = production_root / 'nwii-full-source' / 'nWiiRuntime' / 'include'
    generated = production_root / 'crash-generated'
    flags = production_flags() + ['-I', str(include), '-I', str(generated), '-I',
                                  str(generated / 'nWiiRuntime'),
                                  '-fsyntax-only']
    result = subprocess.run([str(compiler), *flags, str(local)], capture_output=True, text=True)
    (work / 'hot_000.log').write_text(result.stdout + result.stderr)
    expect('the transformed production unit still compiles',
           result.returncode == 0, (result.stdout + result.stderr)[-800:])
    return {'production_hot': str(hot)}


# ---------------------------------------------------------------------------
# real source coverage
# ---------------------------------------------------------------------------

def real_source_candidates():
    base = Path(os.environ.get('COTT_GENOPT', r'C:\Users\deki\AppData\Local\Temp\opencode\cott-genopt'))
    return [base / 'primary_restored.txt', base / 'prod_restored.cpp',
            base / 'primary_prod.txt']


def check_real_source():
    found = False
    for path in real_source_candidates():
        if not path.is_file():
            continue
        found = True
        text = path.read_text(errors='ignore')
        report = F.analyse_source(text)
        new, count, details = F.hoist_gqr_tests(text)
        print('    %s: functions=%d units=%d blocks=%d joins=%d loops=%d '
              'gqr_sites=%d gqr_invariant=%d fp_facts=%d f32_facts=%d '
              'addr_bases=%d loop_invariant_bases=%d hoisted=%d' % (
                  path.name, report['functions'], report['units'], report['blocks'],
                  report['joins'], report['loops'], report['gqr_sites'],
                  report['gqr_sites_invariant'], report['fp_facts'],
                  report['fp_facts_canonical'], report['addr_bases'],
                  report['addr_loop_invariant'], count))
        expect('the real source %s parses' % path.name, report['functions'] > 0)
        expect('the consumer does not change the real source %s' % path.name,
               new == text, 'hoisted=%d' % count)
        if report['gqr_sites']:
            expect('the real GQR site of %s is not inside a hoistable loop' % path.name,
                   count == 0)
    expect('a real production source was found', found)


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', type=Path,
                    default=Path(os.environ.get('TEMP', '.')) / 'cott-genopt' / 'A' / 'work')
    ap.add_argument('--production-root', type=Path,
                    default=Path(r'C:\Users\deki\.codex\native-port-builds'
                                 r'\clean-room-guest-batch-20261019-2\native-port\build'))
    ap.add_argument('--skip-code', action='store_true')
    args = ap.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)

    check_join_disagreement()
    check_loop_fixed_point()
    check_backedge_invalidation()
    check_calls_polls_returns()
    check_address_bases()
    check_loop_bases()
    check_interior_entries()
    check_consumer()
    check_real_source()

    counts = {}
    if not args.skip_code:
        base = winlibs()
        if base is None:
            expect('a host compiler is available', False, 'no WinLibs g++ found')
        else:
            expect('a host compiler is available', True, str(base))
            counts = differential_and_code(args.work, base / 'g++.exe', base / 'objdump.exe')
            if counts:
                old = counts.get('old', {})
                new = counts.get('new', {})
                print('    generated code old: %s' % old)
                print('    generated code new: %s' % new)
                expect('the consumer changes the generated host code',
                       old.get('instructions') != new.get('instructions'),
                       'old=%s new=%s' % (old.get('instructions'), new.get('instructions')))
            production_check(args.work, base / 'g++.exe', args.production_root)

    failed = [r for r in RESULTS if not r[1]]
    print('\n%d checks, %d failed' % (len(RESULTS), len(failed)))
    for name, ok, detail in failed:
        print('  FAILED %s :: %s' % (name, detail))
    (args.work / 'results.json').write_text(json.dumps(
        [{'name': n, 'ok': o, 'detail': d} for n, o, d in RESULTS], indent=2) + NL)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

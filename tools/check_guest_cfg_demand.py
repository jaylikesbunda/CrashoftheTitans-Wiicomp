"""Local analysis and differential checks for the CFG-wide CR / XER demand pass.

The checks never stage inputs, never launch a game and never touch the
repository: every generated artefact lives under the work directory.

    python native-port/tools/check_guest_cfg_demand.py --work <dir>

Sections
--------
1. `analysis_checks`  - hand written CFGs for joins, loops, barriers, polls,
   returns and the whole-XER / carry-only update distinction.
2. `fuzz_checks`      - randomised small CFGs compiled with the production
   compiler and executed against the untransformed body: every CR bit, the XER
   word, the GPR / CTR / IC locals and the observer log must match at every
   exit, including the interior entries.
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guest_batch_lowering as L            # noqa: E402
import guest_cfg_demand as D                # noqa: E402
from port_paths import winlibs_bin          # noqa: E402

GPRS = ['g0', 'g1', 'g2', 'g3']
CRS = ['c0lt', 'c0gt', 'c0eq', 'c0so', 'c1lt', 'c1gt', 'c1eq']


def fixture(body, name='func_00000001'):
    return 'void %s(NWII_CTX_PARAM ctx) {\n%s\n}' % (name, body)


# ---------------------------------------------------------------- analysis
def analysis_checks():
    out = {}

    # a join where the bit is live on one path only: the definition must stay
    live_one_path = fixture('''
    uint32_t g0;
    bool c0lt, c0eq;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt; c0eq = ctx.cr[0].eq;
    c0lt = ((int32_t)g0 < 0);
    if (g0) goto loc_00000002;
    c0eq = (g0 == 0);
loc_00000002: ;
    if (c0lt) goto loc_00000003;
loc_00000003: ;
    return;
''')
    stats = D.scan(live_one_path)
    assert stats['cr_dead'] == 0, stats
    out['join_live_on_one_path_kept'] = stats['cr_dead']

    # the same join without a reader: the definition is dead on every path
    dead_join = fixture('''
    uint32_t g0;
    bool c0lt, c0eq;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt; c0eq = ctx.cr[0].eq;
    c0lt = ((int32_t)g0 < 0);
    if (g0) goto loc_00000002;
    c0eq = (g0 == 0);
loc_00000002: ;
    c0lt = ((int32_t)g0 < 0);
    if (c0eq) goto loc_00000003;
loc_00000003: ;
    return;
''')
    stats = D.scan(dead_join)
    assert stats['cr_dead'] == 1, stats
    new, _, deletions = D.eliminate_detailed(dead_join)
    assert len(deletions) == 1 and 'c0lt' in deletions[0][1], deletions
    assert 'c0eq = (g0 == 0);' in new
    assert D.restore(new, deletions) == dead_join
    out['join_dead_on_every_path_removed'] = stats['cr_dead']

    # a loop whose definition is overwritten before the next reader
    loop_dead = fixture('''
    uint32_t g0;
    uint32_t ctrv;
    bool c0lt, c0eq;
    g0 = ctx.gpr[0]; ctrv = ctx.ctr; c0lt = ctx.cr[0].lt; c0eq = ctx.cr[0].eq;
    ctrv = 3;
loc_00000001: ;
    c0lt = ((int32_t)g0 < 0);
    c0eq = (g0 == 0);
    g0 = g0 + 1;
    c0lt = ((int32_t)g0 < 0);
    ctrv = ctrv - 1;
    if (ctrv != 0) goto loc_00000001;
    ctx.cr[0].lt = c0lt; ctx.cr[0].eq = c0eq;
    return;
''')
    stats = D.scan(loop_dead)
    assert stats['cr_dead'] == 1, stats
    out['loop_dead_until_overwrite_removed'] = stats['cr_dead']

    # a loop whose back edge reads the definition
    loop_live = fixture('''
    uint32_t g0;
    uint32_t ctrv;
    bool c0lt, c0eq;
    g0 = ctx.gpr[0]; ctrv = ctx.ctr; c0lt = ctx.cr[0].lt; c0eq = ctx.cr[0].eq;
    ctrv = 3;
loc_00000001: ;
    c0eq = (g0 == 0);
    c0lt = ((int32_t)g0 < 0);
    g0 = g0 + 1;
    ctrv = ctrv - 1;
    if (ctrv != 0) goto loc_00000001;
    ctx.cr[0].lt = c0lt; ctx.cr[0].eq = c0eq;
    return;
''')
    stats = D.scan(loop_live)
    assert stats['cr_dead'] == 0, stats
    out['loop_backedge_reader_kept'] = stats['cr_dead']

    # a barrier observes the CR state: the definition must stay
    barrier = fixture('''
    uint32_t g0;
    bool c0lt;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt;
    c0lt = ((int32_t)g0 < 0);
    observer(ctx);
    return;
''')
    stats = D.scan(barrier)
    assert stats['cr_dead'] == 0, stats
    out['barrier_observer_kept'] = stats['cr_dead']

    # a poll observes the CR state as well
    poll = fixture('''
    uint32_t g0;
    bool c0lt;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt;
    c0lt = ((int32_t)g0 < 0);
    if (nwii::runtime::native_poll_due()) { observer(ctx); }
    return;
''')
    stats = D.scan(poll)
    assert stats['cr_dead'] == 0, stats
    out['poll_observer_kept'] = stats['cr_dead']

    # a return observes the CR state
    ret = fixture('''
    uint32_t g0;
    bool c0lt;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt;
    c0lt = ((int32_t)g0 < 0);
    return;
''')
    stats = D.scan(ret)
    assert stats['cr_dead'] == 0, stats
    out['return_observer_kept'] = stats['cr_dead']

    # the promotion write back is an observation too
    writeback = fixture('''
    uint32_t g0;
    bool c0lt;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt;
    c0lt = ((int32_t)g0 < 0);
    ctx.cr[0].lt = c0lt;
    return;
''')
    stats = D.scan(writeback)
    assert stats['cr_dead'] == 0, stats
    out['writeback_kept'] = stats['cr_dead']

    # a whole word XER read-modify-write is never removed, a carry only one is
    xer = fixture('''
    uint32_t g0;
    uint32_t xer;
    g0 = ctx.gpr[0]; xer = ctx.xer;
    xer = (xer & ~(1 << 29)) | (((g0 >> 3) & 1) << 29);
    xer = (xer & 0x000000E0u) | 0;
    xer = (xer & ~(1 << 29)) | (((g0 >> 5) & 1) << 29);
    ctx.xer = xer;
    return;
''')
    stats = D.scan(xer)
    assert stats['xer_ca_updates'] == 2, stats
    assert stats['xer_dead'] == 0, stats
    out['whole_xer_update_kept'] = stats['xer_dead']

    xer_dead = fixture('''
    uint32_t g0;
    uint32_t xer;
    g0 = ctx.gpr[0]; xer = ctx.xer;
    xer = (xer & ~(1 << 29)) | (((g0 >> 3) & 1) << 29);
    xer = (xer & ~(1 << 29)) | (((g0 >> 5) & 1) << 29);
    ctx.xer = xer;
    return;
''')
    stats = D.scan(xer_dead)
    assert stats['xer_dead'] == 1, stats
    new, _, deletions = D.eliminate_detailed(xer_dead)
    assert len(deletions) == 1 and 'xer = (xer & ~(1 << 29))' in deletions[0][1], deletions
    assert D.restore(new, deletions) == xer_dead
    out['carry_only_update_removed'] = stats['xer_dead']

    # an FP comparison is not a pure integer comparison: it must stay
    fp = fixture('''
    double f0, f1;
    bool c0lt;
    f0 = ctx.fpr[0]; f1 = ctx.fpr[1]; c0lt = ctx.cr[0].lt;
    c0lt = !un && (f0 < f1);
    return;
''')
    stats = D.scan(fp)
    assert stats['cr_dead'] == 0, stats
    out['fp_comparison_kept'] = stats['cr_dead']

    # a call in the right hand side is not pure
    call = fixture('''
    uint32_t g0;
    bool c0lt;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt;
    c0lt = (helper(g0) < 0);
    return;
''')
    stats = D.scan(call)
    assert stats['cr_dead'] == 0, stats
    out['call_rhs_kept'] = stats['cr_dead']

    # an unpromoted definition site is understood as well
    unpromoted = fixture('''
    uint32_t g0;
    g0 = ctx.gpr[0];
    ctx.cr[0].lt = ((int32_t)g0 < 0);
    ctx.cr[0].lt = ((int32_t)g0 < 0);
    return;
''')
    stats = D.scan(unpromoted)
    assert stats['cr_dead'] == 1, stats
    out['unpromoted_site_removed'] = stats['cr_dead']

    # a definition that is the body of a braceless control header is never
    # removed even when it is dead
    braced = fixture('''
    uint32_t g0;
    bool c0lt, c0eq;
    g0 = ctx.gpr[0]; c0lt = ctx.cr[0].lt; c0eq = ctx.cr[0].eq;
    c0lt = ((int32_t)g0 < 0);
    if (g0)
        c0eq = ((int32_t)g0 < 0);
    c0eq = (g0 == 0);
    c0lt = ((int32_t)g0 < 0);
    return;
''')
    stats = D.scan(braced)
    assert stats['cr_dead'] == 1, stats
    new, _, deletions = D.eliminate_detailed(braced)
    assert len(deletions) == 1, deletions
    assert 'c0eq = ((int32_t)g0 < 0);' in new, 'a braceless control body was removed'
    out['braceless_control_body_kept'] = stats['cr_dead']

    # the purity rule is the one of condition_demand: a definition of a name
    # that is overwritten later in the same straight line run is removed by
    # condition_demand exactly when its right hand side is pure
    samples = ['((int32_t)g0 < 0)', '(g0 == 0)', '!un && (f0 < f1)', '(helper(g0) < 0)',
               '(g0 * 2) < 3', '((xer & 1) != 0)', 'true', 'g0', 'c0lt', '((int32_t)g0 == 0)',
               '((int32_t)g0 + 1) < 0', '(g0 / 2) == 0']
    for value in samples:
        probe = '    c0lt = %s;\n    c0lt = ((int32_t)g0 < 0);\n' % value
        expected = L.condition_demand(probe)[1]
        mine = 1 if D.pure_cr_value(value) else 0
        assert expected == mine, (value, expected, mine)
    out['purity_matches_condition_demand'] = len(samples)

    # the carry value rule is the one of carry_regions
    carry_samples = [
        '(((res >> 32) & 1) << 29)',
        '((res & 1) ? (1u << 29) : 0u)',
        '(((res >> 32) & 1u) << 29)',
        '(((res * 3) & 1) << 29)',
    ]
    for value in carry_samples:
        probe = '{\n    xer = (xer & ~(1 << 29)) | %s;\n    xer = (xer & ~(1 << 29)) | %s;\n}\n' % (value, value)
        accepted = L.carry_regions(probe)[1] == 2
        mine = D._ca_value_accepted(value, probe)
        assert accepted == mine, (value, accepted, mine)
    out['carry_value_matches_carry_regions'] = len(carry_samples)
    return out


# ---------------------------------------------------------------- fuzz
CR_SLOT = {name: index for index, name in enumerate(CRS)}
GPR_SLOT = {name: index for index, name in enumerate(GPRS)}
BIT_CTRV = 4
BIT_IC = 5
BIT_XER = 6
BIT_CR0 = 7


def _cr_bit(name):
    return 1 << (BIT_CR0 + CR_SLOT[name])


def _ctx_cr_name(name):
    field = name[1]
    return 'ctx.cr[%s].%s' % (field, name[2:])


def gen_function(rng, index, force=False):
    """Random promoted-style function body with joins, a loop and entries.

    With `force` the body is a straight line chain (no conditional branch, no
    early return) so that every definition is executed on every path; the
    removed definitions can then be checked with an independent linear scan.
    """
    count = rng.randint(3, 8)
    labels = ['loc_%08X' % (0x80314100 + i * 4) for i in range(count)]
    loop_start = rng.randrange(0, max(1, count - 1))
    loop_end = rng.randint(loop_start + 1, count)
    if force:
        loop_start = count - 1
        loop_end = count
    loop_units = set(range(loop_start, loop_end))

    def cr_def():
        name = rng.choice(CRS)
        a = rng.choice(GPRS)
        b = rng.choice(GPRS)
        kind = rng.randrange(4)
        if kind == 0:
            return '    %s = ((int32_t)%s < 0);' % (name, a)
        if kind == 1:
            return '    %s = ((int32_t)%s > 0);' % (name, a)
        if kind == 2:
            return '    %s = (%s == 0);' % (name, a)
        return '    %s = ((int32_t)%s < (int32_t)%s);' % (name, a, b)

    def gpr_stmt():
        a = rng.choice(GPRS)
        b = rng.choice(GPRS)
        return '    %s = %s + %d;' % (a, b, rng.randrange(1, 9))

    plain = [cr_def, gpr_stmt, gpr_stmt,
             lambda: '    ic = ic + 1;',
             lambda: '    xer = (xer & ~(1 << 29)) | (((%s >> %d) & 1) << 29);' % (rng.choice(GPRS), rng.randrange(0, 8)),
             lambda: '    xer = (xer & 0x000000E0u) | 0;']

    units = []
    for unit in range(count):
        body = ['    if (++g_steps > 1000000u) std::abort();']
        while len(body) < rng.randint(2, 5):
            kind = rng.random()
            if kind < 0.30:
                # a run of definitions of the same CR field: the earlier ones
                # are overwritten before any reader
                name = rng.choice(CRS)
                for _ in range(rng.randint(2, 3)):
                    body.append('    %s = ((int32_t)%s < %d);' % (name, rng.choice(GPRS), rng.randrange(0, 4)))
                continue
            if kind < 0.50:
                body.append(plain[rng.randrange(len(plain))]())
            elif kind < 0.62 and not force:
                body.append('    if (%s) goto @@%d@@;' % (rng.choice(CRS), rng.randrange(count)))
            elif kind < 0.70 and not force:
                body.append('    if (((xer >> 29) & 1u) != 0) goto @@%d@@;' % rng.randrange(count))
            elif kind < 0.80:
                body.append('    @@BARRIER@@')
            elif kind < 0.86:
                body.append('    @@POLL@@')
            elif kind < 0.93 and not force:
                body.append('    return;')
            else:
                body.append('    ctx.pc = 0x%08X;' % (0x80314100 + unit * 4))
        units.append(body)

    # the loop back edge
    if force:
        # the forced variant is a straight line prefix plus one self looping
        # tail, so that every definition is executed exactly once
        units[count - 1] = ['    if (++g_steps > 1000000u) std::abort();']
    units[loop_end - 1].append('    lp = lp - 1;')
    units[loop_end - 1].append('    if (lp != 0) goto %s;' % labels[loop_start])

    # resolve the forward goto placeholders: a branch may only go forward, a
    # branch out of the last unit is dropped so that no CFG can loop forever
    for unit in range(count):
        resolved = []
        for line in units[unit]:
            while True:
                m = re.search(r'@@(\d+)@@', line)
                if not m:
                    break
                target = int(m.group(1))
                if target <= unit:
                    if unit + 1 < count:
                        target = unit + 1
                    else:
                        line = '    ctx.pc = 0x%08X;' % (0x80314100 + unit * 4)
                        target = None
                        break
                line = line[:m.start()] + labels[target] + line[m.end():]
            resolved.append(line)
        units[unit] = resolved

    # control flow (same rules as the analysis: a trailing return stops the
    # fall through, everything else falls through)
    succ = []
    for unit in range(count):
        targets = set()
        for line in units[unit]:
            m = re.search(r'goto (loc_[0-9A-F]{8});', line)
            if m:
                targets.add(labels.index(m.group(1)))
        if not units[unit][-1].strip().startswith('return;') and unit + 1 < count:
            targets.add(unit + 1)
        succ.append(sorted(targets))

    # the barrier / poll placeholders become spill points
    for unit in range(count):
        units[unit] = ['@@SPILL@@' if line == '    @@BARRIER@@' else
                        '@@POLLSPILL@@' if line == '    @@POLL@@' else line
                        for line in units[unit]]

    # may-dirty sets, exactly like the promotion: a barrier syncs (the dirty
    # set is reset), a poll does not
    def sweep(unit, dirty, masks):
        for position, line in enumerate(units[unit]):
            if line in ('@@SPILL@@', '@@POLLSPILL@@'):
                masks[(unit, position)] = dirty
                if line == '@@SPILL@@':
                    dirty = 0
                continue
            for name in GPRS:
                if re.search(r'(?<![\w.])%s\s*=(?!=)' % name, line):
                    dirty |= 1 << GPR_SLOT[name]
            if re.search(r'(?<![\w.])ctrv\s*=(?!=)', line):
                dirty |= 1 << BIT_CTRV
            if re.search(r'(?<![\w.])ic\s*=(?!=)', line):
                dirty |= 1 << BIT_IC
            if re.search(r'(?<![\w.])xer\s*=(?!=)', line):
                dirty |= 1 << BIT_XER
            for name in CRS:
                if re.search(r'(?<![\w.])%s\s*=(?!=)' % name, line):
                    dirty |= _cr_bit(name)
        return dirty

    incoming = [0] * count
    outgoing = [0] * count
    changed = True
    while changed:
        changed = False
        for unit in range(count):
            new = 0
            for pred in range(count):
                if unit in succ[pred]:
                    new |= outgoing[pred]
            if new != incoming[unit]:
                incoming[unit] = new
                changed = True
            out = sweep(unit, incoming[unit], {})
            if out != outgoing[unit]:
                outgoing[unit] = out
                changed = True
    # the settled may-dirty set at every spill point
    masks = {}
    for unit in range(count):
        sweep(unit, incoming[unit], masks)

    def spill(mask):
        parts = []
        for name in GPRS:
            if mask & (1 << GPR_SLOT[name]):
                parts.append('ctx.gpr[%d] = %s;' % (GPR_SLOT[name], name))
        if mask & (1 << BIT_CTRV):
            parts.append('ctx.ctr = ctrv;')
        if mask & (1 << BIT_IC):
            parts.append('ctx.inst_count = ic;')
        if mask & (1 << BIT_XER):
            parts.append('ctx.xer = xer;')
        for name in CRS:
            if mask & _cr_bit(name):
                parts.append('%s = %s;' % (_ctx_cr_name(name), name))
        return ' '.join(parts)

    def reload(mask):
        parts = []
        for name in GPRS:
            if mask & (1 << GPR_SLOT[name]):
                parts.append('%s = ctx.gpr[%d];' % (name, GPR_SLOT[name]))
        if mask & (1 << BIT_CTRV):
            parts.append('ctrv = ctx.ctr;')
        if mask & (1 << BIT_IC):
            parts.append('ic = ctx.inst_count;')
        if mask & (1 << BIT_XER):
            parts.append('xer = ctx.xer;')
        for name in CRS:
            if mask & _cr_bit(name):
                parts.append('%s = %s;' % (name, _ctx_cr_name(name)))
        return ' '.join(parts)

    lines = []
    lines.append('void func_%08X(NWII_CTX_PARAM ctx) {' % (0x80314000 + index))
    lines.append('    uint32_t %s;' % ', '.join(GPRS))
    lines.append('    uint32_t ctrv;')
    lines.append('    uint64_t ic;')
    lines.append('    uint32_t xer;')
    lines.append('    uint32_t lp;')
    lines.append('    bool %s;' % ', '.join(CRS))
    entries = [unit for unit in range(count) if unit not in loop_units]
    if force:
        entries = []
    else:
        rng.shuffle(entries)
        entries = entries[:rng.randint(0, min(3, len(entries)))]
    if entries:
        lines.append('    if (ctx.pc != 0x%08X) {' % (0x80314000 + index))
        lines.append('        switch (ctx.pc) {')
        for unit in entries:
            full = (1 << len(GPRS)) | (1 << BIT_CTRV) | (1 << BIT_IC) | (1 << BIT_XER)
            for name in CRS:
                full |= _cr_bit(name)
            lines.append('            case 0x%08X: %s lp = 3; goto %s;'
                         % (0x80314100 + unit * 4, reload(full), labels[unit]))
        lines.append('            default: return;')
        lines.append('        }')
        lines.append('    }')
    full = (1 << len(GPRS)) | (1 << BIT_CTRV) | (1 << BIT_IC) | (1 << BIT_XER)
    for name in CRS:
        full |= _cr_bit(name)
    lines.append('    %s lp = 3;' % reload(full))
    for unit in range(count):
        lines.append('%s: ;' % labels[unit])
        for position, line in enumerate(units[unit]):
            key = (unit, position)
            if line == '@@SPILL@@':
                mask = masks[key]
                lines.append('    %s' % spill(mask))
                lines.append('    observer(ctx);')
                lines.append('    %s' % reload(mask))
            elif line == '@@POLLSPILL@@':
                mask = masks[key]
                lines.append('    if (nwii::runtime::native_poll_due()) { %s observer(ctx); %s }'
                             % (spill(mask), reload(mask)))
            else:
                lines.append(line)
    lines.append('}')
    text = '\n'.join(lines)
    return text, [0x80314100 + unit * 4 for unit in entries]


PRELUDE = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <random>
struct CRField { bool lt, gt, eq, so; };
struct Ctx {
  uint32_t gpr[32];
  CRField cr[8];
  uint32_t ctr, xer, pc;
  uint64_t inst_count;
};
#define NWII_CTX_PARAM Ctx&
namespace nwii { namespace runtime {
inline unsigned g_polls = 0;
inline bool native_poll_due() { return (g_polls++ % 3) == 0; }
} }
static unsigned long long g_steps = 0;
static std::vector<uint64_t> g_log;
static void observer(Ctx& c) {
  uint64_t seen = c.xer;
  for (int f = 0; f < 8; ++f) {
    seen = seen * 1000003u + (c.cr[f].lt ? 1u : 0u) + (c.cr[f].gt ? 2u : 0u)
         + (c.cr[f].eq ? 4u : 0u) + (c.cr[f].so ? 8u : 0u);
  }
  g_log.push_back(seen);
  c.cr[0].lt = !c.cr[0].lt;
  c.xer ^= 0x20000000u;
}
struct Snapshot {
  uint32_t gpr[32]; bool cr[32]; uint32_t ctr, xer, pc; uint64_t ic; unsigned long long steps; std::vector<uint64_t> log;
  bool operator==(const Snapshot& o) const {
    for (int i = 0; i < 32; ++i) if (gpr[i] != o.gpr[i]) return false;
    for (int i = 0; i < 32; ++i) if (cr[i] != o.cr[i]) return false;
    return ctr == o.ctr && xer == o.xer && pc == o.pc && ic == o.ic && log == o.log;
  }
};
static void reset(Ctx& c, unsigned trial) {
  std::mt19937 r(trial * 2654435761u + 12345u);
  for (auto& v : c.gpr) v = r();
  for (int f = 0; f < 8; ++f) { c.cr[f].lt = r() & 1; c.cr[f].gt = r() & 1; c.cr[f].eq = r() & 1; c.cr[f].so = r() & 1; }
  c.ctr = r(); c.xer = r(); c.pc = 0; c.inst_count = r();
  g_log.clear(); g_steps = 0; nwii::runtime::g_polls = 0;
}
static Snapshot snap(const Ctx& c) {
  Snapshot s; for (int i = 0; i < 32; ++i) s.gpr[i] = c.gpr[i];
  for (int f = 0; f < 8; ++f) { s.cr[f*4+0]=c.cr[f].lt; s.cr[f*4+1]=c.cr[f].gt; s.cr[f*4+2]=c.cr[f].eq; s.cr[f*4+3]=c.cr[f].so; }
  s.ctr = c.ctr; s.xer = c.xer; s.pc = c.pc; s.ic = c.inst_count; s.steps = g_steps; s.log = g_log; return s;
}
'''

HARNESS = r'''
int main() {
  unsigned long long checks = 0;
  for (unsigned trial = 0; trial < 12; ++trial) {
    for (unsigned c = 0; c < CASE_COUNT; ++c) {
      for (unsigned e = 0; e < ENTRY_COUNT; ++e) {
        uint32_t pc = entries[c][e];
        Ctx a, b;
        reset(a, trial); a.pc = pc; old_fn[c](a); Snapshot sa = snap(a);
        reset(b, trial); b.pc = pc; new_fn[c](b); Snapshot sb = snap(b);
        if (sa.steps > 1000000u || sb.steps > 1000000u) { std::printf("STEP OVERFLOW case=%u\n", c); std::abort(); }
        if (!(sa == sb)) {
          std::printf("DIFFERENTIAL FAILURE case=%u entry=%u pc=%08X trial=%u\n", c, e, pc, trial);
          for (int i = 0; i < 32; ++i) if (sa.gpr[i] != sb.gpr[i]) std::printf("  gpr[%d] %08X != %08X\n", i, sa.gpr[i], sb.gpr[i]);
          for (int i = 0; i < 32; ++i) if (sa.cr[i] != sb.cr[i]) std::printf("  cr[%d] %d != %d\n", i, (int)sa.cr[i], (int)sb.cr[i]);
          if (sa.ctr != sb.ctr) std::printf("  ctr %08X != %08X\n", sa.ctr, sb.ctr);
          if (sa.xer != sb.xer) std::printf("  xer %08X != %08X\n", sa.xer, sb.xer);
          if (sa.ic != sb.ic) std::printf("  ic %llu != %llu\n", (unsigned long long)sa.ic, (unsigned long long)sb.ic);
          if (sa.log != sb.log) std::printf("  observer log %zu != %zu\n", sa.log.size(), sb.log.size());
          std::fflush(stdout);
          std::abort();
        }
        ++checks;
      }
    }
  }
  std::printf("CFG demand fuzz: %llu comparisons over %u cases, %llu removed definitions; all CR bits, XER, GPR/CTR/IC locals and observer logs matched.\n",
              checks, (unsigned)CASE_COUNT, (unsigned long long)g_removed);
  return 0;
}
'''


OBSERVER_RE = re.compile(r'observer\(ctx\)|native_poll_due|\breturn\s*;')


def independent_dead_check(text, deletions):
    """Independent linear verification of a removed definition.

    The definition must be overwritten before any reader of the same component
    and no barrier, poll or return may observe the component in between.  The
    check does not use the analysis at all, it only scans the text.
    """
    lines = text.split('\n')
    for index, original in deletions:
        m = re.match(r'\s*(c[0-7](?:lt|gt|eq|so))\s*=', original)
        if m:
            name = m.group(1)
            pattern = r'(?<![\w.])%s\b' % re.escape(name)
            definition = r'(?<![\w.])%s\s*=(?!=)' % re.escape(name)
        else:
            name = 'xer'
            pattern = r'(?<![\w.])xer\b'
            definition = r'(?<![\w.])xer\s*=(?!=)'
        for later in lines[index + 1:]:
            if re.search(definition, later):
                break
            if re.search(pattern, later):
                return False, 'reader of %s after line %d: %r' % (name, index, later.strip()[:80])
            if OBSERVER_RE.search(later):
                return False, 'observation after the definition of %s at line %d' % (name, index)
    return True, ''


def fuzz_checks(work, batches=3, per_batch=40, seed=90210):
    compiler = str(winlibs_bin() / 'g++.exe') if winlibs_bin() else shutil.which('g++')
    assert compiler, 'no C++ compiler found'
    total_removed = 0
    total_cases = 0
    forced_removed = 0
    for batch in range(batches):
        rng = random.Random(seed + batch)
        olds = []
        news = []
        entry_table = []
        removed = 0
        forced = 0
        for index in range(per_batch * 2):
            is_forced = index >= per_batch
            text, entries = gen_function(rng, index, force=is_forced)
            new, stats, deletions = D.eliminate_detailed(text)
            assert D.restore(new, deletions) == text
            again, _ = D.eliminate(new)
            assert again == new, 'not idempotent'
            before = D.parse_function(text)
            after = D.parse_function(new)
            assert after is not None
            assert after.entries == before.entries, 'interior entries changed'
            assert [l for l in before.body if D.LABEL_RE.match(l)] == [l for l in after.body if D.LABEL_RE.match(l)]
            kept = [l for l in new.split('\n') if l.strip()]
            source_lines = [l for l in text.split('\n') if l.strip()]
            for line in kept:
                assert line in source_lines, 'a surviving line was rewritten'
            if is_forced:
                ok, why = independent_dead_check(text, deletions)
                assert ok, why
                forced_removed += len(deletions)
                forced += 1
            removed += len(deletions)
            olds.append(text)
            news.append(new)
            entry_table.append([0x80314000 + index] + list(entries))
        total_removed += removed
        total_cases += per_batch * 2
        cases = per_batch * 2
        width = max(len(e) for e in entry_table)
        source = [PRELUDE]
        source.append('static unsigned long long g_removed = %d;' % removed)
        for index, text in enumerate(olds):
            source.append('namespace old_%d {\n%s\n}' % (index, text))
        for index, text in enumerate(news):
            source.append('namespace new_%d {\n%s\n}' % (index, text))
        source.append('typedef void (*Fn)(Ctx&);')
        source.append('static Fn old_fn[] = {' + ','.join('old_%d::func_%08X' % (i, 0x80314000 + i) for i in range(cases)) + '};')
        source.append('static Fn new_fn[] = {' + ','.join('new_%d::func_%08X' % (i, 0x80314000 + i) for i in range(cases)) + '};')
        rows = []
        for index, entry in enumerate(entry_table):
            row = list(entry) + [0x80314000 + index] * (width - len(entry))
            rows.append('{' + ','.join('0x%08X' % v for v in row) + '}')
        source.append('static uint32_t entries[][%d] = {%s};' % (width, ','.join(rows)))
        source.append('#define CASE_COUNT %d' % cases)
        source.append('#define ENTRY_COUNT %d' % width)
        source.append(HARNESS)
        path = work / ('fuzz_%d.cpp' % batch)
        path.write_text('\n'.join(source))
        binary = work / ('fuzz_%d.exe' % batch)
        command = [compiler, '-std=c++20', '-O1', '-march=x86-64-v3', '-mfma', '-fwrapv',
                   '-fno-strict-aliasing', '-ffp-contract=off', str(path), '-o', str(binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            (work / ('fuzz_%d.log' % batch)).write_text(result.stdout + result.stderr)
            raise SystemExit((result.stdout + result.stderr)[-12000:])
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=300)
        if result.returncode:
            (work / ('fuzz_%d.log' % batch)).write_text(result.stdout + result.stderr)
            raise SystemExit(result.stdout + result.stderr)
        print(result.stdout.strip(), flush=True)
    return {'fuzz_cases': total_cases, 'fuzz_removed': total_removed, 'fuzz_forced_removed': forced_removed}


def production_coverage(paths):
    """Static coverage of the real production source (read only)."""
    out = []
    for path in paths:
        text = Path(path).read_text(errors='surrogateescape')
        for name, body in D.iter_functions(text):
            stats = D.scan(body)
            out.append({'file': Path(path).name, 'function': name,
                        'cr_defs': stats.get('cr_defs'), 'cr_pure_defs': stats.get('cr_pure_defs'),
                        'cr_dead': stats.get('cr_dead'),
                        'xer_word_defs': stats.get('xer_word_defs'),
                        'xer_ca_updates': stats.get('xer_ca_updates'),
                        'xer_accepted': stats.get('xer_accepted'),
                        'xer_dead': stats.get('xer_dead'),
                        'units': stats.get('units'), 'barriers': stats.get('barriers'),
                        'polls': stats.get('polls'), 'returns': stats.get('returns')})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--batches', type=int, default=3)
    parser.add_argument('--per-batch', type=int, default=40)
    parser.add_argument('--production', nargs='*', default=[],
                        help='read only production units for the static coverage report')
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    results = {'analysis': analysis_checks()}
    results.update(fuzz_checks(args.work, args.batches, args.per_batch))
    if args.production:
        results['production'] = production_coverage(args.production)
    print(json.dumps(results, indent=2, sort_keys=True))
    (args.work / 'results.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()

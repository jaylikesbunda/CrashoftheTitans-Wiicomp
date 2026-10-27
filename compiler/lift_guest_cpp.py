"""Lift generated nWii guest C++ bodies into a small semantic IR.

The production pipeline already decodes PPC words into a very regular C++
statement grammar (see storage/crash-generated/output_*.cpp).  This module
parses that grammar into an op list so a whole *module* of guest functions can
be re-emitted as one LLVM IR module (see emit_llvm_module.py).

Only the integer subset is lifted; anything else marks the function opaque so
its callers fall back to the original C++ symbol with a full register clobber.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# op encoding: (kind, ...)
#  ('ic', n)
#  ('read', d, a, off, width) ('write', s, a, off, width)
#  ('mr', d, a) ('mflr', d) ('mtlr', a) ('extsh', d, a)
#  ('li', d, v) ('addi', d, a, imm)
#  ('add'|'subf'|'mullw'|'mulhwu'|'divwu'|'or'|'orc', d, a, b)
#  ('mulli', d, a, imm) ('ori', d, a, imm) ('rlwinm', d, a, sh, mask)
#  ('srawi', d, a, sh, mask, xerbit)
#  ('setcr', field, bit, spec)
#  ('b', target) ('bc', field, bit, target, invert) ('bclr', field, bit, invert)
#  ('bl', target, ret, callee) ('retpc', target) ('blr') ('bctr', a)
#  ('label', pc)

BRANCHERS = {'b', 'bc', 'bclr', 'bl', 'retpc', 'blr', 'bctr'}


class Unsupported(Exception):
    pass


@dataclass
class Fn:
    name: str
    entry: int
    ops: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)   # pc -> op index
    calls: list = field(default_factory=list)
    inputs: set = field(default_factory=set)     # gpr live-in
    defs: set = field(default_factory=set)       # gpr written
    cr_in: set = field(default_factory=set)
    cr_out: set = field(default_factory=set)
    xer_in: bool = False
    xer_out: bool = False
    lr_in: bool = False
    lr_out: bool = False


def imm(text):
    text = text.strip()
    neg = text.startswith('-')
    if neg:
        text = text[1:]
    v = int(text, 0)
    return (-v) & 0xFFFFFFFF if neg else (v & 0xFFFFFFFF)


def parse(name, src):
    entry = int(name[5:], 16)
    lines = []
    for raw in src.splitlines():
        s = raw.strip()
        if '//' in s:
            s = s[:s.index('//')]
        s = s.strip()
        if s:
            lines.append(s)
    try:
        i = lines.index('++ctx.inst_count;')
    except ValueError:
        raise Unsupported('no entry guard')
    fn = Fn(name=name, entry=entry)
    body = lines[i + 1:]
    fn.ops.append(('ic', 1))  # the entry ++ctx.inst_count stripped with the guard

    j, n = 0, len(body)
    while j < n:
        s = body[j]
        nxt = body[j + 1] if j + 1 < n else ''
        nxt2 = body[j + 2] if j + 2 < n else ''
        nxt3 = body[j + 3] if j + 3 < n else ''
        j += 1

        if s in ('{', '}'):
            continue
        if s.startswith('NWII_TRACE_CALL') or s.startswith('if(native_blocks_on)'):
            continue
        if s == '++ctx.inst_count;':
            fn.ops.append(('ic', 1)); continue
        m = re.fullmatch(r'ctx\.inst_count \+= (\d+);', s)
        if m:
            fn.ops.append(('ic', int(m.group(1)))); continue

        m = re.fullmatch(r'loc_([0-9A-F]{8}): ;', s)
        if m:
            fn.labels[int(m.group(1), 16)] = len(fn.ops)
            fn.ops.append(('label', int(m.group(1), 16))); continue

        m = re.fullmatch(r'goto loc_([0-9A-F]{8});', s)
        if m:
            fn.ops.append(('b', int(m.group(1), 16))); continue

        m = re.fullmatch(r'if \((!?)ctx\.cr\[(\d)\]\.(lt|gt|eq|so)\) goto loc_([0-9A-F]{8});', s)
        if m:
            fn.ops.append(('bc', int(m.group(2)), m.group(3), int(m.group(4), 16), m.group(1) == '!'))
            continue

        m = re.fullmatch(r'if \((!?)ctx\.cr\[(\d)\]\.(lt|gt|eq|so)\) \{ ctx\.pc = ctx\.lr; return; \}', s)
        if m:
            fn.ops.append(('bclr', int(m.group(2)), m.group(3), m.group(1) == '!'))
            continue

        if s == 'ctx.pc = ctx.lr; return;':
            fn.ops.append(('blr',)); continue

        m = re.fullmatch(r'ctx\.pc = (0x[0-9A-F]{8}); return;', s)
        if m:
            fn.ops.append(('retpc', int(m.group(1), 16))); continue

        # direct call block
        m = re.fullmatch(r'ctx\.lr = (0x[0-9A-F]{8});', s)
        if m:
            ret = int(m.group(1), 16)
            mc = re.fullmatch(r'ctx\.pc = (0x[0-9A-F]{8}); (func_[0-9A-F]{8})\(ctx\);', nxt)
            mchk = re.fullmatch(r'if \(ctx\.pc != (0x[0-9A-F]{8})\) return;', nxt2)
            if not (mc and mchk and int(mchk.group(1), 16) == ret):
                raise Unsupported('bad call block: ' + s + ' | ' + nxt + ' | ' + nxt2)
            fn.ops.append(('bl', int(mc.group(1), 16), ret, mc.group(2)))
            j += 2
            continue

        # indirect call
        m = re.fullmatch(r'ctx\.ctr = ctx\.gpr\[(\d+)\];', s)
        if m:
            if not re.fullmatch(r'if \(ctx\.ctr\) \{ ctx\.pc = ctx\.ctr; return; \} else nwii::runtime\.note_null_call\(0x[0-9A-F]{8}, ctx\.lr\);', nxt):
                raise Unsupported('bad indirect: ' + nxt)
            fn.ops.append(('bctr', int(m.group(1))))
            j += 1
            continue

        # stwu block
        m = re.fullmatch(r'uint32_t ea = ctx\.gpr\[(\d+)\] \+ (-?\d+);', s)
        if m:
            base, off = int(m.group(1)), int(m.group(2))
            mw = re.fullmatch(r'ctx\.mmu\.write32\(ea, ctx\.gpr\[(\d+)\]\);', nxt)
            ms = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ea;', nxt2)
            if not (mw and ms):
                raise Unsupported('bad stwu: ' + nxt + ' | ' + nxt2)
            fn.ops.append(('write', int(mw.group(1)), base, off, 32))
            fn.ops.append(('addi', int(ms.group(1)), base, off))
            j += 2
            continue

        # srawi block
        m = re.fullmatch(r'int32_t sv = \(int32_t\)ctx\.gpr\[(\d+)\];', s)
        if m:
            a = int(m.group(1))
            md = re.fullmatch(r'ctx\.gpr\[(\d+)\] = \(uint32_t\)\(sv >> (\d+)\);', nxt)
            mm = re.fullmatch(r'uint32_t ca = \(sv < 0 && \(uint32_t\)\(sv & (\d+)u\) != 0\) \? (\d+) : (\d+);', nxt2)
            mx = re.fullmatch(r'ctx\.xer = \(ctx\.xer & ~\(1u << (\d+)\)\) \| \(ca << (\d+)\);', nxt3)
            if not (md and mm and mx):
                raise Unsupported('bad srawi: ' + nxt + ' | ' + nxt2 + ' | ' + nxt3)
            fn.ops.append(('srawi', int(md.group(1)), a, int(md.group(2)), int(mm.group(1)), int(mx.group(1))))
            j += 3
            continue

        # cr assignment(s), possibly several per line
        if 'ctx.cr[' in s and re.search(r'ctx\.cr\[\d\]\.(lt|gt|eq|so)\s*=', s):
            for part in s.split(';'):
                part = part.strip()
                if not part:
                    continue
                mc = re.fullmatch(r'ctx\.cr\[(\d)\]\.(lt|gt|eq|so) = (.+)', part)
                if not mc:
                    raise Unsupported('bad cr: ' + part)
                field, bit, expr = int(mc.group(1)), mc.group(2), mc.group(3).strip()
                fn.ops.append(('setcr', field, bit, _cmp(expr)))
            continue

        if _meta(s, fn):
            continue
        raise Unsupported('unparsed: ' + s)

    _classify(fn)
    return fn


def _cmp(expr):
    e = expr.strip()
    m = re.fullmatch(r'\(\(int32_t\)ctx\.gpr\[(\d+)\] ([<>]) (-?\d+)\)', e)
    if m:
        return ('c', True, int(m.group(1)), m.group(2), 'imm', imm(m.group(3)))
    m = re.fullmatch(r'\(ctx\.gpr\[(\d+)\] ([<>]) (-?\d+)\)', e)
    if m:
        return ('c', False, int(m.group(1)), m.group(2), 'imm', imm(m.group(3)))
    m = re.fullmatch(r'\(\(int32_t\)ctx\.gpr\[(\d+)\] ([<>]) \(int32_t\)ctx\.gpr\[(\d+)\]\)', e)
    if m:
        return ('c', True, int(m.group(1)), m.group(2), 'reg', int(m.group(3)))
    m = re.fullmatch(r'\(ctx\.gpr\[(\d+)\] ([<>]) ctx\.gpr\[(\d+)\]\)', e)
    if m:
        return ('c', False, int(m.group(1)), m.group(2), 'reg', int(m.group(3)))
    m = re.fullmatch(r'\(\(int32_t\)ctx\.gpr\[(\d+)\] == (-?\d+)\)', e)
    if m:
        return ('c', True, int(m.group(1)), '==', 'imm', imm(m.group(2)))
    m = re.fullmatch(r'\(ctx\.gpr\[(\d+)\] == (-?\d+)\)', e)
    if m:
        return ('c', False, int(m.group(1)), '==', 'imm', imm(m.group(2)))
    m = re.fullmatch(r'\(\(int32_t\)ctx\.gpr\[(\d+)\] == \(int32_t\)ctx\.gpr\[(\d+)\]\)', e)
    if m:
        return ('c', True, int(m.group(1)), '==', 'reg', int(m.group(2)))
    m = re.fullmatch(r'\(ctx\.gpr\[(\d+)\] == ctx\.gpr\[(\d+)\]\)', e)
    if m:
        return ('c', False, int(m.group(1)), '==', 'reg', int(m.group(2)))
    m = re.fullmatch(r'\(ctx\.xer >> (\d+)\) & (\d+)', e)
    if m:
        return ('xer', int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r'ctx\.cr\[(\d)\]\.(lt|gt|eq|so) \^ ctx\.cr\[(\d)\]\.(lt|gt|eq|so)', e)
    if m:
        return ('xor', int(m.group(1)), m.group(2))
    raise Unsupported('bad cr expr: ' + e)


def _meta(s, fn):
    def E(*op):
        fn.ops.append(op)

    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.mmu\.read(8|16|32)\(ctx\.gpr\[(\d+)\] \+ (-?\d+)\);', s)
    if m:
        E('read', int(m.group(1)), int(m.group(3)), int(m.group(4)), int(m.group(2))); return True
    m = re.fullmatch(r'ctx\.mmu\.write(8|16|32)\(ctx\.gpr\[(\d+)\] \+ (-?\d+), (?:\(u?int(?:8|16|32)_t\))?ctx\.gpr\[(\d+)\]\);', s)
    if m:
        E('write', int(m.group(4)), int(m.group(2)), int(m.group(3)), int(m.group(1))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = \(uint32_t\)\(int32_t\)\(int16_t\)ctx\.mmu\.read16\(ctx\.gpr\[(\d+)\] \+ (-?\d+)\);', s)
    if m:
        E('read', int(m.group(1)), int(m.group(2)), int(m.group(3)), 16)
        E('extsh', int(m.group(1)), int(m.group(1))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = \(uint32_t\)\(int32_t\)\(int16_t\)ctx\.gpr\[(\d+)\];', s)
    if m:
        E('extsh', int(m.group(1)), int(m.group(2))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\];', s)
    if m:
        E('mr', int(m.group(1)), int(m.group(2))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.lr;', s)
    if m:
        E('mflr', int(m.group(1))); return True
    m = re.fullmatch(r'ctx\.lr = ctx\.gpr\[(\d+)\];', s)
    if m:
        E('mtlr', int(m.group(1))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = (-?\d+);', s)
    if m:
        E('li', int(m.group(1)), imm(m.group(2))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] \+ (-?\d+);', s)
    if m:
        E('addi', int(m.group(1)), int(m.group(2)), int(m.group(3))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] \+ ctx\.gpr\[(\d+)\];', s)
    if m:
        E('add', int(m.group(1)), int(m.group(2)), int(m.group(3))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] - ctx\.gpr\[(\d+)\];', s)
    if m:
        E('subf', int(m.group(1)), int(m.group(2)), int(m.group(3))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] \* ctx\.gpr\[(\d+)\];', s)
    if m:
        E('mullw', int(m.group(1)), int(m.group(2)), int(m.group(3))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = \(uint32_t\)\(\(int32_t\)ctx\.gpr\[(\d+)\] \* (-?\d+)\);', s)
    if m:
        E('mulli', int(m.group(1)), int(m.group(2)), int(m.group(3))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = \(uint32_t\)\(\(\(uint64_t\)ctx\.gpr\[(\d+)\] \* \(uint64_t\)ctx\.gpr\[(\d+)\]\) >> 32\);', s)
    if m:
        E('mulhwu', int(m.group(1)), int(m.group(2)), int(m.group(3))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] \? ctx\.gpr\[(\d+)\] / ctx\.gpr\[(\d+)\] : 0u;', s)
    if m:
        E('divwu', int(m.group(1)), int(m.group(3)), int(m.group(2))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] \| (~?)ctx\.gpr\[(\d+)\];', s)
    if m:
        E('orc' if m.group(3) else 'or', int(m.group(1)), int(m.group(2)), int(m.group(4))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = ctx\.gpr\[(\d+)\] \| (-?\d+);', s)
    if m:
        E('ori', int(m.group(1)), int(m.group(2)), imm(m.group(3))); return True
    m = re.fullmatch(r'ctx\.gpr\[(\d+)\] = std::rotl\(ctx\.gpr\[(\d+)\], (\d+)\) & (0x[0-9A-Fa-f]+|\d+);', s)
    if m:
        E('rlwinm', int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4), 0)); return True
    return False


# ---------------------------------------------------------------------------
# liveness / def sets
# ---------------------------------------------------------------------------
USE_DEF = {}


def _ud(op):
    k = op[0]
    if k == 'read':
        return {op[2]}, {op[1]}
    if k == 'write':
        return {op[1], op[2]}, set()
    if k in ('mr', 'extsh'):
        return {op[2]}, {op[1]}
    if k == 'mflr':
        return set(), {op[1]}
    if k == 'mtlr':
        return {op[1]}, set()
    if k == 'li':
        return set(), {op[1]}
    if k in ('addi', 'mulli', 'ori', 'rlwinm', 'srawi'):
        return {op[2]}, {op[1]}
    if k in ('add', 'subf', 'mullw', 'mulhwu', 'divwu', 'or', 'orc'):
        return {op[2], op[3]}, {op[1]}
    if k == 'setcr':
        spec = op[3]
        if spec[0] == 'c':
            u = {spec[2]}
            if spec[4] == 'reg':
                u.add(spec[5])
            return u, set()
        return set(), set()
    return set(), set()


def _classify(fn):
    ops = fn.ops
    # basic blocks split at labels and after terminators
    blocks = []          # list of (start, end, terminator op index or None)
    starts = {}
    i = 0
    cur_start = 0
    n = len(ops)
    # collect label positions
    label_at = {}
    for idx, op in enumerate(ops):
        if op[0] == 'label':
            label_at[idx] = op[1]
    # block boundaries: label indices and (terminator index + 1)
    bounds = sorted(set([0] + [i for i in label_at] + [i + 1 for i, op in enumerate(ops) if op[0] in ('b', 'bc', 'bclr', 'retpc', 'blr', 'bctr')]))
    bounds = [b for b in bounds if b <= n]
    # build blocks
    succ = {}
    for bi, b in enumerate(bounds):
        e = bounds[bi + 1] if bi + 1 < len(bounds) else n
        blocks.append((b, e))
    # map label pc -> block index (block starting with that label)
    pc_to_block = {}
    for bi, (b, e) in enumerate(blocks):
        if b < n and ops[b][0] == 'label':
            pc_to_block[ops[b][1]] = bi
    # successors
    block_of_op = {}
    for bi, (b, e) in enumerate(blocks):
        for oi in range(b, e):
            block_of_op[oi] = bi
    for bi, (b, e) in enumerate(blocks):
        s = set()
        term = None
        for oi in range(b, e):
            if ops[oi][0] in ('b', 'bc', 'bclr', 'bl', 'retpc', 'blr', 'bctr'):
                term = ops[oi]
        if term is None:
            if bi + 1 < len(blocks):
                s.add(bi + 1)
        elif term[0] == 'b':
            s.add(pc_to_block[term[1]])
        elif term[0] == 'bc':
            s.add(pc_to_block[term[3]])
            if bi + 1 < len(blocks):
                s.add(bi + 1)
        elif term[0] == 'bl':
            if bi + 1 < len(blocks):
                s.add(bi + 1)
        elif term[0] == 'bclr':
            if bi + 1 < len(blocks):
                s.add(bi + 1)
        # retpc/blr/bctr -> exit (no successors)
        succ[bi] = s
    # backward liveness
    live_in = [set() for _ in blocks]
    live_out = [set() for _ in blocks]
    changed = True
    while changed:
        changed = False
        for bi in range(len(blocks) - 1, -1, -1):
            b, e = blocks[bi]
            u = set(); d = set()
            for oi in range(b, e):
                uu, dd = _ud(ops[oi])
                u |= uu - d
                d |= dd
            out = set()
            for sb in succ.get(bi, ()):
                out |= live_in[sb]
            nin = u | (out - d)
            if nin != live_in[bi] or out != live_out[bi]:
                live_in[bi] = nin; live_out[bi] = out; changed = True
    fn.inputs = set(live_in[0]) if blocks else set()
    defs = set()
    for op in ops:
        _, dd = _ud(op)
        defs |= dd
    fn.defs = defs
    # must-def analysis: a register written on every path to a return does not
    # need to be passed in by value (it can never be propagated unmodified).
    preds = {b: set() for b in range(len(blocks))}
    for b, s in succ.items():
        for d in s:
            preds[d].add(b)
    must_in = [set() for _ in blocks]
    must_out = [set() for _ in blocks]
    changed = True
    while changed:
        changed = False
        for b in range(len(blocks)):
            if b == 0 or not preds[b]:
                mi = set()
            else:
                mi = None
                for p in preds[b]:
                    mi = set(must_out[p]) if mi is None else (mi & must_out[p])
                mi = mi or set()
            bs, be = blocks[b]
            dd = set()
            for oi in range(bs, be):
                _, d = _ud(ops[oi])
                dd |= d
            mo = mi | dd
            if mi != must_in[b] or mo != must_out[b]:
                must_in[b] = mi; must_out[b] = mo; changed = True
    mustdef = None
    for b in range(len(blocks)):
        bs, be = blocks[b]
        isret = any(ops[oi][0] in ('blr', 'retpc', 'bclr') for oi in range(bs, be))
        if not isret:
            continue
        hasbl = any(ops[oi][0] == 'bl' for oi in range(bs, be))
        contrib = set(must_in[b])
        if not hasbl:
            for oi in range(bs, be):
                _, d = _ud(ops[oi])
                contrib |= d
        mustdef = contrib if mustdef is None else (mustdef & contrib)
    fn.mustdef = mustdef or set()
    fn.calls = sorted({op[3] for op in ops if op[0] == 'bl'})
    fn.cr_out = sorted({op[1] for op in ops if op[0] == 'setcr'})
    fn.cr_in = sorted({op[1] for op in ops if op[0] in ('bc', 'bclr')})
    fn.xer_in = any(op[0] == 'setcr' and op[3][0] == 'xer' for op in ops)
    fn.xer_out = any(op[0] == 'srawi' for op in ops)
    fn.lr_in = any(op[0] in ('blr', 'bclr', 'mflr') for op in ops)
    fn.lr_out = any(op[0] == 'mtlr' for op in ops)

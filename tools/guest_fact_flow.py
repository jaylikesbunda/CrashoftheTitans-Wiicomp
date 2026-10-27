"""CFG-wide value and fact propagation for one promoted generated function body.

The module reuses the promotion grammar (``promote_regs``) and works on one
already promoted generated function: ``loc_XXXXXXXX: ;`` labels, ``goto
loc_...`` jumps, ``if (...) goto ...`` branches and the barrier / observer
classification that the promotion pass produces.  It never re-implements
register promotion and it never rewrites FP transport.

Fact families
-------------
GQR
    ``ctx.gqr[i]`` is a guest graphic-quantisation register.  The analysis
    keeps an over-approximated value set per index: a pair of bit masks
    (bits known to be zero, bits known to be one).  This is a *may* domain, so
    joins take the union of the incoming sets and the fixed point is the least
    one.  ``(ctx.gqr[i] & mask) == 0`` is reported *proven true* only when
    every value of the over-approximation satisfies it, *proven false* only
    when some bit of the mask is known to be one, and *unknown* otherwise.

Canonical FP
    Per ``f``/``p`` local the analysis keeps a must-fact: the local still
    holds the value that an F32 (or F64) memory read produced, directly
    (``read_f32`` / ``native_fastmem::read_f32``) or through copies, and has
    not been modified since.  Arithmetic, unknown calls, barriers, polls and
    block calls destroy it; a join keeps it only when every incoming path
    agrees.  The fact describes the *value* of the local, not the current
    content of guest memory.

Address bases
    Per integer local the analysis keeps a set of affine relations
    ``local = root + offset`` (mod 2^32) that hold simultaneously; an empty
    set means the local is an opaque root.  Writing a root removes every
    relation rooted at it, so a relation survives only while its base is
    unchanged.  The module can therefore report, per loop, which bases are
    loop invariant and which constant offsets are accessed from them.  No
    claim is made about RAM residency: only address expressions are carried.

Soundness rules
---------------
* Interior entries are roots.  The function entry and every ``case`` target
  start with no facts, so a fact is never assumed across an entry.
* The must domains run a descending iteration (greatest fixed point) over the
  intersection of the incoming states; the GQR may domain runs the ascending
  iteration (least fixed point) over the union.  Both use a worklist and
  assert that they reach a stable post-fixpoint.
* A barrier, poll, block call, unknown call, return or observation boundary
  invalidates every fact that the promotion pass may have reloaded from
  ``ctx``.
* A definition inside a conditional statement is never *established*; it is
  invalidated instead, because the branch may not be taken.  This keeps the
  block level analysis sound without splitting conditional blocks.
* Goto edges carry the state at the point of the goto, not the state at the
  end of the enclosing block.
* Every fact of a fixed point is justified either by the definition on the
  line that establishes it or by every predecessor, so a fact of the fixed
  point holds on every path from the entry to its point.

API
---
``parse_source(text)``
    The generated functions of a source file, with their units, blocks and
    control flow graph.
``FactFlow(fn).analyse()``
    The three dataflow runs.  ``gqr_test(block, index, mask)`` answers
    *true* / *false* / *unknown* for a format test, ``fp_fact(block, local)``
    returns the canonical FP fact, ``addr_base(block, local)`` returns the
    affine base and ``loop_bases(loop)`` reports the loop invariant bases with
    their constant offsets.
``hoist_gqr_tests(source, prefix='native_gqr_fast', strict_observers=False)``
    The consumer: hoists a loop invariant GQR format test into the loop
    preheader and keeps the original loop as the fallback.  It returns
    ``(new_source, count, details)``.
``analyse_source(source)``
    The static coverage of a whole source file.
"""
import re
import sys
from pathlib import Path

try:
    import promote_regs as P
except ImportError:  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import promote_regs as P

__all__ = [
    'parse_source', 'parse_function', 'FactFlow', 'Loop', 'hoist_gqr_tests',
    'analyse_source', 'find_gqr_sites', 'GqrValue', 'FpFact', 'GQR_TEST',
    'PURE_READ_CALLS', 'GQR_INERT_CALLS', 'GQR_READONLY_CALLS',
    'GQR_SAFE_CALLS',
]

NL = '\n'
MASK32 = 0xFFFFFFFF

# ---------------------------------------------------------------------------
# grammar
# ---------------------------------------------------------------------------

FUNC_HEAD = re.compile(
    r'^(?P<lead>.*?)\bvoid\s+(?P<name>[A-Za-z_]\w*)\s*\(\s*NWII_CTX_PARAM\s+ctx\s*\)\s*\{\s*$')
LABEL_ANY = re.compile(r'^(?P<indent>\s*)(?P<label>[A-Za-z_]\w*):\s*;\s*$')
GOTO_ANY = re.compile(r'\bgoto\s+(?P<target>[A-Za-z_]\w*)\s*;')
CASE_GOTO = re.compile(r'\bcase\s+(?:0x[0-9A-Fa-f]+|\d+)\s*:\s*goto\s+(?P<target>[A-Za-z_]\w*)\s*;')
TERMINATOR_GOTO = re.compile(r'^(?:ctx\.pc\s*=\s*[^;]+;\s*)?goto\s+[A-Za-z_]\w*;\s*$')
TERMINATOR_RETURN = re.compile(r'^(?:ctx\.pc\s*=\s*[^;]+;\s*)?return;\s*$')
INT_LOCAL = re.compile(r'^(?:g\d+|ctrv)$')
FP_LOCAL = re.compile(r'^[fp]\d+$')
CONDITIONAL = re.compile(r'\b(?:if|while|for|switch)\b|\?')

# A canonical F32 / F64 memory read.  ``read_f32`` is the F32 form required
# by the F32 transport; ``read_f64`` is tracked as a separate canonical kind.
FP_READ = re.compile(
    r'^(?P<call>(?:ctx\.mmu\.|native_fastmem::)read_(?P<kind>f32|f64))\s*\((?P<args>.*)\)$')
FP_CHAIN = re.compile(r'\b(?P<targets>(?:[fp]\d+\s*=\s*)+)(?P<value>[^;]+);')
# cheap pre filters: the statement scanners are quadratic on the long spill
# lines of the promoted body, so they only run on the lines that can match
FP_HINT = re.compile(r'[fp]\d+\s*=')
GQR_HINT = re.compile(r'ctx\.gqr\[')

# The GQR format test produced by the PSQ lowering and understood here.
GQR_TEST = re.compile(
    r'^\(\s*ctx\.gqr\[(?P<index>\d+)\]\s*&\s*(?P<mask>0[xX][0-9A-Fa-f]+|\d+)\s*u?\s*\)'
    r'\s*(?P<polarity>==|!=)\s*0\s*$')

# Reads that provably touch neither ctx nor guest memory content.
PURE_READ_CALLS = {
    'ctx.mmu.read8', 'ctx.mmu.read16', 'ctx.mmu.read32', 'ctx.mmu.read64',
    'ctx.mmu.read_f32', 'ctx.mmu.read_f64',
    'native_fastmem.read8', 'native_fastmem.read16', 'native_fastmem.read32',
    'native_fastmem.read64', 'native_fastmem.read_f32', 'native_fastmem.read_f64',
}
# Runtime methods that provably do not write ctx.gqr (checked against the
# production cpu_context.h: require_fpu writes srr0/srr1/msr/pc only).
GQR_INERT_CALLS = {
    'ctx.require_fpu', 'ctx.read_timebase', 'ctx.read_dec', 'ctx.write_dec',
    'ctx.dec_expired',
}
# Runtime methods that read ctx.gqr but never write it.
GQR_READONLY_CALLS = {
    'ctx.psq_load_pair', 'ctx.psq_store_val', 'ctx.native_batch_psq_load_zero',
    'ctx.native_batch_psq_store_zero',
}
# Guest memory writers: they change memory, never ctx.gqr.
GQR_MEMORY_WRITE_CALLS = {
    'ctx.mmu.write8', 'ctx.mmu.write16', 'ctx.mmu.write32', 'ctx.mmu.write64',
    'ctx.mmu.write_f32', 'ctx.mmu.write_f64',
    'native_fastmem.write8', 'native_fastmem.write16', 'native_fastmem.write32',
    'native_fastmem.write64', 'native_fastmem.write_f32', 'native_fastmem.write_f64',
}
# Every call that provably does not write ctx.gqr.
GQR_SAFE_CALLS = (PURE_READ_CALLS | GQR_INERT_CALLS | GQR_READONLY_CALLS |
                  GQR_MEMORY_WRITE_CALLS)

CALL_NAME = re.compile(r'([A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)*)\s*\(')
CALL_KEYWORDS = {'if', 'for', 'while', 'switch', 'return', 'sizeof', 'catch',
               'else', 'do', 'new', 'delete', 'and', 'or', 'not', 'defined'}
LOCAL_REF = re.compile(r'\b(?:g\d+|ctrv|[fp]\d+)\b')
INT_ASSIGN = re.compile(r'\b(?P<local>g\d+|ctrv)\s*=(?!=)\s*(?P<value>[^;]*);')
INT_UPDATE = re.compile(r'\b(?P<local>g\d+|ctrv)\s*(?P<op>\+\+|--|\+=|-=)')
GQR_WRITE = re.compile(r'ctx\.gqr\[(?P<index>\d+)\]\s*(?P<op>=(?!=)|\|=|&=|\^=|\+=|-=|\*=|<<=|>>=)\s*(?P<value>[^;]*)')
GQR_REF = re.compile(r'ctx\.gqr\[(?P<index>\d+)\]')
LITERAL = re.compile(r'^(?:0[xX][0-9A-Fa-f]+|\d+)[uUlL]*$')
INT_LITERAL = re.compile(r'^(?:0[xX][0-9A-Fa-f]+|\d+)[uUlL]*$')

# Address expressions that the guest code builds from a local plus a constant.
ADDR_USE = re.compile(
    r'(?P<call>ctx\.mmu\.(?:read|write)\w*|native_fastmem::(?:read|write)\w*'
    r'|native_guest_fast::write\w*|ctx\.psq_load_pair|ctx\.psq_store_val'
    r'|ctx\.native_batch_psq_\w+)\s*\((?P<args>[^;]*)\)')
ADDR_OPERAND = re.compile(r'^(?P<base>[A-Za-z_]\w*)\s*(?P<op>[+-])\s*(?P<const>-?\d+)$')
BARE_OPERAND = re.compile(r'^(?P<base>[A-Za-z_]\w*)$')


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def strip_strings(code):
    """Blank out string and character literals so brace counting is exact."""
    out = []
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        if c in '"\'':
            quote = c
            out.append(' ')
            i += 1
            while i < n:
                if code[i] == '\\':
                    i += 2
                    continue
                if code[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def scope_end(text, at):
    """Index just past the '}' that matches the '{' at ``at``."""
    depth = 1
    i = at + 1
    n = len(text)
    while i < n and depth:
        c = text[i]
        if c == '/' and text[i + 1:i + 2] == '/':
            j = text.find('\n', i)
            i = n if j < 0 else j
            continue
        if c == '/' and text[i + 1:i + 2] == '*':
            j = text.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in '"\'':
            quote = c
            i += 1
            while i < n:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    if depth:
        raise ValueError('unbalanced scope')
    return i


def literal_value(text):
    """Integer value of a C integer literal, or None."""
    t = text.strip()
    if not LITERAL.match(t):
        return None
    t = t.rstrip('uUlL')
    try:
        return int(t, 0) & MASK32
    except ValueError:
        return None


def call_names(code):
    """Every call-like name of a line, without the C++ keywords."""
    return [n for n in CALL_NAME.findall(code) if n not in CALL_KEYWORDS]


def is_conditional(code):
    return bool(CONDITIONAL.search(code))


# ---------------------------------------------------------------------------
# parsed representation
# ---------------------------------------------------------------------------

class Line(object):
    __slots__ = ('text', 'code', 'kind', 'gotos', 'conditional', 'index')

    def __init__(self, text, index):
        self.text = text
        self.code = P.code_of(text)
        self.kind = P.classify(self.code)
        self.gotos = GOTO_ANY.findall(self.code)
        self.conditional = is_conditional(self.code)
        self.index = index


class Block(object):
    """A run of lines of one unit that has a single outgoing state."""

    __slots__ = ('unit', 'lines', 'succ', 'pred', 'gotos', 'terminator')

    def __init__(self, unit, lines):
        self.unit = unit
        self.lines = lines
        self.succ = []
        self.pred = []
        self.gotos = []
        self.terminator = False


class Unit(object):
    """A label delimited region of the function body."""

    __slots__ = ('index', 'label', 'lines', 'blocks', 'first')

    def __init__(self, index, label):
        self.index = index
        self.label = label
        self.lines = []
        self.blocks = []
        self.first = 0

    def text(self):
        return NL.join(self.lines)


class Function(object):
    __slots__ = ('name', 'entry', 'head', 'body', 'units', 'labels', 'blocks',
                 'roots', 'case_targets', 'source', 'first_line', 'last_line')

    def __init__(self):
        self.name = ''
        self.entry = 0
        self.head = []
        self.body = []
        self.units = []
        self.labels = {}
        self.blocks = []
        self.roots = []
        self.case_targets = []
        self.source = ''
        self.first_line = 0
        self.last_line = 0


def _split_units(body, offset=0):
    """Split a body into units at label lines.

    ``Unit.first`` is the index of the unit's first line inside the body, so
    that a text edit can be mapped back to an absolute source line.
    """
    units = []
    cur = Unit(0, None)
    cur.first = 0
    for i, raw in enumerate(body):
        m = LABEL_ANY.match(raw)
        if m:
            units.append(cur)
            cur = Unit(len(units), m.group('label'))
            cur.first = i + 1
            continue
        cur.lines.append(raw)
    units.append(cur)
    # renumber after the final append
    for i, u in enumerate(units):
        u.index = i
    return units


def _split_blocks(unit):
    """Split a unit into blocks at every line that carries a goto."""
    blocks = []
    cur = []
    for i, raw in enumerate(unit.lines):
        line = Line(raw, i)
        cur.append(line)
        if line.gotos:
            blocks.append(Block(unit, cur))
            cur = []
    if cur:
        blocks.append(Block(unit, cur))
    unit.blocks = blocks
    for b in blocks:
        last = b.lines[-1]
        b.terminator = bool(TERMINATOR_GOTO.match(last.code.strip()) or
                             TERMINATOR_RETURN.match(last.code.strip()))
        for g in last.gotos:
            b.gotos.append(g)
    return blocks


def parse_function(lines, head_index):
    """Parse the function whose head is at ``head_index`` of ``lines``."""
    head = lines[head_index]
    m = FUNC_HEAD.match(head)
    if not m:
        return None
    depth = strip_strings(P.code_of(head)).count('{') - strip_strings(P.code_of(head)).count('}')
    end = None
    for i in range(head_index + 1, len(lines)):
        code = strip_strings(P.code_of(lines[i]))
        depth += code.count('{') - code.count('}')
        if depth <= 0:
            end = i
            break
    if end is None:
        return None
    fn = Function()
    fn.name = m.group('name')
    fn.head = lines[head_index:end + 1]
    fn.body = lines[head_index + 1:end]
    fn.source = NL.join(lines[head_index:end + 1])
    fn.first_line = head_index
    fn.last_line = end
    fn.units = _split_units(fn.body, head_index + 1)
    for u in fn.units:
        _split_blocks(u)
        for b in u.blocks:
            fn.blocks.append(b)
    fn.labels = {}
    for u in fn.units:
        if u.label:
            fn.labels[u.label] = u
    # entry address from the trailing hex of the function name
    tail = re.search(r'([0-9A-Fa-f]{6,8})$', fn.name)
    if tail:
        try:
            fn.entry = int(tail.group(1), 16)
        except ValueError:
            fn.entry = 0
    # interior entries: every case target of the function head
    head_text = NL.join(fn.head)
    for m2 in CASE_GOTO.finditer(head_text):
        fn.case_targets.append(m2.group('target'))
    return fn


def parse_source(source):
    """Parse every generated function of ``source``.

    Returns a list of ``(start_line, end_line, Function)`` triples.
    """
    lines = source.split(NL)
    out = []
    i = 0
    while i < len(lines):
        if FUNC_HEAD.match(lines[i]):
            fn = parse_function(lines, i)
            if fn is not None:
                out.append((fn.first_line, fn.last_line, fn))
                i = fn.last_line + 1
                continue
        i += 1
    return out


# ---------------------------------------------------------------------------
# control flow
# ---------------------------------------------------------------------------

def build_cfg(fn):
    """Build the block level control flow graph of ``fn``."""
    for b in fn.blocks:
        b.succ = []
        b.pred = []
    rebuild_successors(fn)
    # roots: the first block of the function and every case target
    roots = []
    if fn.blocks:
        roots.append(fn.blocks[0])
    for name in fn.case_targets:
        unit = fn.labels.get(name)
        if unit is not None and unit.blocks and unit.blocks[0] not in roots:
            roots.append(unit.blocks[0])
    fn.roots = roots
    return fn


def _unit_after(fn, unit):
    idx = unit.index + 1
    if idx < len(fn.units):
        return fn.units[idx]
    return None



def _block_after(fn, block):
    """The text successor of ``block`` (fallthrough), if any."""
    unit = block.unit
    pos = unit.blocks.index(block)
    if pos + 1 < len(unit.blocks):
        return unit.blocks[pos + 1]
    nxt = _unit_after(fn, unit)
    if nxt is None:
        return None
    return _first_block(fn, nxt)


def _first_block(fn, unit):
    """The first block of a unit, skipping the units without any code."""
    index = unit.index
    while index < len(fn.units):
        candidate = fn.units[index]
        if candidate.blocks:
            return candidate.blocks[0]
        index += 1
    return None


def rebuild_successors(fn):
    """Recompute the successors with the unit level fallthrough rule."""
    blocks = fn.blocks
    for b in blocks:
        b.succ = []
        b.pred = []
    for i, b in enumerate(blocks):
        for g in b.gotos:
            unit = fn.labels.get(g)
            if unit is None:
                continue
            dest = _first_block(fn, unit)
            if dest is not None and dest not in b.succ:
                b.succ.append(dest)
        if not b.terminator:
            nxt = _block_after(fn, b)
            if nxt is not None and nxt not in b.succ:
                b.succ.append(nxt)
    for b in blocks:
        for s in b.succ:
            s.pred.append(b)
    return fn


class Loop(object):
    """A natural loop with its header, latch, preheader and body blocks."""

    __slots__ = ('header', 'latch', 'blocks', 'preheader', 'back_edges', 'units')

    def __init__(self, header, latch, blocks, back_edges):
        self.header = header
        self.latch = latch
        self.blocks = blocks
        self.back_edges = back_edges
        self.preheader = None
        self.units = []

    def contains(self, block):
        return block in self.blocks

    def __repr__(self):
        return '<Loop header=%s size=%d>' % (self.header.unit.label, len(self.blocks))


def find_loops(fn):
    """Natural loops of ``fn`` (single back edge loops only)."""
    blocks = fn.blocks
    n = len(blocks)
    index = {}
    for i, b in enumerate(blocks):
        index[id(b)] = i
    colour = [0] * n
    order = []
    back_edges = []
    # iterative DFS
    for start in range(n):
        if colour[start]:
            continue
        stack = [(start, 0)]
        colour[start] = 1
        while stack:
            node, pos = stack[-1]
            succs = blocks[node].succ
            if pos < len(succs):
                stack[-1] = (node, pos + 1)
                nxt = index[id(succs[pos])]
                if colour[nxt] == 0:
                    colour[nxt] = 1
                    stack.append((nxt, 0))
                elif colour[nxt] == 1:
                    back_edges.append((node, nxt))
            else:
                colour[node] = 2
                order.append(node)
                stack.pop()
    loops = []
    for (tail, head) in back_edges:
        members = {head}
        work = [tail]
        while work:
            x = work.pop()
            if x in members:
                continue
            members.add(x)
            for p in blocks[x].pred:
                if p not in members:
                    work.append(index[id(p)])
        header = blocks[head]
        merged = None
        for lp in loops:
            if lp.header is header:
                merged = lp
                break
        if merged is None:
            merged = Loop(header, blocks[tail], set(), [])
            loops.append(merged)
        merged.blocks |= set(blocks[i] for i in members)
        merged.back_edges.append((tail, head))
        if tail > index[id(merged.latch)]:
            merged.latch = blocks[tail]
    # keep only loops with a single back edge, they are the only ones the
    # consumer can clone without changing the backedge semantics
    loops = [lp for lp in loops if len(lp.back_edges) == 1]
    for lp in loops:
        units = []
        for b in lp.blocks:
            if b.unit not in units:
                units.append(b.unit)
        units.sort(key=lambda u: u.index)
        lp.units = units
        # preheader: the unique outside predecessor of the header
        outside = [p for p in lp.header.pred if not lp.contains(p)]
        if len(outside) == 1:
            lp.preheader = outside[0]
    return loops


# ---------------------------------------------------------------------------
# fact domains
# ---------------------------------------------------------------------------

class GqrValue(object):
    """Over-approximated value set of one ctx.gqr index."""

    __slots__ = ('zero', 'one')

    def __init__(self, zero=0, one=0):
        self.zero = zero
        self.one = one

    def __eq__(self, other):
        return isinstance(other, GqrValue) and self.zero == other.zero and self.one == other.one

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.zero, self.one))

    def __repr__(self):
        return 'GqrValue(zero=%#x,one=%#x)' % (self.zero, self.one)

    @staticmethod
    def top():
        return GqrValue(0, 0)

    @staticmethod
    def exact(value):
        value &= MASK32
        return GqrValue((~value) & MASK32, value)

    def union(self, other):
        """Union of two value sets, over-approximated by mask intersection."""
        if other is None:
            return GqrValue.top()
        return GqrValue(self.zero & other.zero, self.one & other.one)

    def test(self, mask):
        """'true', 'false' or 'unknown' for (value & mask) == 0."""
        mask &= MASK32
        if mask & self.one:
            return 'false'
        if mask & ~self.zero & MASK32:
            return 'unknown'
        return 'true'

    def known_word(self):
        if (self.zero | self.one) == MASK32 and not (self.zero & self.one):
            return self.one
        return None


class FpFact(object):
    """Must-fact: a local still holds a canonical value read from memory."""

    __slots__ = ('kind', 'src', 'direct')

    def __init__(self, kind, src, direct):
        self.kind = kind
        self.src = src
        self.direct = direct

    def __eq__(self, other):
        return (isinstance(other, FpFact) and self.kind == other.kind and
                self.src == other.src and self.direct == other.direct)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.kind, self.src, self.direct))

    def __repr__(self):
        return 'FpFact(%s,%r,direct=%s)' % (self.kind, self.src, self.direct)


class _Top(object):
    """Sentinel for the top of a must lattice."""

    __slots__ = ()

    def __repr__(self):
        return 'TOP'


TOP = _Top()


# ---------------------------------------------------------------------------
# the analysis
# ---------------------------------------------------------------------------

class FactFlow(object):
    """Value and fact propagation over the CFG of one generated function.

    Three independent dataflow runs share the same block graph:

    * the GQR domain is a *may* domain (an over-approximated value set per
      index).  It joins with the union of the incoming sets and its fixed
      point is the least one.
    * the canonical FP domain and the affine address domain are *must*
      domains.  They join with the intersection of the incoming sets and
      their fixed point is the greatest one, reached by a descending
      iteration that starts at the top of the lattice.
    """

    def __init__(self, fn):
        self.fn = fn
        build_cfg(fn)
        rebuild_successors(fn)
        self.loops = find_loops(fn)
        self.gqr_indices = set()
        self.fp_locals = set()
        self.int_locals = set()
        self._scan()
        self.gqr_in = {}
        self.gqr_out = {}
        self.fp_in = {}
        self.fp_out = {}
        self.addr_in = {}
        self.addr_out = {}
        self._line_cache = {}
        self._analysed = False

    # -- setup ------------------------------------------------------------
    def _scan(self):
        """Collect the index sets of the three domains."""
        pairs = set()
        for u in self.fn.units:
            for raw in u.lines:
                code = P.code_of(raw)
                for m in GQR_REF.finditer(code):
                    self.gqr_indices.add(int(m.group('index')))
                for m in FP_CHAIN.finditer(code):
                    value = m.group('value').strip()
                    read = FP_READ.match(value)
                    if read is not None:
                        args = _split_args(read.group('args'))
                        addr = (args[0] if read.group('call').startswith('ctx.mmu')
                                else (args[1] if len(args) > 1 else ''))
                        pairs.add((read.group('kind'), _normalise_addr(addr)))
                for m in LOCAL_REF.finditer(code):
                    name = m.group(0)
                    if INT_LOCAL.match(name):
                        self.int_locals.add(name)
                    elif FP_LOCAL.match(name):
                        self.fp_locals.add(name)
        # the direct variant and the copy variant of one source are adjacent
        # so that a copy is a bit permutation of the source mask
        self.fp_pairs = sorted(pairs)
        self.fp_bits = {}
        self.fp_even = 0
        self.fp_odd = 0
        for index, pair in enumerate(self.fp_pairs):
            self.fp_bits[(pair[0], pair[1], True)] = 1 << (2 * index)
            self.fp_bits[(pair[0], pair[1], False)] = 1 << (2 * index + 1)
            self.fp_even |= 1 << (2 * index)
            self.fp_odd |= 1 << (2 * index + 1)
        self.fp_top = (1 << (2 * len(self.fp_pairs))) - 1

    # -- GQR --------------------------------------------------------------
    def _gqr_unsafe(self, code):
        """True when a call of the line may write ctx.gqr."""
        for name in call_names(code):
            if name in GQR_SAFE_CALLS:
                continue
            return True
        return False

    def _gqr_observer(self, code):
        """True when a call of the line may observe ctx.gqr."""
        for name in call_names(code):
            if name in PURE_READ_CALLS or name in GQR_INERT_CALLS:
                continue
            return True
        return False

    def _gqr_transfer(self, state, line):
        code = line.code
        out = dict(state)
        conditional = line.conditional
        if not GQR_HINT.search(code):
            if self._gqr_unsafe(code):
                for index in self.gqr_indices:
                    out[index] = GqrValue.top()
            return out
        for m in GQR_WRITE.finditer(code):
            index = int(m.group('index'))
            op = m.group('op')
            value = literal_value(m.group('value'))
            if op == '=' and value is not None:
                new = GqrValue.exact(value)
            else:
                new = GqrValue.top()
            if conditional and op == '=' and value is not None:
                # the write may not happen: keep the old values as well
                old = out.get(index, GqrValue.top())
                new = old.union(new)
            out[index] = new
        if self._gqr_unsafe(code):
            for index in self.gqr_indices:
                out[index] = GqrValue.top()
        return out

    # -- canonical FP -----------------------------------------------------
    def _fp_barrier(self, line):
        return line.kind in ('barrier', 'cbarrier', 'poll', 'blockcall')

    def _fp_transfer(self, state, line):
        if self._fp_barrier(line):
            # the promotion reloads the promoted locals from ctx here
            return {}
        out = dict(state)
        if not FP_HINT.search(line.code):
            return out
        for m in FP_CHAIN.finditer(line.code):
            targets = re.findall(r'[fp]\d+', m.group('targets'))
            value = m.group('value').strip()
            read = FP_READ.match(value)
            mask = 0
            if read is not None:
                args = _split_args(read.group('args'))
                addr = (args[0] if read.group('call').startswith('ctx.mmu')
                        else (args[1] if len(args) > 1 else ''))
                mask = self.fp_bits.get(
                    (read.group('kind'), _normalise_addr(addr), True), 0)
            elif FP_LOCAL.match(value):
                source = out.get(value, 0)
                # a copy keeps the source but is never a direct read
                mask = ((source & self.fp_even) << 1) | (source & self.fp_odd)
            if line.conditional:
                mask = 0
            for t in targets:
                out[t] = mask
        return out

    # -- affine address bases -------------------------------------------
    def _addr_transfer(self, state, line):
        out = dict(state)
        code = line.code
        conditional = line.conditional
        for m in INT_ASSIGN.finditer(code):
            local = m.group('local')
            value = m.group('value').strip()
            rel = self._addr_resolve(out, value, local)
            if conditional:
                rel = frozenset()
            # a write invalidates every relation rooted at the written local
            for other in list(out):
                if other == local:
                    continue
                rels = out[other]
                if rels is TOP:
                    continue
                kept = frozenset(r for r in rels if r[0] != local)
                if kept != rels:
                    out[other] = kept
            out[local] = rel
        for m in INT_UPDATE.finditer(code):
            local = m.group('local')
            for other in list(out):
                if other == local:
                    continue
                rels = out[other]
                if rels is TOP:
                    continue
                kept = frozenset(r for r in rels if r[0] != local)
                if kept != rels:
                    out[other] = kept
            out[local] = frozenset()
        return out

    def _addr_resolve(self, state, value, local=None):
        """Affine relations of ``value`` given the current state."""
        value = value.strip()
        if INT_LITERAL.match(value):
            return frozenset()
        m = ADDR_OPERAND.match(value)
        if m is not None and INT_LOCAL.match(m.group('base')):
            base = m.group('base')
            const = int(m.group('const'))
            if m.group('op') == '-':
                const = -const
            return self._addr_shift(state.get(base), base, const, local)
        m = BARE_OPERAND.match(value)
        if m is not None and INT_LOCAL.match(m.group('base')):
            base = m.group('base')
            rels = state.get(base)
            if rels is None:
                return frozenset()
            if rels is TOP:
                return TOP
            return frozenset(rels)
        return frozenset()

    def _addr_shift(self, rels, base, const, local=None):
        if rels is None:
            rels = frozenset()
        if rels is TOP:
            return TOP
        if not rels:
            if local is not None and base == local:
                # the local is defined from itself: the relation is broken
                return frozenset()
            return frozenset(((base, const & MASK32),))
        return frozenset((r, (o + const) & MASK32) for (r, o) in rels)

    # -- dataflow ---------------------------------------------------------
    def _gqr_join(self, states):
        out = {}
        for state in states:
            for index, value in state.items():
                if index in out:
                    out[index] = out[index].union(value)
                else:
                    out[index] = value
        return out

    def _fp_join(self, states):
        if not states:
            return {}
        keys = set(states[0])
        for s in states[1:]:
            keys &= set(s)
        out = {}
        for key in keys:
            value = states[0][key]
            for s in states[1:]:
                value &= s[key]
            out[key] = value
        return out

    def _addr_join(self, states):
        if not states:
            return {}
        keys = set(states[0])
        for s in states[1:]:
            keys &= set(s)
        out = {}
        for key in keys:
            value = states[0][key]
            for s in states[1:]:
                if value is TOP:
                    value = s[key]
                    continue
                if s[key] is TOP:
                    continue
                value = value & s[key]
            out[key] = value
        return out

    def _run(self, transfer, join, boundary, initial, ins, outs):
        """Worklist dataflow.

        The iteration is monotone: the may domain grows from the bottom and
        the must domains shrink from the top, so the loop reaches a stable
        post-fixpoint.  A root (the function entry or a case target) is
        pinned to the boundary state, so no fact survives an interior entry.
        """
        blocks = self.fn.blocks
        roots = set(id(b) for b in self.fn.roots)
        for b in blocks:
            ins[id(b)] = initial()
            outs[id(b)] = initial()
        work = list(blocks)
        queued = set(id(b) for b in blocks)
        updates = 0
        while work:
            b = work.pop()
            queued.discard(id(b))
            updates += 1
            if updates > 2000000:
                raise AssertionError('dataflow iteration did not converge')
            if id(b) in roots:
                new_in = boundary()
            else:
                incoming = [outs[id(p)] for p in b.pred]
                new_in = join(incoming) if incoming else boundary()
            new_out = transfer(b, new_in)
            if new_in != ins[id(b)] or new_out != outs[id(b)]:
                ins[id(b)] = new_in
                outs[id(b)] = new_out
                for s in b.succ:
                    if id(s) not in queued:
                        work.append(s)
                        queued.add(id(s))
        return updates

    def _transfer_blocks(self, transfer, state, block):
        """Sweep the lines of one block."""
        for line in block.lines:
            state = transfer(state, line)
        return state

    def analyse(self):
        if self._analysed:
            return self
        # GQR: may domain, ascending least fixed point
        self.gqr_rounds = self._run(
            lambda b, s: self._transfer_blocks(self._gqr_transfer, s, b),
            self._gqr_join,
            lambda: dict((i, GqrValue.top()) for i in self.gqr_indices),
            lambda: {},
            self.gqr_in, self.gqr_out)
        # canonical FP: must domain, descending greatest fixed point
        self.fp_rounds = self._run(
            lambda b, s: self._transfer_blocks(self._fp_transfer, s, b),
            self._fp_join,
            lambda: {},
            lambda: dict((k, self.fp_top) for k in self.fp_locals),
            self.fp_in, self.fp_out)
        # affine address bases: must domain
        self.addr_rounds = self._run(
            lambda b, s: self._transfer_blocks(self._addr_transfer, s, b),
            self._addr_join,
            lambda: {},
            lambda: dict((k, TOP) for k in self.int_locals),
            self.addr_in, self.addr_out)
        self._analysed = True
        return self

    # -- queries ----------------------------------------------------------
    def gqr_state(self, block):
        self.analyse()
        return self.gqr_out.get(id(block), {})

    def gqr_test(self, block, index, mask):
        """'true', 'false' or 'unknown' for (ctx.gqr[index] & mask) == 0."""
        state = self.gqr_state(block)
        value = state.get(index)
        if value is None:
            return 'unknown'
        return value.test(mask)

    def gqr_loop_invariant(self, loop, index):
        """True when ctx.gqr[index] cannot change inside ``loop``.

        The proof needs two things: no explicit write to that index and no
        call that may write any ctx.gqr index.  A call that only reads
        ctx.gqr does not change the value and does not break invariance.
        """
        for b in loop.blocks:
            for line in b.lines:
                for m in GQR_WRITE.finditer(line.code):
                    if int(m.group('index')) == index:
                        return False
                if self._gqr_unsafe(line.code):
                    return False
        return True

    def gqr_loop_observer(self, loop):
        """True when a call inside ``loop`` may observe ctx.gqr."""
        for b in loop.blocks:
            for line in b.lines:
                if self._gqr_observer(line.code):
                    return True
        return False

    def fp_state(self, block):
        self.analyse()
        return self.fp_out.get(id(block), {})

    def fp_fact(self, block, local):
        """The single canonical fact of ``local``, or None."""
        mask = self.fp_state(block).get(local)
        if not mask or mask & (mask - 1):
            return None
        index = mask.bit_length() - 1
        direct = not (index & 1)
        pair = self.fp_pairs[index // 2]
        return FpFact(pair[0], pair[1], direct)

    def addr_state(self, block):
        self.analyse()
        return self.addr_out.get(id(block), {})

    def addr_base(self, block, local):
        """The single affine relation of ``local``, or None."""
        rels = self.addr_state(block).get(local)
        if not rels or rels is TOP:
            return None
        if len(rels) != 1:
            return None
        return next(iter(rels))

    def address_uses(self, block):
        """Every (base local, constant offset) address expression of a block."""
        out = []
        for line in block.lines:
            for m in ADDR_USE.finditer(line.code):
                args = _split_args(m.group('args'))
                call = m.group('call')
                if call.startswith('ctx.mmu.'):
                    operand = args[0] if args else ''
                elif call.startswith('native_fastmem.') or call.startswith('native_guest_fast.'):
                    operand = args[1] if len(args) > 1 else ''
                elif call == 'ctx.psq_load_pair':
                    operand = args[0] if args else ''
                elif call == 'ctx.psq_store_val':
                    operand = args[2] if len(args) > 2 else ''
                else:
                    operand = args[0] if args else ''
                base, off = _operand(operand)
                if base is not None:
                    out.append((base, off))
        return out

    def loop_bases(self, loop):
        """Loop invariant bases and the constant offsets accessed from them."""
        written = set()
        for b in loop.blocks:
            for line in b.lines:
                for m in INT_ASSIGN.finditer(line.code):
                    written.add(m.group('local'))
                for m in INT_UPDATE.finditer(line.code):
                    written.add(m.group('local'))
        offsets = {}
        bases = set()
        for b in loop.blocks:
            for base, off in self.address_uses(b):
                state = self.addr_state(b)
                rels = state.get(base)
                root = base
                offset = off
                if rels and rels is not TOP and len(rels) == 1:
                    r, o = next(iter(rels))
                    root = r
                    offset = (o + off) & MASK32
                bases.add(root)
                offsets.setdefault(root, set()).add(offset)
        report = {}
        for root in sorted(bases):
            report[root] = {
                'invariant': root not in written,
                'offsets': sorted(offsets.get(root, ())),
                'written': root in written,
            }
        return report

    # -- per line states --------------------------------------------------
    def line_states(self, block):
        """State before every line of ``block`` (cached)."""
        key = id(block)
        cached = self._line_cache.get(key)
        if cached is not None:
            return cached
        self.analyse()
        gqr = dict(self.gqr_in.get(key, {}))
        fp = dict(self.fp_in.get(key, {}))
        addr = dict(self.addr_in.get(key, {}))
        out = []
        for line in block.lines:
            out.append((dict(gqr), dict(fp), dict(addr)))
            gqr = self._gqr_transfer(gqr, line)
            fp = self._fp_transfer(fp, line)
            addr = self._addr_transfer(addr, line)
        self._line_cache[key] = out
        return out


def _split_args(text):
    args = []
    depth = 0
    start = 0
    for i, c in enumerate(text):
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ',' and depth == 0:
            args.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        args.append(tail)
    return args


def _normalise_addr(text):
    return ' '.join(text.split())


def _operand(text):
    """(base local, constant offset) of an address expression."""
    text = text.strip()
    m = ADDR_OPERAND.match(text)
    if m is not None:
        return m.group('base'), (int(m.group('const')) if m.group('op') == '+' else -int(m.group('const')))
    m = BARE_OPERAND.match(text)
    if m is not None:
        return m.group('base'), 0
    return None, None


# ---------------------------------------------------------------------------
# the consumer: hoist loop invariant GQR format tests
# ---------------------------------------------------------------------------

class _Decline(Exception):
    """Internal signal: this loop does not qualify."""


class GqrSite(object):
    """A GQR format test found in the function body."""

    __slots__ = ('unit', 'line', 'index', 'mask', 'polarity', 'condition')

    def __init__(self, unit, line, index, mask, polarity, condition):
        self.unit = unit
        self.line = line
        self.index = index
        self.mask = mask
        self.polarity = polarity
        self.condition = condition

    def __repr__(self):
        return '<GqrSite %s mask=%#x in %s>' % (self.index, self.mask, self.unit.label)


def find_gqr_sites(fn):
    """Every GQR format test of the function, in text order."""
    sites = []
    for u in fn.units:
        for i, raw in enumerate(u.lines):
            code = P.code_of(raw).strip()
            m = re.match(r'^if\s*\((?P<cond>.*)\)\s*\{$', code)
            if not m:
                continue
            cond = m.group('cond').strip()
            while cond.startswith('(') and cond.endswith(')') and _balanced(cond[1:-1]):
                cond = cond[1:-1].strip()
            test = GQR_TEST.match(cond)
            if test is None:
                continue
            sites.append(GqrSite(u, i, int(test.group('index')),
                                 int(test.group('mask'), 0), test.group('polarity'), cond))
    return sites


def _balanced(text):
    depth = 0
    for c in text:
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _block_terminates(text, at):
    """Index just past the '}' that closes the block opened at ``at``."""
    depth = 0
    i = at
    n = len(text)
    while i < n:
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _replace_site(text, at):
    """Replace ``if(cond){A}else{B}`` with ``{A}``; ``at`` is the 'if' index."""
    open_at = text.find('{', at)
    if open_at < 0:
        return None
    depth = 1
    i = open_at + 1
    n = len(text)
    while i < n and depth:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    if depth:
        return None
    close_at = i - 1
    stop = i
    j = i
    while j < n and text[j] in ' \t\r\n':
        j += 1
    if text[j:j + 4] == 'else':
        else_open = text.find('{', j)
        if else_open < 0:
            return None
        d = 1
        k = else_open + 1
        while k < n and d:
            if text[k] == '{':
                d += 1
            elif text[k] == '}':
                d -= 1
            k += 1
        if d:
            return None
        stop = k
    true_branch = text[open_at + 1:close_at]
    return text[:at] + '{' + true_branch + '}' + text[stop:]


def _line_offset(lines, index):
    offset = 0
    for i in range(index):
        offset += len(lines[i]) + 1
    return offset


def _clone_labels(loop, prefix, taken=()):
    """Fresh label names for the clone of ``loop``."""
    names = {}
    used = set(taken)
    for u in loop.units:
        if u.label:
            used.add(u.label)
    for u in loop.units:
        if not u.label:
            continue
        if u is loop.units[0]:
            name = prefix
        else:
            name = prefix + '_' + u.label
        candidate = name
        serial = 0
        while candidate in used:
            serial += 1
            candidate = name + '_x%d' % serial
        used.add(candidate)
        names[u.label] = candidate
    return names


def _clone_text(loop, sites, prefix, taken=()):
    """Text of the cloned loop with renamed labels and hoisted tests."""
    units = loop.units
    names = _clone_labels(loop, prefix, taken)
    site_by_unit = {}
    for site in sites:
        site_by_unit.setdefault(id(site.unit), []).append(site)
    chunks = []
    for u in units:
        lines = list(u.lines)
        text = NL.join(lines)
        for site in sorted(site_by_unit.get(id(u), ()), key=lambda s: -s.line):
            offset = _line_offset(lines, site.line)
            code = P.code_of(lines[site.line])
            at = offset + code.find('if')
            if at < 0:
                return None
            replaced = _replace_site(text, at)
            if replaced is None:
                return None
            text = replaced
        def repl(m, names=names):
            target = m.group('target')
            return 'goto %s;' % names.get(target, target)
        text = GOTO_ANY.sub(repl, text)
        if u.label:
            text = '%s: ;' % names[u.label] + (NL if text else '') + text
        chunks.append(text)
    return NL.join(chunks), names


def _eligible_loop(flow, loop):
    """Check the structural preconditions of the hoist transform."""
    if loop.preheader is None:
        return None, 'no single preheader'
    if len(loop.back_edges) != 1:
        return None, 'multiple back edges'
    header = loop.header
    # exactly one entry edge from outside the loop
    for b in loop.blocks:
        for p in b.pred:
            if not loop.contains(p):
                if b is not header:
                    return None, 'interior entry'
                if p is not loop.preheader:
                    return None, 'several preheaders'
    if header.pred.count(loop.preheader) != 1:
        return None, 'several entry edges'
    # the preheader must enter the loop exactly once
    entries = [s for s in loop.preheader.succ if loop.contains(s)]
    if len(entries) != 1 or entries[0] is not header:
        return None, 'preheader enters the loop more than once'
    # no interior entry through the function entry switch
    for b in flow.fn.roots:
        if loop.contains(b):
            return None, 'interior entry'
    # the loop must be a contiguous run of units, headed by its header unit
    units = loop.units
    if not units or units[0] is not header.unit:
        return None, 'loop is not a contiguous unit range'
    for a, b in zip(units, units[1:]):
        if b.index != a.index + 1:
            return None, 'loop is not a contiguous unit range'
    # the last unit must end the loop with the back edge
    last = units[-1]
    if not last.blocks or last.blocks[-1] is not loop.latch:
        return None, 'back edge is not the last block of the loop'
    # a fall through out of the loop needs an explicit exit, because the
    # clone is placed between the loop and its exit
    if not loop.latch.terminator:
        following = None
        for u in flow.fn.units:
            if u.index == last.index + 1:
                following = u
        if following is None:
            return None, 'the loop has no explicit exit unit'
        if not following.label:
            return None, 'the loop exit unit has no label'
    return loop.preheader, ''


def hoist_gqr_tests(source, prefix='native_gqr_fast', strict_observers=False):
    """Hoist loop invariant GQR format tests out of their loop.

    Returns ``(new_source, count, details)``.  ``new_source`` is the source
    unchanged when no loop qualifies.  Every transformed loop keeps a full
    original fallback: the original text of the loop is never modified, the
    fast path is a renamed clone whose format test is replaced by the proven
    true branch, and the test itself is evaluated once in the preheader.

    ``strict_observers`` demands that no call inside the loop may even read
    ctx.gqr.  The default only demands the value proof: no write to that
    index and no call that may write any ctx.gqr index.
    """
    parsed = parse_source(source)
    if not parsed:
        return source, 0, []
    lines = source.split(NL)
    details = []
    total = 0
    edits = []
    for start_line, end_line, fn in parsed:
        flow = FactFlow(fn).analyse()
        sites = find_gqr_sites(fn)
        if not sites:
            continue
        for loop in flow.loops:
            loop_sites = [s for s in sites if any(s.unit is u for u in loop.units)]
            name = loop.header.unit.label or '(entry)'
            if not loop_sites:
                continue
            indexes = set(s.index for s in loop_sites)
            masks = set(s.mask for s in loop_sites)
            if len(indexes) != 1 or len(masks) != 1:
                details.append({'loop': name, 'declined': 'several format tests'})
                continue
            index = indexes.pop()
            mask = masks.pop()
            if not flow.gqr_loop_invariant(loop, index):
                details.append({'loop': name, 'index': index,
                                'declined': 'ctx.gqr[%d] may change in the loop' % index})
                continue
            if strict_observers and flow.gqr_loop_observer(loop):
                details.append({'loop': name, 'index': index,
                                'declined': 'an observing call is inside the loop'})
                continue
            preheader, why = _eligible_loop(flow, loop)
            if preheader is None:
                details.append({'loop': name, 'index': index, 'declined': why})
                continue
            block = preheader
            unit = block.unit
            begin = len(unit.lines) - len(block.lines)
            if block.terminator:
                # the preheader ends with an unconditional jump into the loop:
                # the guard has to be evaluated on that path only
                at = begin + len(block.lines) - 1
            else:
                # the fall through of the preheader enters the loop: the guard
                # becomes a block of its own between the preheader and the
                # loop, so that it is evaluated on the entering path only
                at = len(unit.lines)
            # the value of ctx.gqr[index] must be the same at the hoisted test
            # and at the test inside the loop
            declined = None
            tail_lines = unit.lines[at:] if not block.terminator else unit.lines[at:]
            for line in tail_lines:
                code = P.code_of(line)
                for m in GQR_WRITE.finditer(code):
                    if int(m.group('index')) == index:
                        declined = 'the preheader writes ctx.gqr[%d]' % index
                if declined is None and flow._gqr_unsafe(code):
                    declined = 'the preheader may change ctx.gqr'
                if declined is not None:
                    break
            if declined is not None:
                details.append({'loop': name, 'index': index, 'declined': declined})
                continue
            clone = _clone_text(loop, loop_sites,
                                prefix + '_' + (loop.header.unit.label or 'loop'),
                                fn.labels.keys())
            if clone is None:
                details.append({'loop': name, 'index': index, 'declined': 'clone failed'})
                continue
            clone_text, names = clone
            # the guard is evaluated once, at the end of the preheader
            condition = loop_sites[0].condition
            guard = '    if (%s) goto %s;' % (condition, names[loop.header.unit.label])
            edits.append((fn.first_line + 1 + unit.first + at, guard))
            # the clone follows the last unit of the loop
            last = loop.units[-1]
            end_of_loop = fn.first_line + 1 + last.first + len(last.lines)
            tail = ''
            if not loop.latch.terminator:
                # the fall through path of the original loop must skip the
                # clone, so it gets an explicit jump to the loop exit
                following = None
                for u in fn.units:
                    if u.index == last.index + 1:
                        following = u
                tail = '    goto %s;' % following.label
            edits.append((end_of_loop, tail + (NL if tail else '') + clone_text))
            total += 1
            details.append({
                'loop': name,
                'index': index,
                'mask': mask,
                'preheader': preheader.unit.label or '(entry)',
                'clone': names[loop.header.unit.label],
                'hoisted': True,
            })
    if edits:
        lines = _apply_edits(lines, edits)
    return NL.join(lines), total, details


def _apply_edits(lines, edits):
    """Apply (absolute line index, text) insertions, last one first."""
    edits = sorted(edits, key=lambda e: e[0], reverse=True)
    out = list(lines)
    for at, text in edits:
        out.insert(at, text)
    return out


# ---------------------------------------------------------------------------
# coverage report
# ---------------------------------------------------------------------------

def analyse_source(source):
    """Static coverage of the fact analysis over a whole source file."""
    parsed = parse_source(source)
    report = {
        'functions': 0,
        'units': 0,
        'blocks': 0,
        'joins': 0,
        'loops': 0,
        'gqr_sites': 0,
        'gqr_sites_invariant': 0,
        'gqr_tests_true': 0,
        'gqr_tests_false': 0,
        'gqr_tests_unknown': 0,
        'fp_facts': 0,
        'fp_facts_canonical': 0,
        'addr_bases': 0,
        'addr_loop_invariant': 0,
        'per_function': [],
    }
    for start, end, fn in parsed:
        flow = FactFlow(fn).analyse()
        sites = find_gqr_sites(fn)
        entry = {
            'name': fn.name,
            'units': len(fn.units),
            'blocks': len(fn.blocks),
            'joins': sum(1 for b in fn.blocks if len(b.pred) > 1),
            'loops': len(flow.loops),
            'gqr_sites': len(sites),
        }
        invariant = 0
        for site in sites:
            for loop in flow.loops:
                if any(site.unit is u for u in loop.units):
                    if flow.gqr_loop_invariant(loop, site.index) and not flow.gqr_loop_observer(loop):
                        invariant += 1
                    break
        entry['gqr_sites_invariant'] = invariant
        fp = 0
        fp32 = 0
        addr = 0
        for b in fn.blocks:
            state = flow.fp_state(b)
            fp += sum(1 for mask in state.values() if mask)
            fp32 += sum(1 for mask in state.values()
                        if mask and not (mask & (mask - 1)) and
                        flow.fp_pairs[(mask.bit_length() - 1) // 2][0] == 'f32')
            addr += sum(1 for v in flow.addr_state(b).values() if v and v is not TOP)
        entry['fp_facts'] = fp
        entry['fp_facts_f32'] = fp32
        entry['addr_bases'] = addr
        invariant_bases = 0
        for loop in flow.loops:
            for root, info in flow.loop_bases(loop).items():
                if info['invariant']:
                    invariant_bases += 1
        entry['addr_loop_invariant'] = invariant_bases
        report['per_function'].append(entry)
        report['functions'] += 1
        report['units'] += entry['units']
        report['blocks'] += entry['blocks']
        report['joins'] += entry['joins']
        report['loops'] += entry['loops']
        report['gqr_sites'] += entry['gqr_sites']
        report['gqr_sites_invariant'] += entry['gqr_sites_invariant']
        report['fp_facts'] += entry['fp_facts']
        report['fp_facts_canonical'] += entry['fp_facts_f32']
        report['addr_bases'] += entry['addr_bases']
        report['addr_loop_invariant'] += entry['addr_loop_invariant']
    return report

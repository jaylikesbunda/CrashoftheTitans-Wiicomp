"""CFG-wide CR / XER demand analysis and dead-definition elimination for promoted guest bodies.

`promote_regs` keeps the guest CR fields and the guest XER word in C++ locals and
writes them back to `ctx` only where something else can observe them.  The
straight-line `condition_demand` pass in `guest_batch_lowering` removes CR
definitions that are overwritten before any reader, but it only inspects
straight-line runs: a join, a loop or a barrier ends the run, so a definition
that is dead on *every* incoming path survives.

This module computes the same demand over the whole control-flow graph of one
promoted function body and removes only the definitions that are provably dead.

Lattice
-------
The analysis tracks 34 components as a bit mask:

    component  0..31  CR bit  f*4+b        (c0lt .. c7so) -- the promoted local
    component  32     XER.CA               (bit 29 of the XER word)
    component  33     XER.WORD             (the whole 32 bit XER word)

A component is *live* at a program point when some path from that point reaches
a use of the component without an intervening definition.  The lattice is flat
(a set of components); the transfer function of a line is

    live_before = (live_after & ~defs) | uses

with `defs` the components the line defines and `uses` the components it reads.
The fixed point is the least one: every component is live at a join only if it
is live on *every* incoming path, and a loop is solved by iteration to a fixed
point (never by an unrolled/rolled assumption).

Uses
----
* a read of a CR local (`c0lt`) or of `ctx.cr[f].bit` is a use of that bit;
* `ctx.cr[f].bit = c0lt;` (the promotion write back) is a use of the local;
* `c0lt = ctx.cr[f].bit;` (the promotion reload) *defines* the local; the
  `ctx.cr[f].bit` read is the source of that definition, not a use of the old
  local value;
* `ctx.cr[f].bit = <expr>;` without the promoted local is a definition site
  (the unpromoted form);
* the XER word is read whole (`ctx.xer = xer;`, `g0 = xer;`) -> use of
  XER.WORD *and* XER.CA, because a whole word observation also observes bit 29;
* the XER word is read as carry only (`((xer >> 29) & 1)`) -> use of XER.CA only;
* `xer = ctx.xer;` defines XER.WORD and XER.CA (a whole word load kills the
  carry facts);
* `xer = (xer & ~(1 << 29)) | V;` defines XER.CA only: the other 31 bits of the
  word are preserved, so this is a carry-only read-modify-write.

Observations
------------
Every barrier that hands `ctx` to something that is not a whitelisted helper
(a call, an unknown helper, the interpreter), every poll, every block call and
every `return` observes *all* CR bits and the whole XER word.  With
`strict_observations` (the default) those observation points are modelled as
uses of every component, which is the conservative reading required for
arbitrary guest state.  With `strict_observations=False` only the explicit
promotion write backs / reloads count, which is the precise reading of the
promoted code and is sound as long as the promotion invariant (ctx always holds
the current CR/XER value) holds.

Deletion classes
----------------
Only two definition shapes may be removed, and only when the definition is
dead (the component is not live after the line):

1. a CR assignment whose right hand side is a *pure integer comparison* over
   locals and constants.  The purity rule is the one used by
   `guest_batch_lowering.condition_demand` (no call except an `int32_t` /
   `uint32_t` cast, no memory access, no division, no FP observation, no
   increment, no assignment);
2. an XER.CA read-modify-write of the guarded form accepted by
   `guest_batch_lowering.carry_regions`

    xer = (xer & ~(1 << 29)) | (V);

   where `V` is one of the carry values that `carry_regions` already proves
   (`((x >> d) & 1) << 29`, `cond ? (1u << 29) : 0u`, `(flag << 29)` with the
   flag only ever holding 0 or 1).  The value is carry-only, so the other 31
   bits of the word are untouched and the statement has no side effect.

Nothing else is removed: no call, no memory access, no FP observation, no
volatile access, no possibly exceptional behaviour, no partial statement, no
label, no goto and no interior entry is ever touched.  A line that is not a
single simple assignment is never edited, so the exact spelling of every
surviving line is preserved.

API
---
    parse_function(text)          -> Parsed | None
    analyze(text, **options)       -> Analysis   (units, lines, liveness, stats)
    eliminate(text, **options)     -> (new_text, stats)
    eliminate_detailed(text, ...)  -> (new_text, stats, deletions)
    restore(text, deletions)       -> text       (exact inverse)
    scan(text, **options)          -> stats      (counts, no rewrite)

`eliminate` is idempotent: a second run finds nothing left to remove.
"""
import re
from collections import deque

import promote_regs as P

NL = '\n'

# ---------------------------------------------------------------- components
CR_COUNT = 32
COMP_CA = 32
COMP_WORD = 33
COMP_COUNT = 34
ALL_CR = (1 << CR_COUNT) - 1
ALL_XER = (1 << COMP_CA) | (1 << COMP_WORD)

CR_BIT_INDEX = {'lt': 0, 'gt': 1, 'eq': 2, 'so': 3}

CR_LOCAL_RE = re.compile(r'(?<![\w.])c([0-7])(lt|gt|eq|so)\b')
CTX_CR_RE = re.compile(r'\bctx\.cr\[(\d)\]\.(lt|gt|eq|so)\b')
CTX_CR_ASSIGN_RE = re.compile(r'ctx\.cr\[(\d)\]\.(lt|gt|eq|so)')
XER_LOCAL_RE = re.compile(r'(?<![\w.])xer\b')
CTX_XER_RE = re.compile(r'\bctx\.xer\b')

CA_EXTRACT_RE = re.compile(r'\(\(xer\s*>>\s*29\)\s*&\s*1(?:u)?\)|\(xer\s*>>\s*29\)\s*&\s*1(?:u)?')
CA_UPDATE_RE = re.compile(r'^\s*xer\s*=\s*\(xer\s*&\s*~\(1u?\s*<<\s*29\)\)\s*\|\s*([^;]+);\s*$')
XER_ASSIGN_RE = re.compile(r'^\s*xer\s*=\s*(.*?);\s*$', re.S)
CTX_XER_ASSIGN_RE = re.compile(r'^\s*ctx\.xer\s*=\s*(.*?);\s*$', re.S)

# the carry values carry_regions already proves
CA_SHIFT_RE = re.compile(r'^\(\(\(\w+ >> \d+\) & 1u?\) << 29\)$')
CA_SELECT_RE = re.compile(r'^\(\([^;()]+\) \? \(1u << 29\) : 0u\)$')
CA_FLAG_RE = re.compile(r'^\((\w+) << 29\)$')

LABEL_RE = re.compile(r'^\s*([A-Za-z_]\w*):\s*;?\s*$')
GOTO_RE = re.compile(r'\bgoto\s+([A-Za-z_]\w*)\s*;')
RETURN_RE = re.compile(r'\breturn\s*;')
FUNC_HEAD_RE = re.compile(r'^(?P<pre>.*?)\bvoid\s+(?P<name>[A-Za-z_]\w*)\s*\(\s*NWII_CTX_PARAM\s+ctx\s*\)\s*\{\s*$')
DECL_RE = re.compile(r'^\s*(?:static\s+|const\s+|constexpr\s+|unsigned\s+|signed\s+)*'
                     r'(?:bool|char|short|int|long|float|double|void|u?int\d+_t|size_t|auto)\b')
CONTROL_HEADER_RE = re.compile(r'^\s*(?:if|while|for|switch|else)\b')
ASSIGN_TARGET_RE = re.compile(r'\s*=(?!=)')

# condition_demand's purity rule, copied verbatim so the two passes agree
PURE_TOKEN_RE = re.compile(r'(?:g\d+|ctrv|xer|c[0-7](?:lt|gt|eq|so)|int32_t|uint32_t|true|false|[uUlL]|x[0-9a-fA-F]+)')
PURE_CALL_RE = re.compile(r'(\w+)\s*\(')


def pure_cr_value(value):
    """Exactly the purity rule used by guest_batch_lowering.condition_demand."""
    tokens = re.findall(r'\b[A-Za-z_]\w*\b', value)
    if re.search(r'[.*/%\[\]]|\+\+|--|(?<![=!<>])=(?!=)', value):
        return False
    if not all(PURE_TOKEN_RE.fullmatch(n) for n in tokens):
        return False
    calls = PURE_CALL_RE.findall(value)
    return all(n in {'int32_t', 'uint32_t'} for n in calls)


def _segments(code):
    """Split a line on ';' (string aware).  Each segment is one statement-ish chunk."""
    out = []
    start = 0
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        if c == '"' or c == "'":
            q = c
            i += 1
            while i < n:
                if code[i] == '\\':
                    i += 2
                    continue
                if code[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == ';':
            out.append(code[start:i + 1])
            start = i + 1
        i += 1
    if code[start:].strip():
        out.append(code[start:])
    return out


def _is_declaration(code):
    return bool(DECL_RE.match(code)) and '(' not in code


def _reload_source_positions(code):
    """Positions of `ctx.cr[f].bit` reads that are the source of a local reload."""
    out = set()
    for m in CR_LOCAL_RE.finditer(code):
        a = ASSIGN_TARGET_RE.match(code, m.end())
        if not a:
            continue
        eq = a.end()
        stop = code.find(';', eq)
        if stop < 0:
            stop = len(code)
        for c in CTX_CR_RE.finditer(code, eq, stop):
            out.add(c.start())
    return out


def _cr_mask(code):
    """(defs, uses) over the 32 CR components for one line."""
    defs = 0
    uses = 0
    if _is_declaration(code):
        for m in CR_LOCAL_RE.finditer(code):
            defs |= 1 << (int(m.group(1)) * 4 + CR_BIT_INDEX[m.group(2)])
        return defs, uses
    sources = _reload_source_positions(code)
    for m in CR_LOCAL_RE.finditer(code):
        b = int(m.group(1)) * 4 + CR_BIT_INDEX[m.group(2)]
        if ASSIGN_TARGET_RE.match(code, m.end()):
            defs |= 1 << b
        else:
            uses |= 1 << b
    for m in CTX_CR_RE.finditer(code):
        b = int(m.group(1)) * 4 + CR_BIT_INDEX[m.group(2)]
        local = 'c%s%s' % (m.group(1), m.group(2))
        if ASSIGN_TARGET_RE.match(code, m.end()):
            # a write of ctx.cr[f].bit: a write back of the promoted local is an
            # observation of the local, anything else is a definition site.
            if re.search(r'(?<![\w.])' + local + r'\b', code):
                uses |= 1 << b
            else:
                defs |= 1 << b
        elif m.start() in sources:
            pass                      # reload: the source of a definition
        else:
            uses |= 1 << b
    return defs, uses


def _xer_mask(code):
    """(defs, uses) over the XER components for one line."""
    defs = 0
    uses = 0
    for segment in _segments(code):
        s = segment.strip()
        if not s:
            continue
        update = CA_UPDATE_RE.match(s)
        if update:
            # carry-only read-modify-write: defines bit 29, keeps the rest
            defs |= 1 << COMP_CA
            uses |= 1 << COMP_WORD
            continue
        assign = XER_ASSIGN_RE.match(s)
        if assign:
            rhs = assign.group(1)
            if CTX_XER_RE.search(rhs) and not XER_LOCAL_RE.search(rhs):
                defs |= 1 << COMP_WORD | 1 << COMP_CA      # whole word reload
                continue
            if XER_LOCAL_RE.search(rhs):
                defs |= 1 << COMP_WORD | 1 << COMP_CA
                uses |= 1 << COMP_WORD | 1 << COMP_CA
                continue
            defs |= 1 << COMP_WORD | 1 << COMP_CA
            continue
        ctx_assign = CTX_XER_ASSIGN_RE.match(s)
        if ctx_assign:
            if XER_LOCAL_RE.search(ctx_assign.group(1)):
                uses |= 1 << COMP_WORD | 1 << COMP_CA      # write back
            else:
                defs |= 1 << COMP_WORD | 1 << COMP_CA
            continue
        if XER_LOCAL_RE.search(s):
            if CA_EXTRACT_RE.search(s):
                uses |= 1 << COMP_CA
            else:
                uses |= 1 << COMP_WORD | 1 << COMP_CA
        if CTX_XER_RE.search(s):
            uses |= 1 << COMP_WORD | 1 << COMP_CA
    return defs, uses


def _terminates(code):
    """True when the line cannot fall through to the next line."""
    s = code.strip()
    if not s:
        return False
    if re.fullmatch(r'goto\s+[A-Za-z_]\w*\s*;', s):
        return True
    if 'goto' in s:
        return False
    if RETURN_RE.search(s) and not re.search(r'\b(if|else|while|for|switch|do)\b', s):
        return True
    return False


class Line(object):
    __slots__ = ('text', 'code', 'comment', 'kind', 'cr_defs', 'cr_uses',
                 'xer_defs', 'xer_uses', 'gotos', 'terminates', 'has_return',
                 'live_after', 'index', 'unit', 'candidate', 'declaration',
                 'protected')

    def __init__(self, text, index):
        self.text = text
        self.code = P.code_of(text)
        self.comment = text[len(self.code):]
        self.index = index
        self.kind = P.classify(self.code)
        self.declaration = _is_declaration(self.code)
        self.cr_defs, self.cr_uses = _cr_mask(self.code)
        self.xer_defs, self.xer_uses = _xer_mask(self.code)
        self.gotos = GOTO_RE.findall(self.code)
        self.terminates = _terminates(self.code)
        self.has_return = bool(RETURN_RE.search(self.code))
        self.live_after = 0
        self.unit = None
        self.candidate = None
        self.protected = False


class Unit(object):
    __slots__ = ('label', 'lines', 'succ', 'preds', 'live_in', 'live_out', 'index')

    def __init__(self, label, index):
        self.label = label
        self.lines = []
        self.succ = []
        self.preds = []
        self.live_in = 0
        self.live_out = 0
        self.index = index


class Parsed(object):
    __slots__ = ('name', 'head', 'preamble', 'body', 'entries', 'origin')

    def __init__(self, name, head, preamble, body, entries, origin):
        self.name = name
        self.head = head
        self.preamble = preamble
        self.body = body
        self.entries = entries
        self.origin = origin


def parse_function(text):
    """Split one promoted function body into preamble, body and interior entries.

    The grammar is the one of promote_regs (units, `loc_` labels, `goto`,
    interior entries through the `ctx.pc` switch) but the function header is
    accepted in its specialised spelling as well.
    """
    lines = text.split(NL)
    if not lines:
        return None
    head = FUNC_HEAD_RE.match(lines[0])
    if not head:
        return None
    if lines[-1].strip() != '}':
        return None
    i = 1
    preamble = []
    if i < len(lines) and lines[i].startswith('    NWII_TRACE_CALL('):
        preamble.append(lines[i])
        i += 1
    entries = []
    # The entry switch may sit behind the local declarations.  It is a dispatch,
    # not a sequential path, so it is lifted out of the body; the interior
    # entries it names are the roots of the CFG.
    switch_at = None
    depth = 0
    for index in range(i, len(lines) - 1):
        line = lines[index]
        code = P.code_of(line)
        if depth == 0 and re.match(r'^\s*if \(ctx\.pc != 0x[0-9A-Fa-f]+\) \{$', line):
            switch_at = index
            break
        depth += code.count('{') - code.count('}')
    if switch_at is not None:
        block = []
        depth = 0
        index = switch_at
        closed = False
        while index < len(lines):
            line = lines[index]
            code = P.code_of(line)
            block.append(line)
            depth += code.count('{') - code.count('}')
            index += 1
            if depth <= 0:
                closed = True
                break
        if not closed:
            return None
        if len(block) < 2 or not re.match(r'^\s*switch \(ctx\.pc\) \{$', block[1]):
            return None
        for line in block:
            entries.extend(GOTO_RE.findall(line))
        body_lines = lines[i:switch_at] + lines[index:-1]
        origin = list(range(i, switch_at)) + list(range(index, len(lines) - 1))
        preamble.extend(block)
        return Parsed(head.group('name'), lines[0], preamble, body_lines, entries, origin)
    body = lines[i:-1]
    return Parsed(head.group('name'), lines[0], preamble, body, entries, list(range(i, len(lines) - 1)))


def iter_functions(text):
    """Yield (name, function_text) for every promoted function body in a file."""
    lines = text.split(NL)
    starts = []
    for index, line in enumerate(lines):
        if FUNC_HEAD_RE.match(line):
            starts.append(index)
    for order, start in enumerate(starts):
        end = starts[order + 1] if order + 1 < len(starts) else len(lines)
        depth = 0
        stop = end
        for index in range(start, end):
            code = P.code_of(lines[index])
            depth += code.count('{') - code.count('}')
            if depth <= 0 and index > start:
                stop = index + 1
                break
        else:
            stop = end
        yield FUNC_HEAD_RE.match(lines[start]).group('name'), NL.join(lines[start:stop])


class Analysis(object):
    __slots__ = ('parsed', 'units', 'lines', 'stats', 'options')

    def __init__(self, parsed, units, lines, stats, options):
        self.parsed = parsed
        self.units = units
        self.lines = lines
        self.stats = stats
        self.options = options


def _observation_mask(kind, has_return, strict):
    """Components observed by a barrier / poll / return."""
    if not strict:
        return 0
    if kind in ('barrier', 'cbarrier', 'poll', 'blockcall') or has_return:
        return ALL_CR | ALL_XER
    return 0


def _analyze(parsed, strict=True):
    units = []
    current = Unit(None, 0)
    labels = {}
    depth = 0
    control_body = False
    for offset, text in enumerate(parsed.body):
        m = LABEL_RE.match(text)
        if m and m.group(1) not in ('case', 'default', 'switch', 'if', 'else', 'while', 'for', 'return', 'goto'):
            if depth != 0:
                return None
            units.append(current)
            current = Unit(m.group(1), len(units))
            labels[m.group(1)] = current.index
            control_body = False
            continue
        line = Line(text, parsed.origin[offset])
        code = line.code.strip()
        if control_body:
            # the line is the body of a braceless control header: removing it
            # would change the meaning of the header
            line.protected = True
            control_body = False
        if code and CONTROL_HEADER_RE.match(code) and not code.endswith('{') and not code.endswith(';'):
            control_body = True
        if depth != 0:
            line.kind = 'cbarrier'
        current.lines.append(line)
        line.unit = current
        depth += line.code.count('{') - line.code.count('}')
        if line.gotos:
            # A branch ends the unit: the successor of the branch is not the
            # fall through of the unit, so the demand of the branch target has
            # to be joined with the demand of the rest of the unit.  Splitting
            # here keeps the backward sweep exact.
            units.append(current)
            current = Unit(None, len(units))
    units.append(current)
    if depth != 0:
        return None
    for unit in units:
        for line in unit.lines:
            for target in line.gotos:
                if target not in labels:
                    return None
    # control flow
    for index, unit in enumerate(units):
        succ = set()
        for line in unit.lines:
            for target in line.gotos:
                succ.add(labels[target])
        if not unit.lines or not unit.lines[-1].terminates:
            if index + 1 < len(units):
                succ.add(index + 1)
        unit.succ = sorted(succ)
    for index, unit in enumerate(units):
        for target in unit.succ:
            units[target].preds.append(index)
    # liveness
    def transfer(unit, live):
        for line in reversed(unit.lines):
            line.live_after = live
            defs = line.cr_defs | line.xer_defs
            uses = line.cr_uses | line.xer_uses | _observation_mask(line.kind, line.has_return, strict)
            live = (live & ~defs) | uses
        return live
    count = len(units)
    live_in = [0] * count
    live_out = [0] * count
    work = deque(range(count))
    queued = set(work)
    while work:
        index = work.popleft()
        queued.discard(index)
        out = 0
        for target in units[index].succ:
            out |= live_in[target]
        new_in = transfer(units[index], out)
        if new_in != live_in[index] or out != live_out[index]:
            live_in[index] = new_in
            live_out[index] = out
            for pred in units[index].preds:
                if pred not in queued:
                    work.append(pred)
                    queued.add(pred)
    for index, unit in enumerate(units):
        unit.live_in = live_in[index]
        unit.live_out = live_out[index]
        transfer(unit, live_out[index])
    lines = [line for unit in units for line in unit.lines]
    stats = _stats(units, lines, parsed)
    return Analysis(parsed, units, lines, stats, {'strict_observations': strict})


def _stats(units, lines, parsed):
    stats = {
        'units': len(units),
        'lines': len(lines),
        'interior_entries': len(parsed.entries),
        'barriers': 0,
        'polls': 0,
        'returns': 0,
        'cr_defs': 0,
        'cr_pure_defs': 0,
        'cr_dead': 0,
        'xer_word_defs': 0,
        'xer_ca_updates': 0,
        'xer_accepted': 0,
        'xer_dead': 0,
    }
    for line in lines:
        if line.kind in ('barrier', 'cbarrier'):
            stats['barriers'] += 1
        elif line.kind == 'poll':
            stats['polls'] += 1
        if line.has_return:
            stats['returns'] += 1
        if line.cr_defs:
            stats['cr_defs'] += bin(line.cr_defs).count('1')
        if line.xer_defs & (1 << COMP_WORD):
            stats['xer_word_defs'] += 1
        for segment in _segments(line.code):
            if CA_UPDATE_RE.match(segment.strip()):
                stats['xer_ca_updates'] += 1
    return stats


def analyze(text, strict=True):
    """Compute the CFG demand of one promoted function body."""
    parsed = parse_function(text)
    if parsed is None:
        return None
    return _analyze(parsed, strict)


def _ca_value_accepted(value, whole_function_text):
    """The carry values carry_regions already proves, checked over the whole function."""
    value = value.strip()
    if '<< 29' not in value:
        return False
    if CA_SHIFT_RE.fullmatch(value):
        return True
    if CA_SELECT_RE.fullmatch(value):
        return True
    flag = CA_FLAG_RE.fullmatch(value)
    if not flag:
        return False
    symbol = re.escape(flag.group(1))
    assignments = re.findall(r'\b' + symbol + r'\s*=(?!=)\s*([^;]+);', whole_function_text)
    if not assignments:
        return False
    if not all(re.search(r'\? 1 : 0$', v.strip()) for v in assignments):
        return False
    return not re.search(r'\b' + symbol + r'\s*(?:\+\+|--|[-+*/&|^]=)', whole_function_text)


def _classify_candidates(analysis, whole_text):
    """Mark the lines that may be removed."""
    for line in analysis.lines:
        line.candidate = None
        if line.protected:
            continue
        stripped = line.code.strip()
        if not stripped or stripped.startswith('{') or stripped.startswith('}'):
            continue
        if stripped.count(';') != 1 or not stripped.endswith(';'):
            continue
        head, sep, tail = stripped[:-1].partition('=')
        if not sep:
            continue
        m = CR_LOCAL_RE.fullmatch(head.strip())
        if m:
            bit = int(m.group(1)) * 4 + CR_BIT_INDEX[m.group(2)]
            value = tail.strip()
            if not pure_cr_value(value):
                continue
            if (1 << bit) & line.live_after:
                continue
            line.candidate = ('cr', bit, m.group(0), value)
            continue
        m = CTX_CR_ASSIGN_RE.fullmatch(head.strip())
        if m:
            # the unpromoted definition site
            bit = int(m.group(1)) * 4 + CR_BIT_INDEX[m.group(2)]
            value = tail.strip()
            if not pure_cr_value(value):
                continue
            if (1 << bit) & line.live_after:
                continue
            line.candidate = ('cr', bit, m.group(0), value)
            continue
        m = CA_UPDATE_RE.match(stripped)
        if m:
            value = m.group(1).strip()
            if not _ca_value_accepted(value, whole_text):
                continue
            if (1 << COMP_CA) & line.live_after:
                continue
            line.candidate = ('xer', COMP_CA, 'xer', value)


def _accepted_ca_updates(analysis, whole_text):
    """Number of carry read-modify-writes whose value carry_regions already proves."""
    total = 0
    for line in analysis.lines:
        for segment in _segments(line.code):
            m = CA_UPDATE_RE.match(segment.strip())
            if m and _ca_value_accepted(m.group(1).strip(), whole_text):
                total += 1
    return total


def eliminate_detailed(text, strict=True):
    """Remove the provably dead CR / XER definitions of one promoted function body.

    Returns (new_text, stats, deletions).  `deletions` is a list of
    (original line index, original line text) so that `restore` can rebuild the
    input exactly.
    """
    analysis = analyze(text, strict)
    if analysis is None:
        return text, {'applied': False, 'reason': 'unrecognised function body'}, []
    _classify_candidates(analysis, text)
    lines = text.split(NL)
    keep = []
    deletions = []
    removed_cr = 0
    removed_xer = 0
    by_index = {line.index: line for line in analysis.lines}
    for index, line in enumerate(lines):
        target = by_index.get(index)
        if target is not None and target.candidate is not None:
            deletions.append((index, lines[index]))
            if target.candidate[0] == 'cr':
                removed_cr += 1
            else:
                removed_xer += 1
            continue
        keep.append(lines[index])
    stats = dict(analysis.stats)
    stats.update({
        'applied': True,
        'cr_removed': removed_cr,
        'xer_removed': removed_xer,
        'cr_pure_defs': stats['cr_defs'],
        'xer_accepted': _accepted_ca_updates(analysis, text),
    })
    stats['cr_dead'] = removed_cr
    stats['xer_dead'] = removed_xer
    return NL.join(keep), stats, deletions


def restore(text, deletions):
    """Undo `eliminate_detailed`: put every deleted line back where it was."""
    if not deletions:
        return text
    lines = text.split(NL)
    for index, original in sorted(deletions, key=lambda item: item[0]):
        lines.insert(index, original)
    return NL.join(lines)


def eliminate(text, strict=True):
    """(new_text, stats) for one promoted function body."""
    new, stats, _ = eliminate_detailed(text, strict)
    return new, stats


def scan(text, strict=True):
    """Static counts for one promoted function body, without rewriting it."""
    analysis = analyze(text, strict)
    if analysis is None:
        return {'applied': False, 'reason': 'unrecognised function body'}
    _classify_candidates(analysis, text)
    stats = dict(analysis.stats)
    removed_cr = 0
    removed_xer = 0
    for line in analysis.lines:
        if line.candidate is None:
            continue
        if line.candidate[0] == 'cr':
            removed_cr += 1
        else:
            removed_xer += 1
    stats.update({
        'applied': True,
        'cr_pure_defs': stats['cr_defs'],
        'xer_accepted': _accepted_ca_updates(analysis, text),
        'cr_dead': removed_cr,
        'xer_dead': removed_xer,
        'cr_removed': removed_cr,
        'xer_removed': removed_xer,
    })
    return stats


def eliminate_file(text, strict=True):
    """Apply `eliminate` to every promoted function body in a file."""
    out = []
    cursor = 0
    total = {}
    for name, body in iter_functions(text):
        start = text.index(body, cursor)
        out.append(text[cursor:start])
        new, stats = eliminate(body, strict)
        out.append(new)
        cursor = start + len(body)
        for key, value in stats.items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
    out.append(text[cursor:])
    return ''.join(out), total

"""Guest register promotion for generated functions (text transform with exact spill / reload placement).

Every generated function keeps the PowerPC register file in the CPUContext: each guest register is a memory operand, every guest store
(through a char pointer) forces the compiler to assume ctx changed, and dead CR / count updates can never be deleted because ctx is
observable. This pass keeps GPR0-31, CTR, the CR bits and inst_count in C++ locals instead and writes them back to ctx exactly where
something else could observe them.

Model (per function, only functions made of recognised statement shapes are touched; anything unusual returns None):
  * promoted state  = g0..g31, ctrv, c<f><b> (CR field f bit b), ic (inst_count)
  * a plain line (touches ctx only through promoted fields, ctx.mmu, FPR/PS1, xer, lr, pc, msr, whitelisted FP helpers) has its
    promoted references replaced by the locals
  * a barrier line (passes ctx to something: callee, helper, interpreter...) is emitted unchanged, operating on ctx memory:
        spill(dirty) ; line ; reload(live-after)
  * the `if (native_poll_due()) { ... }` line pays spill / reload only inside the taken branch
  * lines that read ic through a method (read_timebase, read_dec, write_dec, dec_expired) spill ic first
  * every `return;` writes back what may be dirty at that point
  * dirty = may-written since the last sync (forward dataflow); live = may be read before being rewritten, counting the spills that
    the dirty sets imply (backward dataflow). Entry loads (function start and every entry-switch case) load only what is live there.
Anything that can unwind (longjmp in the IOS callback helpers) happens inside a barrier line, after the spill, so ctx is authoritative.
Diagnostic-only caveat: the scheduler watch recorder (os_hle_stubs watch_hit, enabled by default) reads r1 through g_ctx_ptr.

Entry trampoline (NWII_BUILD_ENTRY_TRAMPOLINE, default on): the entry switch of a function with interior entries used
to repeat the full live-in reload for every case. Cases whose label has the same live-in set now share one materialiser
(the outer switch groups them, the group reloads the set once, a nested switch then picks the target). Every case still
materialises exactly its label's live-in set and nothing else, so a register outside that set is never spuriously loaded
and one inside it is always present before the loc_ label runs. The transform is per group and only taken when the
grouped text is strictly shorter than the flat text, so a group that would not shrink (e.g. two one-register reloads)
keeps its original spelling. NWII_BUILD_ENTRY_TRAMPOLINE=0 restores the flat per-case emission.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NL = '\n'
# The pristine generated chunk spells the entry `NWII_GUEST_ABI void func_...`
# and carries a perf-census call before the interior-entry switch; the staged
# tree has neither (the entry specialisation stage removes the attribute and the
# census line is added by a later stage). Both spellings describe the same
# function, so the parser accepts either.
FUNC_HEAD = re.compile(r'^(?:NWII_GUEST_ABI )?void (func_[0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{$')
LABEL = re.compile(r'^loc_([0-9A-F]{8}): ;$')
GOTO = re.compile(r'goto loc_([0-9A-F]{8})')
G_REF = re.compile(r'ctx\.gpr\[(\d+)\]')
CR_REF = re.compile(r'ctx\.cr\[(\d)\]\.(lt|gt|eq|so)\b')
CTR_REF = re.compile(r'ctx\.ctr\b')
IC_REF = re.compile(r'ctx\.inst_count\b')
ANY_REF = re.compile(r'ctx\.gpr\[(\d+)\]|ctx\.cr\[(\d)\]\.(lt|gt|eq|so)\b|ctx\.ctr\b|ctx\.inst_count\b|ctx\.fpr\[(\d+)\]|ctx\.ps1\[(\d+)\]|ctx\.xer\b')
ASSIGN_AFTER = re.compile(r'\s*(?:=(?!=)|[-+*/%&|^]=|<<=|>>=|\+\+|--)')
INCDEC_BEFORE = re.compile(r'(?:\+\+|--)\s*$')
RETURN = re.compile(r'\breturn\s*;')
WHITELIST_PREFIX = ('ps_', 'psq_')
WHITELIST_EXACT = {'require_fpu', 'read_timebase', 'read_dec', 'write_dec', 'dec_expired', 'native_psq_load_slow_pair', 'native_psq_store_slow_val'}
FP_GUARD = re.compile(r'if \(ctx\.require_fpu\((0x[0-9A-F]{8})\)\)')
IC_SYNC = re.compile(r'ctx\.(?:read_timebase|read_dec|write_dec|dec_expired)\s*\(')
UNSUPPORTED = ('NATIVE_SQRT', 'NATIVE_PLAIN', 'NATIVE_CACHE', 'NATIVE_WPAD', 'static ', '__asm', '__builtin', '&ctx.gpr', '&ctx.cr', '&ctx.ctr', '&ctx.inst_count', 'setjmp', 'longjmp', 'throw')
BIT_CTR = 32
BIT_CR = 33          # 33 + field*4 + bit
BIT_IC = 65
BIT_FPR = 66         # fpr[i]
BIT_PS1 = 98         # ps1[i]
BIT_XER = 130        # whole XER word (CA/OV/SO)
FP_MASK = ((1 << 64) - 1) << BIT_FPR
CR_MASK = ((1 << 32) - 1) << BIT_CR
CR_BITS = {'lt': 0, 'gt': 1, 'eq': 2, 'so': 3}
POLL_PREFIX = 'if (nwii::runtime::native_poll_due()) {'
BLOCKCALL = re.compile(r'^\s*if\s*\(native_blocks_on\)\s*\{\s*(native_block_[0-9A-F_]+)\(ctx\);\s*goto loc_([0-9A-F]{8});\s*\}\s*$')
ENTRY_TRAMPOLINE_ENV = 'NWII_BUILD_ENTRY_TRAMPOLINE'


def entry_trampoline_enabled():
    """Shared per-live-in-set entry materialisers instead of one reload list per case."""
    return os.environ.get(ENTRY_TRAMPOLINE_ENV, '1') != '0'


def code_of(line):
    """Line without its trailing // comment (quote aware)."""
    if '//' not in line:
        return line
    in_str = False
    i = 0
    while i < len(line) - 1:
        c = line[i]
        if c == '"' and (i == 0 or line[i - 1] != '\\'):
            in_str = not in_str
        elif not in_str and c == '/' and line[i + 1] == '/':
            return line[:i]
        i += 1
    return line


def comment_of(line):
    code = code_of(line)
    return line[len(code):]


def bit_of(m):
    if m.group(0) == 'ctx.ctr':
        return BIT_CTR
    if m.group(0) == 'ctx.inst_count':
        return BIT_IC
    if m.group(0) == 'ctx.xer':
        return BIT_XER
    if m.group(1) is not None:
        return int(m.group(1))
    if m.group(4) is not None:
        return BIT_FPR + int(m.group(4))
    if m.group(5) is not None:
        return BIT_PS1 + int(m.group(5))
    return BIT_CR + int(m.group(2)) * 4 + CR_BITS[m.group(3)]


def local_name(bit):
    if bit < 32:
        return 'g%d' % bit
    if bit == BIT_CTR:
        return 'ctrv'
    if bit == BIT_IC:
        return 'ic'
    if bit == BIT_XER:
        return 'xer'
    if bit >= BIT_PS1:
        return 'p%d' % (bit - BIT_PS1)
    if bit >= BIT_FPR:
        return 'f%d' % (bit - BIT_FPR)
    b = bit - BIT_CR
    return 'c%d%s' % (b // 4, ('lt', 'gt', 'eq', 'so')[b % 4])


def ctx_name(bit):
    if bit < 32:
        return 'ctx.gpr[%d]' % bit
    if bit == BIT_CTR:
        return 'ctx.ctr'
    if bit == BIT_IC:
        return 'ctx.inst_count'
    if bit == BIT_XER:
        return 'ctx.xer'
    if bit >= BIT_PS1:
        return 'ctx.ps1[%d]' % (bit - BIT_PS1)
    if bit >= BIT_FPR:
        return 'ctx.fpr[%d]' % (bit - BIT_FPR)
    b = bit - BIT_CR
    return 'ctx.cr[%d].%s' % (b // 4, ('lt', 'gt', 'eq', 'so')[b % 4])


def bits_of(mask):
    out = []
    b = 0
    while mask:
        if mask & 1:
            out.append(b)
        mask >>= 1
        b += 1
    return out


def spill_stmt(mask):
    return ' '.join('%s = %s;' % (ctx_name(b), local_name(b)) for b in bits_of(mask))


def reload_stmt(mask):
    return ' '.join('%s = %s;' % (local_name(b), ctx_name(b)) for b in bits_of(mask))


class FpUnsupported(Exception):
    pass


_PS = None


def ps_methods():
    global _PS
    if _PS is None:
        _PS = {}
        s = (ROOT / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h').read_text(errors='ignore')
        for m in re.finditer(r'inline void (ps_\w+)\(([^)]*)\) \{\n(.*?)\n  \}', s, re.S):
            params = [x.split()[-1] for x in m.group(2).split(',')]
            body = ' '.join(l.strip() for l in m.group(3).split(NL))
            _PS[m.group(1)] = (params, body)
    return _PS


def split_args(text):
    args = []
    depth = 0
    cur = ''
    for ch in text:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            args.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def build_fp_call(name, args):
    lit = lambda a: re.fullmatch(r'\d+', a.strip()) is not None
    if name.startswith('ps_'):
        methods = ps_methods()
        if name not in methods:
            raise FpUnsupported('unknown ' + name)
        params, body = methods[name]
        if len(params) != len(args) or not all(lit(a) for a in args):
            raise FpUnsupported('arguments of ' + name)
        for p, a in zip(params, args):
            body = re.sub(r'\b' + re.escape(p) + r'\b', a.strip(), body)
        body = re.sub(r'\b(fpr|ps1|cr)\[', r'ctx.\1[', body)
        return '{ ' + body + ' }'
    if name == 'psq_load':
        if len(args) != 4 or not (lit(args[0]) and lit(args[2]) and lit(args[3])):
            raise FpUnsupported('psq_load arguments')
        n, addr, w, i = args
        # NATIVE_PSQ_QUANT_INLINE_V1: psq_load_pair handles f32 and the 8/16-bit quantised types inline
        return ('{ auto pq_ = ctx.psq_load_pair(' + addr + ', ' + w + ', ' + i + '); ctx.fpr[' + n + '] = pq_.f; ctx.ps1[' + n + '] = pq_.p; }')
    if name == 'psq_store':
        if len(args) != 4 or not (lit(args[0]) and lit(args[2]) and lit(args[3])):
            raise FpUnsupported('psq_store arguments')
        n, addr, w, i = args
        return ('{ ctx.psq_store_val(ctx.fpr[' + n + '], ctx.ps1[' + n + '], ' + addr + ', ' + w + ', ' + i + '); }')
    raise FpUnsupported(name)


MEMCPY_W = re.compile(r'std::memcpy\(&ctx\.(fpr|ps1)\[(\d+)\],\s*&(\w+),\s*(\d+)\);')
MEMCPY_R = re.compile(r'std::memcpy\(&(\w+),\s*&ctx\.(fpr|ps1)\[(\d+)\],\s*(\d+)\);')


def expand_fp(code):
    """Rewrite FP helper calls into statements over ctx.fpr[N] / ctx.ps1[N] so the promotion pass can see (and localise) every access."""
    out = code
    while True:
        m = re.search(r'ctx\.(ps_\w+|psq_load|psq_store)\(', out)
        if not m:
            break
        open_i = m.end() - 1
        depth = 0
        j = open_i
        while j < len(out):
            if out[j] == '(':
                depth += 1
            elif out[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(out) or out[j + 1:j + 2] != ';':
            raise FpUnsupported('call is not a statement')
        out = out[:m.start()] + build_fp_call(m.group(1), split_args(out[open_i + 1:j])) + out[j + 2:]
    out = MEMCPY_W.sub(lambda m: '{ double t_ = ctx.%s[%s]; std::memcpy(&t_, &%s, %s); ctx.%s[%s] = t_; }' % (m.group(1), m.group(2), m.group(3), m.group(4), m.group(1), m.group(2)), out)
    out = MEMCPY_R.sub(lambda m: '{ double t_ = ctx.%s[%s]; std::memcpy(&%s, &t_, %s); }' % (m.group(2), m.group(3), m.group(1), m.group(4)), out)
    if re.search(r'&ctx\.(?:fpr|ps1)', out):
        raise FpUnsupported('address of an FP register')
    return out


class Line:
    __slots__ = ('text', 'code', 'comment', 'kind', 'uses', 'writes', 'mustdef', 'has_return', 'gotos', 'dbefore', 'lafter', 'ic_sync', 'fp_guard')


def classify(code, fp_mode=False):
    s = code.lstrip()
    if BLOCKCALL.match(code):
        return 'blockcall'
    if s.startswith(POLL_PREFIX):
        return 'poll'
    if re.search(r'\bctx\b(?!\.)', code):
        return 'barrier'
    for m in re.finditer(r'ctx\.(\w+)\s*\(', code):
        name = m.group(1)
        if name in WHITELIST_EXACT:
            continue
        if name.startswith(WHITELIST_PREFIX) and (fp_mode or not name.startswith('ps_cmp')):
            continue
        return 'barrier'
    return 'plain'


def analyse_line(text, depth):
    ln = Line()
    ln.text = text
    ln.code = code_of(text)
    ln.comment = text[len(ln.code):]
    ln.kind = classify(ln.code)
    if ln.kind == 'barrier' and depth > 0:
        ln.kind = 'cbarrier'    # inside a block it may not execute: spill/reload stay inside it and the dirty set is unchanged afterwards
    ln.has_return = bool(RETURN.search(ln.code))
    ln.gotos = GOTO.findall(ln.code)
    ln.ic_sync = bool(IC_SYNC.search(ln.code))
    ln.fp_guard = bool(FP_GUARD.search(ln.code))
    uses = writes = mustdef = 0
    refs = list(ANY_REF.finditer(ln.code))
    stripped = ln.code.strip()
    chain_end = -1       # a leading `a = b = ... = expr;` statement defines every target of the chain
    if depth == 0 and refs and ln.kind == 'plain' and stripped.count(';') == 1 and stripped.endswith(';'):
        pos = 0
        base = ln.code.index(stripped)
        while True:
            m = ANY_REF.match(stripped, pos)
            if not m:
                break
            a = re.compile(r'\s*=(?!=)').match(stripped, m.end())
            if not a:
                break
            mustdef |= 1 << bit_of(m)
            chain_end = base + a.end()
            pos = a.end()
            while pos < len(stripped) and stripped[pos] == ' ':
                pos += 1
            if not ANY_REF.match(stripped, pos):
                break
    for m in refs:
        bit = bit_of(m)
        tail = ln.code[m.end():]
        head = ln.code[:m.start()]
        is_write = bool(ASSIGN_AFTER.match(tail)) or bool(INCDEC_BEFORE.search(head))
        compound = bool(re.match(r'\s*(?:[-+*/%&|^]=|<<=|>>=|\+\+|--)', tail)) or bool(INCDEC_BEFORE.search(head))
        if is_write:
            writes |= 1 << bit
        # a plain `=` target is only written (its old value is not read here); anything else reads it
        if (not is_write) or compound:
            uses |= 1 << bit
    ln.uses, ln.writes, ln.mustdef = uses, writes, mustdef
    if ln.kind in ('barrier', 'cbarrier', 'poll', 'blockcall'):
        ln.uses = ln.writes = ln.mustdef = 0
    return ln


class Unit:
    __slots__ = ('label', 'lines', 'succ', 'falls', 'din', 'dout', 'lin', 'lout')


def parse(text):
    lines = text.split(NL)
    if not FUNC_HEAD.match(lines[0]) or lines[-1] != '}':
        return None
    name = FUNC_HEAD.match(lines[0]).group(1)
    entry = int(name[5:], 16)
    i = 1
    trace = None
    if lines[i].startswith('    NWII_TRACE_CALL('):
        trace = lines[i]
        i += 1
    cases = []                      # (addr_text, label)
    special = []                    # verbatim case/default lines that are not gotos
    if lines[i].startswith('    if (ctx.pc != 0x'):
        i += 1
        # NATIVE_PERF_CENSUS_V1: the pristine chunk counts the interior entry
        # before the switch. It has no effect on the guest state the promoted
        # body reproduces and is dropped with the switch.
        if lines[i].startswith('        native_perf_census::GuestSwitch('):
            i += 1
        if not lines[i].startswith('        switch (ctx.pc) {'):
            return None
        i += 1
        while not lines[i].startswith('        }'):
            m = re.match(r'^            case (0x[0-9A-F]+): goto loc_([0-9A-F]{8});$', lines[i])
            if m:
                cases.append((m.group(1), m.group(2)))
            elif lines[i].startswith('            case 0x0: return;') or lines[i].startswith('            default: nwii::runtime::interpret_step(ctx); return;'):
                special.append(lines[i])
            else:
                return None
            i += 1
        i += 1
        if lines[i] != '    }':
            return None
        i += 1
    body = lines[i:-1]
    return name, entry, trace, cases, special, body


NEST_LINE = re.compile(r'^(\s*)if \(ctx\.ctr\) \{ ctx\.pc = ctx\.ctr; return; \} else nwii::runtime::note_null_call\((0x[0-9A-F]{8}), ctx\.lr\);(\s*//.*)?$')
LR_LINE = re.compile(r'^\s*ctx\.lr = (0x[0-9A-F]{8});')


def nest_indirect(body, func_name, nest):
    """bctrl (`ctx.lr = RET; if (ctx.ctr) { ctx.pc = ctx.ctr; return; } else ...`) -> call the target inline (native_call_indirect) and
    continue at RET when the callee returns normally; any other pc goes back to the dispatcher exactly as the old return did.
    Only when `nest(func_name, RET)` confirms that dispatching RET would enter this very function."""
    labels = {LABEL.match(l).group(1) for l in body if LABEL.match(l)}
    out = []
    prev = None
    count = 0
    for l in body:
        m = NEST_LINE.match(l)
        if m and prev is not None and prev.group(1)[2:] in labels and nest(func_name, int(prev.group(1), 16)):
            ret = prev.group(1)
            l = (m.group(1) + 'if (ctx.ctr) { ctx.pc = ctx.ctr; nwii::runtime::native_call_indirect(ctx); if (ctx.pc != ' + ret + ') return; '
                 'process_pending_callbacks(ctx); if (ctx.pc != ' + ret + ') return; } else nwii::runtime::note_null_call(' + m.group(2) + ', ctx.lr);' + (m.group(3) or ''))
            count += 1
        if code_of(l).strip():
            prev = LR_LINE.match(l)
        out.append(l)
    return out, count


def promote(text, promote_cr=True, promote_fp=True, nest=None):
    """Return (new_text, stats) or (None, reason). FP promotion falls back to integer-only when a function uses an unexpandable FP form."""
    new, st = _promote(text, promote_cr, promote_fp, nest)
    if new is None and promote_fp and isinstance(st, str) and st.startswith('fp unsupported'):
        new, st = _promote(text, promote_cr, False, nest)
    return new, st


def _promote(text, promote_cr=True, promote_fp=True, nest=None):
    parsed = parse(text)
    if parsed is None:
        return None, 'unrecognised preamble'
    name, entry, trace, cases, special, body = parsed
    nested = 0
    if nest is not None:
        body, nested = nest_indirect(body, name, nest)
    joined = NL.join(body)
    for token in UNSUPPORTED:
        if token in joined:
            return None, 'unsupported token ' + token
    dynamic_cr = bool(re.search(r'ctx\.cr\[(?!\d\])', joined)) or bool(re.search(r'ctx\.cr\[\d\](?!\.(?:lt|gt|eq|so))', joined))
    if re.search(r'ctx\.gpr\[(?!\d+\])', joined) or re.search(r'ctx\.gpr(?!\[)', joined):
        return None, 'dynamic gpr index'
    use_cr = promote_cr and not dynamic_cr
    use_fp = promote_fp
    if use_fp and (re.search(r'ctx\.(?:fpr|ps1)\[(?!\d+\])', joined) or re.search(r'ctx\.(?:fpr|ps1)(?!\[)', joined)):
        return None, 'fp unsupported: dynamic index'
    # split into units
    units = []
    cur = Unit()
    cur.label = None
    cur.lines = []
    depth = 0
    for text_line in body:
        m = LABEL.match(text_line)
        if m:
            if depth != 0:
                return None, 'label inside block'
            units.append(cur)
            cur = Unit()
            cur.label = m.group(1)
            cur.lines = []
            continue
        if use_fp:
            code0 = code_of(text_line)
            if classify(code0, True) == 'plain':
                try:
                    text_line = expand_fp(code0) + text_line[len(code0):]
                except FpUnsupported as e:
                    return None, 'fp unsupported: ' + str(e)
        ln = analyse_line(text_line, depth)
        cur.lines.append(ln)
        depth += ln.code.count('{') - ln.code.count('}')
    units.append(cur)
    if depth != 0:
        return None, 'unbalanced braces'
    index = {u.label: k for k, u in enumerate(units) if u.label}
    for u in units:
        for ln in u.lines:
            for g in ln.gotos:
                if g not in index:
                    return None, 'goto to unknown label'
            if ln.kind in ('barrier', 'cbarrier') and ln.gotos:
                return None, 'barrier line with goto'
    for _, label in cases:
        if label not in index:
            return None, 'case label missing'
    if not use_fp:
        for u in units:
            for ln in u.lines:
                ln.uses &= ~FP_MASK
                ln.writes &= ~FP_MASK
                ln.mustdef &= ~FP_MASK
    # CR class off: strip CR bits from every line's masks and keep ctx text
    if not use_cr:
        crmask = 0
        for b in range(BIT_CR, BIT_CR + 32):
            crmask |= 1 << b
        for u in units:
            for ln in u.lines:
                ln.uses &= ~crmask
                ln.writes &= ~crmask
                ln.mustdef &= ~crmask
    # control flow
    for k, u in enumerate(units):
        succ = set()
        falls = True
        last = None
        for ln in u.lines:
            for g in ln.gotos:
                succ.add(index[g])
            if ln.code.strip():
                last = ln.code.strip()
        if last is not None:
            if re.match(r'^goto loc_[0-9A-F]{8};$', last) or re.match(r'^(?:ctx\.pc = [^;]+; )?return;$', last):
                falls = False
        if falls and k + 1 < len(units):
            succ.add(k + 1)
        u.succ = sorted(succ)
        u.falls = falls
    n = len(units)
    preds = [[] for _ in range(n)]
    for k, u in enumerate(units):
        for s in u.succ:
            preds[s].append(k)
    entries = {0} | {index[l] for _, l in cases}
    # forward may-dirty
    for u in units:
        u.din = 0
        u.dout = 0

    def sweep_forward(u, d):
        for ln in u.lines:
            ln.dbefore = d
            if ln.kind == 'barrier':
                d = 0
            elif ln.kind in ('poll', 'blockcall', 'cbarrier'):
                pass
            else:
                if ln.ic_sync:
                    d &= ~(1 << BIT_IC)
                d |= ln.writes
        return d
    work = list(range(n))
    inq = set(work)
    while work:
        k = work.pop(0)
        inq.discard(k)
        u = units[k]
        din = 0
        for p in preds[k]:
            din |= units[p].dout
        u.din = din
        dout = sweep_forward(u, din)
        if dout != u.dout:
            u.dout = dout
            for s in u.succ:
                if s not in inq:
                    work.append(s)
                    inq.add(s)
    for u in units:
        u.dout = sweep_forward(u, u.din)
    # backward liveness (spills of dirty sets count as uses)
    for u in units:
        u.lin = 0
        u.lout = 0

    def sweep_backward(u, live):
        for ln in reversed(u.lines):
            if ln.kind == 'barrier':
                ln.lafter = live
                live = ln.dbefore
            elif ln.kind in ('poll', 'blockcall', 'cbarrier'):
                ln.lafter = live
                live = live | ln.dbefore
            else:
                ln.lafter = live
                after = live
                if ln.has_return:
                    after |= ln.dbefore | ln.writes
                if ln.ic_sync:
                    after |= (1 << BIT_IC)
                live = (after & ~ln.mustdef) | ln.uses
                if ln.has_return and not (ln.mustdef):
                    pass
        return live
    changed = True
    work = list(range(n - 1, -1, -1))
    inq = set(work)
    while work:
        k = work.pop(0)
        inq.discard(k)
        u = units[k]
        lout = 0
        for s in u.succ:
            lout |= units[s].lin
        u.lout = lout
        lin = sweep_backward(u, lout)
        if lin != u.lin:
            u.lin = lin
            for p in preds[k]:
                if p not in inq:
                    work.append(p)
                    inq.add(p)
    for u in units:
        sweep_backward(u, u.lout)
    # does an unconditional-terminator unit still count fall-through live-out? (no: succ excludes it)
    referenced = 0
    for u in units:
        for ln in u.lines:
            referenced |= ln.uses | ln.writes
    if not referenced:
        return None, 'no promotable references'
    # ---- emission ----
    out = [lines_head(text)]
    if trace:
        out.append(trace)
    decl = []
    gprs = [b for b in bits_of(referenced) if b < 32]
    if gprs:
        decl.append('    uint32_t ' + ', '.join('g%d' % b for b in gprs) + ';')
    if referenced >> BIT_CTR & 1:
        decl.append('    uint32_t ctrv;')
    if referenced >> BIT_IC & 1:
        decl.append('    uint64_t ic;')
    if referenced >> BIT_XER & 1:
        decl.append('    uint32_t xer;')
    fps = [b for b in bits_of(referenced) if BIT_FPR <= b < BIT_XER]
    if fps:
        decl.append('    double ' + ', '.join(local_name(b) for b in fps) + ';')
    crs = [b for b in bits_of(referenced) if BIT_CR <= b < BIT_CR + 32]
    if crs:
        decl.append('    bool ' + ', '.join(local_name(b) for b in crs) + ';')
    out.extend(decl)

    def sanitise_mask(mask):
        return mask & referenced

    def flat_case_lines(live, members):
        return ['            case %s: %sgoto loc_%s;' % (addr, (reload_stmt(live) + ' ') if live else '', label)
                for addr, label in members]

    def grouped_case_lines(live, members):
        """One reload block for every case whose label shares this live-in set, then a nested
        dispatch to the individual target. Empty or single-member groups keep the flat form."""
        if not live or len(members) == 1:
            return flat_case_lines(live, members)
        assert len({a for a, _ in members}) == len(members)
        lines = ['            ' + ' '.join('case %s:' % a for a, _ in members),
                 '                ' + reload_stmt(live),
                 '                switch (ctx.pc) {']
        lines += ['                    case %s: goto loc_%s;' % (addr, label) for addr, label in members]
        lines.append('                    default: goto loc_%s;' % members[-1][1])
        lines.append('                }')
        return lines

    def emit_entry_switch():
        grouped = {}
        order = []
        for addr, label in cases:
            live = sanitise_mask(units[index[label]].lin)
            grouped.setdefault(live, []).append((addr, label))
            if len(grouped[live]) == 1:
                order.append(live)
        assert sum(len(m) for m in grouped.values()) == len(cases)
        for live in order:
            members = grouped[live]
            flat = flat_case_lines(live, members)
            if not entry_trampoline_enabled():
                out.extend(flat)
                continue
            # anchored: the shared materialiser is only sound if every member really has this live-in set
            assert all(sanitise_mask(units[index[l]].lin) == live for _, l in members)
            shared = grouped_case_lines(live, members)
            out.extend(shared if sum(map(len, shared)) < sum(map(len, flat)) else flat)

    if cases or special:
        out.append('    if (ctx.pc != 0x%08X) {' % entry)
        out.append('        switch (ctx.pc) {')
        emit_entry_switch()
        out.extend(special)
        out.append('        }')
        out.append('    }')
    live0 = sanitise_mask(units[0].lin)
    if live0:
        out.append('    ' + reload_stmt(live0))
    exits = {}
    stats = dict(nested=nested, units=n, barriers=0, polls=0, returns=0, spill_bits=0, reload_bits=0, promoted=bin(referenced).count('1'))

    def subst(code):
        def rep(m):
            b = bit_of(m)
            if (not use_cr) and BIT_CR <= b < BIT_CR + 32:
                return m.group(0)
            if (not use_fp) and b >= BIT_FPR:
                return m.group(0)
            return local_name(b)
        return ANY_REF.sub(rep, code)

    for u in units:
        if u.label:
            out.append('loc_%s: ;' % u.label)
        for ln in u.lines:
            if ln.kind in ('barrier', 'cbarrier'):
                sp = sanitise_mask(ln.dbefore)
                rl = sanitise_mask(ln.lafter)
                if sp:
                    out.append('    ' + spill_stmt(sp))
                out.append(ln.text)
                if rl:
                    out.append('    ' + reload_stmt(rl))
                stats['barriers'] += 1
                stats['spill_bits'] += bin(sp).count('1')
                stats['reload_bits'] += bin(rl).count('1')
                continue
            if ln.kind == 'blockcall':
                m = BLOCKCALL.match(ln.code)
                sp = sanitise_mask(ln.dbefore)
                rl = sanitise_mask(units[index[m.group(2)]].lin)
                out.append('    if(native_blocks_on){' + (spill_stmt(sp) + ' ' if sp else '') + m.group(1) + '(ctx);' + (reload_stmt(rl) + ' ' if rl else '') + 'goto loc_' + m.group(2) + ';}' + ln.comment)
                stats['barriers'] += 1
                stats['spill_bits'] += bin(sp).count('1')
                stats['reload_bits'] += bin(rl).count('1')
                continue
            if ln.kind == 'poll':
                sp = sanitise_mask(ln.dbefore)
                rl = sanitise_mask(ln.lafter)
                code = ln.code.rstrip()
                if not code.endswith('}'):
                    return None, 'poll line shape'
                inner = code[len(code.lstrip()) - len(code.lstrip()):]
                head = code[:code.index(POLL_PREFIX) + len(POLL_PREFIX)]
                rest = code[len(head):-1].rstrip()
                new = head + ' ' + (spill_stmt(sp) + ' ' if sp else '') + rest.strip() + ' ' + (reload_stmt(rl) + ' ' if rl else '') + '}'
                out.append(new + ln.comment)
                stats['polls'] += 1
                continue
            code = subst(ln.code)
            if ln.ic_sync and (referenced >> BIT_IC) & 1:
                out.append('    ' + spill_stmt(1 << BIT_IC))
            if ln.has_return:
                dirty = sanitise_mask(ln.dbefore | ln.writes)
                if dirty:
                    exit_id = exits.setdefault(dirty, len(exits))
                    code = RETURN.sub(lambda m, k=exit_id: 'goto exit_%d;' % k, code)
                    stats['returns'] += 1
                if ln.fp_guard:
                    code = FP_GUARD.sub(lambda m: 'if (__builtin_expect(ctx.require_fpu(%s), 0))' % m.group(1), code, count=1)
            out.append(code + ln.comment)
    # falling off the end of the original function returned with ctx already current; here the dirty locals must be written back first
    last = units[-1]
    if last.falls or not last.lines:
        out.append('    ' + (spill_stmt(sanitise_mask(last.dout)) + ' ' if sanitise_mask(last.dout) else '') + 'return;')
    for dirty, k in sorted(exits.items(), key=lambda kv: kv[1]):
        out.append('exit_%d: ;' % k)
        out.append('    ' + spill_stmt(dirty) + ' return;')
    out.append('}')
    stats['exits'] = len(exits)
    return NL.join(out), stats


def lines_head(text):
    return text.split(NL, 1)[0]

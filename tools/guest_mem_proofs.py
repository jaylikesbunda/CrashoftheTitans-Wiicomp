"""Region-hoisted address proofs for promoted guest code (NATIVE_MEM_PROOF_V1).

Every translated guest load/store classifies its address on its own: mask, bank range compares, the
0x0C slot, the watch snapshot, page_bias or the stack watch span. In the 2026-10-19 PMC capture those
helper lines hold about half of all samples on guest memory lines. Most accesses reuse a base register
that an earlier access in the same straight-line region already classified (57% sample-weighted, top 300
functions, proofs dropped at every join, call and poll).

This transform gives such a group one proof (runtime-overlays/native_mem_proof.h) and rewrites each
member as

    read : (okN ? native_mem_proof::read32(pmN + OFF) : <original read expression>)
    write: if (__builtin_expect(okN, 1)) native_mem_proof::write32(pmN + OFF, VALUE); else <original statement>

so a failed proof (MMIO/FIFO base, watch overlap, bank edge) keeps exactly the original per-access path.

Soundness rules, all syntactic over the promoted text of one function:
  * a region ends at every label that is a jump target, every line that passes ctx to anything other
    than the recognised accessors (calls, polls, helpers, PSQ/PS methods, block calls), and every line
    with a statement the scanner does not understand (any `{` opening a conditional or loop body);
  * a group ends when its base local is assigned (including a load into the base itself, after that
    access) and never crosses a region boundary;
  * a proof is only established at brace depth 0 of the function body, so it dominates the rest of the
    group, which lies in the same straight-line region;
  * only plain `native_fastmem::readN(ctx.mmu, gB [+ C])`, `native_guest_fast::writeN(ctx.mmu, gB [+ C], V)`
    and `ctx.mmu.writeN(gB [+ C], V)` forms are rewritten; anything else is left untouched;
  * groups of one access are left untouched (nothing to share).
"""
import re

MARK = 'NATIVE_MEM_PROOF_V1'
WIDTH = {'8': 1, '16': 2, '32': 4, '64': 8, '_f32': 4, '_f64': 8}
READ = re.compile(r'native_fastmem::read(8|16|32|64|_f32|_f64)\(ctx\.mmu,\s*g(\d+)(?:\s*\+\s*(-?\d+))?\)')
WRITE_FAST = re.compile(r'^(?P<pre>\s*(?:/\*.*?\*/\s*)?)native_guest_fast::write(?P<w>8|16|32|64|_f32|_f64)\(ctx\.mmu,\s*g(?P<r>\d+)(?:\s*\+\s*(?P<o>-?\d+))?,\s*(?P<v>[^;]*?)\);(?P<post>\s*(?://.*)?)$')
WRITE_MMU = re.compile(r'^(?P<pre>\s*(?:/\*.*?\*/\s*)?)ctx\.mmu\.write(?P<w>8|16|32|64|_f32|_f64)\(g(?P<r>\d+)(?:\s*\+\s*(?P<o>-?\d+))?,\s*(?P<v>[^;]*?)\);(?P<post>\s*(?://.*)?)$')
LABEL = re.compile(r'^\s*([A-Za-z_]\w*):\s*;?\s*$')
GOTO = re.compile(r'goto\s+([A-Za-z_]\w*)\s*;')
CONDITIONAL_PREFIX = re.compile(r'^\s*(?:\}\s*)?(?:if|else|for|while|do|switch|case|default)\b')


def _code(line):
    """The line with comments and string/char literals blanked (same length, so offsets line up)."""
    out = list(line); i = 0; n = len(line)
    while i < n:
        if line.startswith('//', i):
            for j in range(i, n): out[j] = ' '
            break
        if line.startswith('/*', i):
            j = line.find('*/', i + 2); j = n if j < 0 else j + 2
            for k in range(i, j): out[k] = ' '
            i = j; continue
        if line[i] in '"\'':
            q = line[i]; j = i + 1
            while j < n and line[j] != q:
                j += 2 if line[j] == '\\' else 1
            for k in range(i + 1, min(j, n)): out[k] = ' '
            i = j + 1; continue
        i += 1
    return ''.join(out)


def _braces(line):
    code = _code(line)
    return [(i, c) for i, c in enumerate(code) if c in '{}']


def _assigned(line, reg):
    # Assignment, increment, or address taken (a helper may then write it).
    return re.search(r'(?<![\w.])g' + reg + r'\s*(?:=(?!=)|[-+*/%&|^]=|<<=|>>=|\+\+|--)|(?:\+\+|--)\s*g' + reg + r'\b|&\s*g' + reg + r'\b', line) is not None


def _strip_accessors(line):
    line = READ.sub('', line)
    for pattern in (WRITE_FAST, WRITE_MMU):
        line = pattern.sub(lambda m: m['pre'] + m['v'], line)
    return line


def _barrier(line):
    # Anything that still passes ctx once the recognised accessors are removed can observe or change state.
    s = _strip_accessors(line)
    s = re.sub(r'//.*$', '', s)
    s = re.sub(r'/\*.*?\*/', '', s)
    # `ctx.require_fpu(pc)` and plain field reads do not pass ctx on; on failure they leave the region by goto.
    # Methods on ctx other than those (PSQ/PS helpers) and every call taking ctx end the region.
    s = re.sub(r'ctx\.require_fpu\(', '(', s)
    return bool(re.search(r'\bctx\s*[,)]|\bctx\.(?!mmu\b|gpr\b|fpr\b|ps1\b|cr\b|ctr\b|xer\b|lr\b|pc\b|msr\b|inst_count\b|gqr\b)\w+\s*\(|native_poll_due|process_pending|interpret_step|native_call|native_dispatch|longjmp|setjmp', s))


def _accesses(line):
    out = []
    for m in READ.finditer(line):
        out.append(('r', m[2], int(m[3] or 0), WIDTH[m[1]]))
    for pattern in (WRITE_FAST, WRITE_MMU):
        m = pattern.match(line)
        if m:
            out.append(('w', m['r'], int(m['o'] or 0), WIDTH[m['w']]))
    return out


def transform_body(lines, counter):
    """Return (new_lines, declarations, stats) for the body lines of one function."""
    targets = set()
    for line in lines:
        targets.update(GOTO.findall(line))
    # Pass 1: group accesses.
    groups = []          # each: dict(reg, members=[(line_index, kind, off, width)], start)
    open_group = {}      # reg -> group
    depth = 0
    conditional = []     # stack of bools: is this brace scope conditional?
    for i, line in enumerate(lines):
        label = LABEL.match(line)
        if label and label[1] in targets:
            open_group = {}
        if _barrier(line):
            open_group = {}
        unconditional = not any(conditional)
        for kind, reg, off, width in _accesses(line):
            g = open_group.get(reg)
            if g is None:
                if unconditional and depth == 0:
                    g = dict(reg=reg, members=[], start=i)
                    open_group[reg] = g
                    groups.append(g)
                else:
                    continue
            g['members'].append((i, kind, off, width))
        for reg in list(open_group):
            if _assigned(line, reg):
                del open_group[reg]
        # Brace tracking, in source order: a conditional/loop body ends every group (it may not run, or run twice).
        for at, ch in _braces(line):
            if ch == '{':
                cond = bool(CONDITIONAL_PREFIX.match(_code(line)[:at]))
                if cond:
                    open_group = {}
                conditional.append(cond)
                depth += 1
            else:
                if conditional:
                    conditional.pop()
                depth = max(0, depth - 1)
    # Pass 2: rewrite groups with at least two members.
    rewrite = {}         # line index -> list of (group id, member)
    decls = []
    proofs = {}          # start line -> proof statement
    stats = dict(groups=0, accesses=0)
    for g in groups:
        if len(g['members']) < 2:
            continue
        n = counter[0] = counter[0] + 1
        lo = min(off for _, _, off, _ in g['members'])
        hi = max(off + width for _, _, off, width in g['members'])
        decls.append('uint32_t pm%d; bool ok%d;' % (n, n))
        proofs.setdefault(g['start'], []).append(
            'pm%d = g%s & 0x3fffffffu; ok%d = native_mem_proof::Span(ctx.mmu, pm%d, %d, %d); /* %s */'
            % (n, g['reg'], n, n, lo, hi, MARK))
        for member in g['members']:
            slot = rewrite.setdefault(member[0], [])
            if (n, g['reg']) not in slot:      # one rewrite per line and group: it handles every member on the line
                slot.append((n, g['reg']))
        stats['groups'] += 1
        stats['accesses'] += len(g['members'])
    out = []
    for i, line in enumerate(lines):
        for proof in proofs.get(i, []):
            indent = re.match(r'\s*', line)[0]
            out.append(indent + proof)
        for n, reg in rewrite.get(i, []):
            line = _rewrite_line(line, n, reg)
        out.append(line)
    return out, decls, stats


def _off(text):
    value = int(text or 0)
    return '' if value == 0 else ' + uint32_t(%d)' % value


def _addr(reg, off):
    value = int(off or 0)
    return 'g%s' % reg if value == 0 else 'g%s + %d' % (reg, value)


def _rewrite_line(line, n, reg):
    # The failed-proof branch calls the original accessor out of line (native_mem_proof::cold_*), with the same
    # address expression and value, so the hot code holds one predicted branch plus the direct access.
    for pattern, cold in ((WRITE_FAST, 'cold_fast_write'), (WRITE_MMU, 'cold_mmu_write')):
        m = pattern.match(line)
        if m and m['r'] == reg:
            return ('%sif (__builtin_expect(ok%d, 1)) native_mem_proof::write%s(pm%d%s, %s); '
                    'else native_mem_proof::%s%s(ctx.mmu, %s, %s);%s'
                    % (m['pre'], n, m['w'], n, _off(m['o']), m['v'], cold, m['w'], _addr(m['r'], m['o']), m['v'], m['post']))
    def read(m):
        if m[2] != reg:
            return m[0]
        return ('(__builtin_expect(ok%d, 1) ? native_mem_proof::read%s(pm%d%s) : native_mem_proof::cold_read%s(ctx.mmu, %s))'
                % (n, m[1], n, _off(m[3]), m[1], _addr(m[2], m[3])))
    return READ.sub(read, line)


HEAD = re.compile(r'^(?:static )?(?:NWII_GUEST_ABI )?(?:__attribute__\(\([^\n]*?\)\)\s+)*void (?:func|native_guest_(?:primary|resume|continuation))_([0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{$', re.M)


def transform_function(text, counter):
    """`text` is one complete definition (head line ... closing brace)."""
    lines = text.split('\n')
    head, body, tail = lines[0], lines[1:-1], lines[-1]
    new_body, decls, stats = transform_body(body, counter)
    if not decls:
        return text, stats
    return '\n'.join([head, '    ' + ' '.join(decls) + ' /* ' + MARK + ' */'] + new_body + [tail]), stats

"""Guest-register effect analysis for the translated guest code.

The analysis answers the question the native lowering depends on: after a call
to a guest function, can any code observe the value that call left in a guest
register?

The generated guest code is analysed at statement level:

  * a block ends where a `loc_XXXX:` label starts,
  * a block's successors are the labels it branches to plus the block that
    follows it, unless the block ends in an unconditional branch or return,
  * a statement writes the register on its left hand side and reads every
    other register it mentions - including a register it also writes
    (`ctx.gpr[3] = ctx.gpr[3] + 1` reads the old value),
  * a call that receives the context can observe every register the callee
    reads before writing it, so the callee's read-before-write set counts as
    observed at the call site.

The analysis is deliberately conservative: it reports an observation when a
reachable path *may* read the register. A candidate whose registers cannot be
proven unobservable is rejected, not adopted.
"""
import re

# Register references in the generated guest code.
GPR = re.compile(r'ctx\.gpr\[(\d+)\]')
FPR = re.compile(r'ctx\.fpr\[(\d+)\]')
PS1 = re.compile(r'ctx\.ps1\[(\d+)\]')
CR = re.compile(r'ctx\.cr\[(\d+)\]\.(\w+)')
PSQ_LOAD = re.compile(r'ctx\.psq_load\((\d+)')
PSQ_STORE = re.compile(r'ctx\.psq_store\((\d+)')
# A call that receives the guest context.
CONTEXT_CALL = re.compile(r'\bfunc_([0-9A-Fa-f]{8})\(ctx\)')
# A call whose register reads are not known from the call site.
OPAQUE_CALL = re.compile(
    r'\b(?:nwii::runtime::)?(?:native_call_indirect|native_dispatch_nested|'
    r'process_pending_callbacks|interpret_step|micro_interpret|'
    r'native_guest_fast::TailEnter)\b')
# An assignment at the start of a statement.
ASSIGNMENT = re.compile(r'^(.*?)\s*(=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=)(?!=)\s*(.*)$', re.S)
LABEL = re.compile(r'^(loc_[0-9A-Fa-f]{8}):$')
GOTO = re.compile(r'\bgoto (loc_[0-9A-Fa-f]{8});')
# The guest's frame: the stack pointer moves and the saved registers live at
# known offsets from the entry stack pointer.
# The statement splitter removes the trailing ';', so the frame patterns must
# not require it.
SP_ADD = re.compile(r'ctx\.gpr\[1\] = ctx\.gpr\[1\] \+ (-?\d+)')
SP_FRAME = re.compile(r'uint32_t ea = ctx\.gpr\[1\] \+ (-?\d+)')
STORE_KINDS = ('write8', 'write16', 'write32', 'write64', 'write_f32', 'write_f64')
READ_KINDS = ('read8', 'read16', 'read32', 'read64', 'read_f32', 'read_f64')


def body_of(text):
    """The statements of a guest function, without its outer braces."""
    start = text.find('{')
    if start < 0:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return text[start + 1:]


def split_statements(body):
    """Split a function body into statements.

    A statement ends at a `;` that is not inside braces, brackets or
    parentheses, so `if (c) { a; b; }` stays one statement.
    """
    out = []
    depth = 0
    start = 0
    for i, ch in enumerate(body):
        if ch in '({[':
            depth += 1
        elif ch in ')}]':
            depth -= 1
        elif ch == ';' and depth == 0:
            text = body[start:i].strip()
            if text:
                out.append(text)
            start = i + 1
    text = body[start:].strip()
    if text:
        out.append(text)
    return out


def blocks_of(text):
    """The basic blocks of a guest function: [(label, [statements])]."""
    blocks = []
    current = ('ENTRY', [])
    for statement in split_statements(body_of(text)):
        m = LABEL.match(statement)
        if m:
            blocks.append(current)
            current = (m[1], [])
            continue
        current[1].append(statement)
    blocks.append(current)
    return blocks


def block_successors(block, position, labels, known):
    """The blocks a basic block can transfer control to.

    A conditional branch has two successors: the branch target and the block
    that follows. A block that ends in an unconditional branch or a return has
    only the successors it names.
    """
    label, statements = block
    last = statements[-1] if statements else ''
    successors = []
    for statement in statements:
        for target in GOTO.findall(statement):
            if target not in successors:
                successors.append(target)
    unconditional_goto = bool(re.fullmatch(r'.*\bgoto (loc_[0-9A-Fa-f]{8});', last)) \
        and 'if (' not in last
    unconditional_return = bool(re.match(r'^\s*return\b', last))
    if not unconditional_goto and not unconditional_return:
        nxt = labels[position + 1] if position + 1 < len(labels) else None
        if nxt and nxt not in successors:
            successors.append(nxt)
    return [s for s in successors if s in known]


def statement_effects(statement):
    """The registers a statement writes and the registers it reads.

    The left hand side of an assignment is the written register; every other
    register the statement mentions is read. A compound assignment also reads
    the register it writes.
    """
    written = set()
    read = set()
    m = ASSIGNMENT.match(statement)
    lhs = ''
    rhs = statement
    compound = False
    if m and not statement.lstrip().startswith('if '):
        lhs, op, rhs = m[1], m[2], m[3]
        compound = op != '='
    targets = register_names(lhs)
    written |= targets
    for name in register_names(rhs):
        read.add(name)
    if compound:
        for name in targets:
            read.add(name)
    for m in PSQ_LOAD.finditer(statement):
        written.add('fpr%s' % m[1])
        written.add('ps1%s' % m[1])
    for m in PSQ_STORE.finditer(statement):
        written.add('fpr%s' % m[1])
        written.add('ps1%s' % m[1])
    return written, read


def register_names(text):
    """The guest registers a piece of text mentions."""
    names = set()
    for m in GPR.finditer(text):
        names.add('gpr%s' % m[1])
    for m in FPR.finditer(text):
        names.add('fpr%s' % m[1])
    for m in PS1.finditer(text):
        names.add('ps1%s' % m[1])
    for m in CR.finditer(text):
        names.add('cr%s' % m[1])
    return names


def statement_calls(statement):
    """The guest functions a statement calls with the context."""
    return [m[1].upper() for m in CONTEXT_CALL.finditer(statement)]


def statement_is_opaque_call(statement):
    """True when a statement calls something whose register reads are unknown."""
    return bool(OPAQUE_CALL.search(statement))


PREAMBLE = re.compile(r'^if \(ctx\.pc != 0x[0-9A-Fa-f]{8}\) \{')


def normal_entry_blocks(text):
    """The blocks reachable from a guest function's ordinary entry.

    The generated code starts every guest function with an interior-entry
    switch (`if (ctx.pc != ADDR) { switch (ctx.pc) { ... } }`). The ordinary
    entry does not take it: the native path only ever serves the ordinary
    entry, so the interior entries must not be taken into account.
    """
    blocks = blocks_of(text)
    if blocks and blocks[0][1] and PREAMBLE.match(blocks[0][1][0]):
        blocks = [(blocks[0][0], blocks[0][1][1:])] + blocks[1:]
    return blocks


def function_read_before_write(text):
    """The registers a function reads before it writes them.

    A register the function reads on a path where it has not written it yet
    may still hold the caller's value, so a caller that passes the context to
    this function cannot assume the register is untouched.

    The search is per register and path sensitive: the translator often puts a
    label between a compare and the branch that uses it, so a block that
    starts with a branch on a condition register does not read the caller's
    value.
    """
    blocks = normal_entry_blocks(text)
    labels = [label for label, _ in blocks]
    known = {label: i for i, label in enumerate(labels)}
    reads = set()
    for register in ALL_REGISTERS:
        if register_read_before_write(blocks, known, register):
            reads.add(register)
    return reads


def register_read_before_write(blocks, known, register):
    """True when some path from the entry reads `register` before writing it."""
    entry = blocks[0][0]
    stack = [(entry, False)]
    seen = set()
    while stack:
        current, written = stack.pop()
        key = (current, written)
        if key in seen:
            continue
        seen.add(key)
        position = known[current]
        label, statements = blocks[position]
        stop = False
        for statement in statements:
            w, r = statement_effects(statement)
            if not written and register in r:
                return True
            if register in w:
                written = True
                stop = True
                break
        if stop:
            continue
        for target in block_successors((label, statements), position,
                                       [l for l, _ in blocks], known):
            stack.append((target, written))
    return False


ALL_REGISTERS = (['gpr%s' % i for i in range(32)] + ['fpr%s' % i for i in range(32)] +
                 ['ps1%s' % i for i in range(32)] + ['cr%s' % i for i in range(8)])


def function_clobbered(text):
    """The guest registers a function leaves modified.

    A register the function saves to its own stack frame and reloads before
    returning keeps its entry value; every other register it writes is
    clobbered.
    """
    written = set()
    restored = set()
    slots = {}
    sp = 0
    sp_delta = 0
    for _label, statements in normal_entry_blocks(text):
        for statement in statements:
            m = SP_FRAME.search(statement)
            if m:
                sp += int(m[1])
                sp_delta += int(m[1])
            m = SP_ADD.search(statement)
            if m:
                sp += int(m[1])
                sp_delta += int(m[1])
            w, _r = statement_effects(statement)
            written |= w
            for kind in STORE_KINDS:
                if ('ctx.mmu.%s(' % kind) not in statement and \
                        ('native_guest_fast::%s(ctx.mmu,' % kind) not in statement:
                    continue
                parts = split_arguments(statement)
                if len(parts) < 2:
                    continue
                offset = frame_offset(parts[0], sp)
                if offset is None:
                    continue
                for name in register_names(parts[1]):
                    slots[offset] = name
                    written.add(name)
            for kind in READ_KINDS:
                if ('ctx.mmu.%s(' % kind) not in statement and \
                        ('native_fastmem::%s(ctx.mmu,' % kind) not in statement:
                    continue
                parts = split_arguments(statement)
                if len(parts) < 2:
                    continue
                offset = frame_offset(parts[1], sp)
                if offset is None or offset not in slots:
                    continue
                name = slots[offset]
                written.add(name)
                destination = set()
                if '=' in statement:
                    destination = register_names(statement.split('=', 1)[0])
                if name in destination:
                    # The register is reloaded into itself: it is restored.
                    restored.add(name)
                else:
                    written.add(name)
    if sp_delta == 0:
        # The frame was balanced, so the stack pointer keeps its entry value.
        restored.add('gpr1')
    return sorted(written - restored)


def frame_offset(address, sp):
    """The entry-relative frame offset of an address expression."""
    address = address.strip()
    if address == 'ea':
        return sp
    m = re.fullmatch(r'ctx\.gpr\[1\](?: \+ (-?\d+))?', address)
    if m:
        return sp + int(m[1] or 0)
    return None


def split_arguments(text):
    """The comma separated arguments of a call expression."""
    start = text.find('(')
    if start < 0:
        return []
    depth = 0
    parts = []
    current = ''
    for ch in text[start:]:
        if ch == '(':
            depth += 1
            if depth == 1:
                continue
        elif ch == ')':
            depth -= 1
            if depth == 0:
                parts.append(current.strip())
                break
        if ch == ',' and depth == 1:
            parts.append(current.strip())
            current = ''
            continue
        current += ch
    return parts

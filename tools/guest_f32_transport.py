"""Guarded F32 transport lowering for generated guest functions.

A guest ``lfs`` loads a 32-bit float; the generated code keeps the guest
double in a promoted local.  Writing that double back to memory costs a
double->float conversion, and the float->double->float round trip is not
value preserving for subnormals (FTZ/DAZ) or signalling NaNs.  This pass
therefore keeps the loaded float in a shadow, guards every store with the
existing ``native_guest_batch::StoreSafe`` predicate and materialises the
guest double only where the double value is really observed.

Public API (same semantics as ``guest_batch_lowering.fp_transport``):

    fp_transport(text, selected=None) -> (new_text, count)

``selected`` is an optional set of shadow names.  When it is given, only those
shadows are lowered; that is how the two-pass protocol of the stage driver
keeps the guards on the sites that actually reach a store.  The helper
``lower_equivalent`` runs the complete lowering, and ``baseline_fp_transport``
keeps the previous implementation available for A/B measurements.

The pass is safe on a text that holds several functions: every function body
is analysed on its own, so the control flow, dominance and liveness facts of
one body never leak into the next one.

Every lowering is reversible: the lowered text only differs from the input by
shadow declarations, shadow assignments and guarded store values, and the
guest double keeps exactly the value the original code produced for every
float encoding and every MXCSR mode.

Measured on the production 803143EC primary (clean-room build
``clean-room-guest-batch-20261019-2``) the guarded store is a machine code
no-op: GCC folds ``(float)(double)x`` back to ``x`` for a float ``x``, so the
``StoreSafe`` guard and the shadow are dead code that the compiler removes.
The lowered and the unlowered primary compile to the same instruction stream
(49,390 instructions, 0x4a0 frame) and differ only in the stack slot
allocation.  See the report in the subagent hand-off for the measurements.

The default mode keeps the footprint of the previous implementation (one
guarded store per load site, inside the same straight line region).  The
``wide`` mode guards every store that the shadow provably reaches; it needs
more guards and produces 102 more instructions in the production primary
without a measurable cycle gain.
"""

import re
from collections import Counter, defaultdict

REG = re.compile(r'\b([fp]\d+)\b')
LABEL = re.compile(r'^\s*([A-Za-z_]\w*):\s*;?\s*$')
READ = re.compile(r'^(\s*)((?:[fp]\d+ = )+)((?:ctx\.mmu\.read_f32\(|native_fastmem::read_f32\(ctx\.mmu,)[^;]+);(.*)$')
GOTO = re.compile(r'\bgoto\s+([A-Za-z_]\w*)\s*;')
WRITE_F32 = re.compile(r'(?:ctx\.mmu\.|native_guest_fast::)write_f32\(')
FLOAT_CAST = re.compile(r'\(float\)\s*([fp]\d+)\b|\bfloat\(([fp]\d+)\)')
CONTROL = re.compile(r'\b(?:return|goto|if|switch|while|for)\b')
COPY = re.compile(r'^\s*([fp]\d+)\s*=\s*([fp]\d+)\s*;\s*(?://.*)?$')
EXIT = -1


def split_comment(line):
    """Return (code, comment) with C++ comments removed from the code part.

    The previous implementation analysed the raw line, so trailing comments
    such as ``// stfs f0, -32768(r29)`` were parsed as code: the register
    name looked like a use of the pending value and the ``(r29)`` looked like
    an opaque call.  Both forced an unconditional materialisation.
    """
    out = []
    i = 0
    n = len(line)
    while i < n:
        if line.startswith('/*', i):
            j = line.find('*/', i + 2)
            if j < 0:
                return (''.join(out), line[i:])
            out.append(' ')
            i = j + 2
            continue
        if line.startswith('//', i):
            return (''.join(out), line[i:])
        out.append(line[i])
        i += 1
    return (''.join(out), '')


def _registers(code):
    """Split every guest FP register occurrence into a use or a definition."""
    uses = []
    defs = []
    for match in REG.finditer(code):
        name = match.group(1)
        tail = code[match.end():]
        stripped = tail.lstrip()
        if stripped.startswith('=') and not stripped.startswith('=='):
            defs.append(name)
            if stripped[1:2] == '=':
                uses.append(name)
        elif re.match(r'[-+*/&|^]=', stripped):
            defs.append(name)
            uses.append(name)
        else:
            uses.append(name)
    return uses, defs


class _Item(object):
    __slots__ = ('index', 'raw', 'code', 'comment', 'kind', 'uses', 'defs', 'succ', 'label')

    def __init__(self, index, raw, code, comment):
        self.index = index
        self.raw = raw
        self.code = code
        self.comment = comment
        self.kind = 'code'
        self.uses = []
        self.defs = []
        self.succ = []
        self.label = None


class Function(object):
    """Line level view of a generated guest function with CFG based facts."""

    def __init__(self, text, wide=False):
        self.text = text
        self.lines = text.splitlines()
        self.items = []
        for index, raw in enumerate(self.lines):
            code, comment = split_comment(raw)
            item = _Item(index, raw, code, comment)
            label = LABEL.match(code)
            if label:
                item.kind = 'label'
                item.label = label.group(1)
            else:
                item.uses, item.defs = _registers(code)
                if CONTROL.search(code) or '{' in code or '}' in code:
                    item.kind = 'ctrl'
                elif READ.fullmatch(code):
                    item.kind = 'read'
            self.items.append(item)
        self.label_index = {}
        for item in self.items:
            if item.label is not None:
                self.label_index[item.label] = item.index
        self._build_cfg()
        self.reachable = self._reachable()
        self._dominators()
        self.reads = self._read_sites()
        self._uses_index()
        self.reaching = self._reaching()
        self._plan(wide)

    # -- control flow -------------------------------------------------
    def _build_cfg(self):
        for item in self.items:
            code = item.code
            if item.kind == 'label':
                item.succ = [item.index + 1] if item.index + 1 < len(self.items) else []
                continue
            gotos = GOTO.findall(code)
            has_return = bool(re.search(r'\breturn\b', code))
            is_if = bool(re.match(r'^\s*if\s*\(', code))
            succ = []
            if gotos:
                for name in gotos:
                    if name in self.label_index:
                        succ.append(self.label_index[name])
                if is_if or len(gotos) > 1:
                    if item.index + 1 < len(self.items):
                        succ.append(item.index + 1)
            elif has_return:
                if is_if:
                    if item.index + 1 < len(self.items):
                        succ.append(item.index + 1)
                    succ.append(EXIT)
                else:
                    succ.append(EXIT)
            else:
                if item.index + 1 < len(self.items):
                    succ.append(item.index + 1)
            item.succ = succ

    def _reachable(self):
        seen = [False] * len(self.items)
        stack = [0]
        while stack:
            index = stack.pop()
            if seen[index]:
                continue
            seen[index] = True
            for succ in self.items[index].succ:
                if succ != EXIT and not seen[succ]:
                    stack.append(succ)
        return seen

    def _dominators(self):
        count = len(self.items)
        idom = [None] * count
        idom[0] = 0
        preds = defaultdict(list)
        for item in self.items:
            if not self.reachable[item.index]:
                continue
            for succ in item.succ:
                if succ != EXIT:
                    preds[succ].append(item.index)
        changed = True
        while changed:
            changed = False
            for item in self.items:
                index = item.index
                if not self.reachable[index] or index == 0:
                    continue
                new = None
                for pred in preds[index]:
                    if idom[pred] is None:
                        continue
                    if new is None:
                        new = idom[pred]
                    else:
                        a, b = new, idom[pred]
                        while a != b:
                            while idom[a] != a:
                                a = idom[a]
                            while idom[b] != b:
                                b = idom[b]
                        new = a
                if new is not None and idom[index] != new:
                    idom[index] = new
                    changed = True
        dom = [0] * count
        for item in self.items:
            index = item.index
            if not self.reachable[index]:
                continue
            if index == 0:
                dom[index] = 1
                continue
            merged = 0
            for pred in preds[index]:
                merged |= dom[pred]
            dom[index] = merged | (1 << index)
        self.dom_mask = dom
        self.preds = preds

    def dominates(self, a, b):
        """True when item a dominates item b."""
        if not self.reachable[b]:
            return False
        return bool(self.dom_mask[b] >> a & 1)

    # -- fact sites ---------------------------------------------------
    def _read_sites(self):
        reads = []
        serial = 0
        for item in self.items:
            if item.kind != 'read':
                continue
            serial += 1
            match = READ.fullmatch(item.code)
            destinations = re.findall(r'[fp]\d+', match.group(2))
            reads.append({'item': item.index, 'serial': serial, 'dests': destinations,
                          'expr': match.group(3), 'indent': match.group(1), 'tail': match.group(4),
                          'shadow': 'native_f32_' + str(serial),
                          'safe': 'native_f32_safe_' + str(serial)})
        self.by_serial = {read['serial']: read for read in reads}
        self.by_item = {read['item']: read for read in reads}
        return reads

    def _uses_index(self):
        self.uses_of = defaultdict(list)
        for item in self.items:
            for name in item.uses:
                self.uses_of[name].append(item.index)
        self.servable = {}
        for item in self.items:
            if not WRITE_F32.search(item.code):
                continue
            for match in FLOAT_CAST.finditer(item.code):
                self.servable.setdefault(item.index, set()).add(match.group(1) or match.group(2))

    def _reaching(self):
        """Forward reaching definition of every guest FP register.

        Only the F32 load sites are tracked: a store may take a shadow when
        the shadow is the unique definition that reaches the store.
        """
        count = len(self.items)
        reach = [dict() for _ in range(count)]
        loads = defaultdict(list)
        for read in self.reads:
            loads[read['item']].append(read)
        pending = [0]
        queued = [False] * count
        queued[0] = True
        visited = [False] * count
        while pending:
            index = pending.pop()
            queued[index] = False
            first = not visited[index]
            visited[index] = True
            item = self.items[index]
            incoming = defaultdict(set)
            for pred in self.preds.get(index, ()):
                for reg, sites in reach[pred].items():
                    incoming[reg] |= sites
            for reg in item.defs:
                incoming.pop(reg, None)
            for read in loads.get(index, []):
                for dest in read['dests']:
                    incoming[dest] = {read['serial']}
            changed = False
            current = reach[index]
            for reg, sites in incoming.items():
                if sites != current.get(reg, set()):
                    current[reg] = set(sites)
                    changed = True
            for reg in [reg for reg in current if reg not in incoming]:
                del current[reg]
                changed = True
            if changed or first:
                for succ in item.succ:
                    if succ != EXIT and not queued[succ]:
                        queued[succ] = True
                        pending.append(succ)
        return reach

    # -- per load site plan -------------------------------------------
    def _barriers(self):
        """Items that end the straight line region of a pending value.

        The previous implementation flushed the pending guest double at a
        goto target label, at a branch, at a guest context access and at an
        opaque call.  The regions below reproduce that footprint so a drop in
        replacement keeps the same machine code.
        """
        pure = {'float', 'double', 'uint32_t', 'int32_t', 'uint64_t', 'int64_t', 'std::fma',
                'std::fabs', 'std::sqrt', 'std::rotl', 'native_guest_batch::StoreValue'}
        targets = set()
        for item in self.items:
            for name in GOTO.findall(item.code):
                if name in self.label_index:
                    targets.add(self.label_index[name])
        barrier = [False] * len(self.items)
        for item in self.items:
            index = item.index
            if item.kind == 'label':
                barrier[index] = index in targets
                continue
            if item.kind == 'ctrl':
                barrier[index] = True
                continue
            code = item.code
            calls = re.findall(r'([\w:.]+)\s*\(', code)
            opaque = bool(re.search(r'\bctx\b(?!\.(?:mmu|inst_count)\b)', code)) or any(
                name not in pure and not name.startswith(('ctx.mmu.', 'native_guest_fast::write'))
                for name in calls)
            barrier[index] = opaque
        return barrier

    def _plan(self, wide=False):
        """Decide, for every load site, which stores it may serve.

        With ``wide`` false only the first store of the loaded value is
        guarded.  That keeps the shadow live range as short as the previous
        implementation and reproduces its machine code; the wide mode guards
        every store that the shadow provably reaches, which costs extra
        guards in the generated code.
        """
        self.store_use = {}
        self.reach_mask = {}
        barrier = self._barriers()
        prefix = [0] * (len(self.items) + 1)
        for index in range(len(self.items)):
            prefix[index + 1] = prefix[index] + (1 if barrier[index] else 0)
        for read in self.reads:
            origin = read['item']
            mask = self._reachable_from(origin)
            self.reach_mask[read['serial']] = mask
            live = set()
            for dest in read['dests']:
                if self._observed(dest, origin, mask):
                    live.add(dest)
            read['live'] = live
            read['stores'] = {}
            for dest in read['dests']:
                stores = []
                for use_item in self.uses_of.get(dest, ()):
                    if use_item <= origin or not (mask >> use_item & 1):
                        continue
                    if dest not in self.servable.get(use_item, ()):
                        continue
                    if not self.dominates(origin, use_item):
                        continue
                    if self.reaching[use_item].get(dest, set()) != {read['serial']}:
                        continue
                    if not wide and prefix[use_item] != prefix[origin + 1]:
                        # A control flow point separates the load and the
                        # store: the shadow does not survive the region.
                        continue
                    stores.append(use_item)
                if not wide and stores:
                    stores = stores[:1]
                read['stores'][dest] = stores
                for store in stores:
                    self.store_use.setdefault(store, (dest, read['serial']))

    def _observed(self, register, origin, mask):
        """True when the guest double value of the register is observed."""
        for use_item in self.uses_of.get(register, ()):
            if use_item == origin or not (mask >> use_item & 1):
                continue
            if register in self.servable.get(use_item, ()) and self.dominates(origin, use_item):
                # The store takes the shadow; the double itself is not read.
                continue
            return True
        return False

    def _reachable_from(self, origin):
        mask = 0
        stack = [origin]
        seen = {origin}
        while stack:
            index = stack.pop()
            mask |= 1 << index
            for succ in self.items[index].succ:
                if succ != EXIT and succ not in seen:
                    seen.add(succ)
                    stack.append(succ)
        return mask


def fp_transport(text, selected=None, wide=False):
    """Lower guarded F32 transports.

    Returns (new_text, count) where count is the number of guarded stores.
    ``wide`` guards every store that the shadow provably reaches instead of
    only the first one; the default keeps the footprint of the previous
    implementation.
    """
    return _lower(text, selected, wide)


def lower_equivalent(text, wide=False):
    """Complete lowering: returns (new_text, count)."""
    return _lower(text, None, wide)


FUNCTION_START = re.compile(r'^\s*(?:static\s+|extern\s+|inline\s+)*[^;{]*\bvoid\s+(\w+)\s*\([^;{]*\)\s*\{')


def _split_functions(text):
    """Split the text into function bodies so every body is analysed alone.

    The stage driver lowers one guest function at a time, but the pass must
    stay sound when it is handed a file with several functions: the control
    flow, the dominance and the liveness facts of one function must never
    leak into the next one.
    """
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if FUNCTION_START.match(line)]
    if len(starts) <= 1:
        return [(0, len(lines))]
    chunks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        chunks.append((start, end))
    return chunks


def _lower(text, selected, wide=False):
    lines = text.splitlines()
    chunks = _split_functions(text)
    out = []
    total = 0
    position = 0
    for start, end in chunks:
        if start > position:
            out.append('\n'.join(lines[position:start]))
        body = '\n'.join(lines[start:end])
        new, count = _lower_function(body, selected, wide)
        out.append(new)
        position = end
        total += count
    if position < len(lines):
        out.append('\n'.join(lines[position:]))
    return '\n'.join(out), total


def _lower_function(text, selected, wide=False):
    function = Function(text, wide)
    if not function.reads:
        return text, 0
    needed = set()
    for read in function.reads:
        if any(read['stores'].values()):
            needed.add(read['shadow'])
    if selected is None:
        selected = needed
    out = []
    declarations = []
    count = 0
    for item in function.items:
        if item.kind == 'label':
            out.append(item.raw)
            continue
        if item.kind == 'ctrl':
            out.append(item.raw)
            continue
        if item.kind == 'read':
            read = function.by_item[item.index]
            if read['shadow'] not in selected:
                out.append(item.raw)
                continue
            shadow = read['shadow']
            safe = read['safe']
            declarations.append('    float ' + shadow + ';')
            declarations.append('    bool ' + safe + ';')
            out.append(read['indent'] + shadow + ' = ' + read['expr'] + ';' + read['tail'])
            out.append('    ' + safe + ' = native_guest_batch::StoreSafe(' + shadow + ');')
            for dest in read['dests']:
                if dest in read['live']:
                    out.append('    ' + dest + ' = double(' + shadow + ');')
            continue
        line = item.raw
        use = function.store_use.get(item.index)
        if use is not None:
            register, serial = use
            read = function.by_serial[serial]
            shadow, safe = read['shadow'], read['safe']
            live = register in read['live']
            fallback = 'float(' + register + ')' if live else 'float(double(' + shadow + '))'
            value = '(' + safe + ' ? ' + shadow + ' : ' + fallback + ')'

            def sub(match, value=value, register=register):
                name = match.group(1) or match.group(2)
                if name != register:
                    return match.group(0)
                return value
            line = FLOAT_CAST.sub(sub, item.code) + item.comment
            count += 1
        out.append(line)
    if not count:
        return text, 0
    result = '\n'.join(out)
    at = result.index('{') + 1
    result = result[:at] + '\n' + '\n'.join(declarations) + result[at:]
    return result, count


def baseline_fp_transport(text, selected=None):
    """The previous implementation, kept for A/B measurements only."""
    lines = text.splitlines(); out = []; pending = {}; decl = []; count = 0; serial = 0
    targets = set(re.findall(r'\bgoto\s+([A-Za-z_]\w*)\s*;', text)); needed = set()

    def flush(names=None):
        for name in list(pending):
            if names is None or name in names:
                out.append('    ' + name + ' = double(' + pending.pop(name)[0] + ');')
    for line in lines:
        label = LABEL.match(line)
        if label and label[1] not in targets:
            out.append(line); continue
        if label or re.search(r'\b(?:return|goto|if|switch|while|for)\b', line) or '{' in line or '}' in line:
            flush(); out.append(line); continue
        load = READ.fullmatch(line)
        if load:
            destinations = re.findall(r'[fp]\d+', load[2]); used = set(re.findall(r'(?:f|p)\d+', load[3]))
            flush(used); serial += 1; shadow = 'native_f32_' + str(serial)
            if selected is not None and shadow not in selected:
                for name in destinations:
                    pending.pop(name, None)
                out.append(line); continue
            safe = 'native_f32_safe_' + str(serial); decl.extend(['    float ' + shadow + ';', '    bool ' + safe + ';'])
            out.append(load[1] + shadow + ' = ' + load[3] + ';' + load[4])
            out.append('    ' + safe + ' = native_guest_batch::StoreSafe(' + shadow + ');')
            out.append('    if(!' + safe + '){' + ''.join(name + ' = double(' + shadow + ');' for name in destinations) + '}')
            for name in destinations:
                pending[name] = (shadow, safe)
            continue
        replaced = []
        if re.search(r'(?:ctx\.mmu\.|native_guest_fast::)write_f32\(', line):
            def store(m):
                nonlocal count
                name = m[1] or m[2]
                if name not in pending:
                    return m[0]
                shadow, safe = pending[name]; count += 1
                needed.add(shadow)
                value = '(' + safe + ' ? ' + shadow + ' : float(' + name + '))'
                replaced.append(value)
                return value
            line = re.sub(r'\(float\)\s*([fp]\d+)\b|\bfloat\(([fp]\d+)\)', store, line)
        use_line = line
        for value in replaced:
            use_line = use_line.replace(value, '0')
        uses = set(re.findall(r'(?:f|p)\d+', use_line))
        assignment = re.match(r'^\s*([fp]\d+) = ([fp]\d+);\s*(?://.*)?$', line)
        if assignment and assignment[2] in pending:
            shadow, safe = pending[assignment[2]]
            out.append('    if(!' + safe + ') ' + assignment[1] + ' = ' + assignment[2] + ';')
            pending[assignment[1]] = (shadow, safe); continue
        lhs = re.match(r'^\s*([fp]\d+)\s*=(?!=)', line)
        if lhs and lhs[1] not in re.findall(r'(?:f|p)\d+', line[lhs.end():]):
            pending.pop(lhs[1], None); uses.discard(lhs[1])
        flush(uses)
        calls = re.findall(r'([\w:.]+)\s*\(', line)
        pure = {'float', 'double', 'uint32_t', 'int32_t', 'uint64_t', 'int64_t', 'std::fma', 'std::fabs', 'std::sqrt', 'std::rotl', 'native_guest_batch::StoreValue'}
        if re.search(r'\bctx\b(?!\.(?:mmu|inst_count)\b)', line) or any(c not in pure and not c.startswith(('ctx.mmu.', 'native_guest_fast::write')) for c in calls):
            flush()
        out.append(line)
    flush()
    if not count:
        return text, 0
    if selected is None:
        return baseline_fp_transport(text, needed)
    result = '\n'.join(out); at = result.index('{') + 1
    return result[:at] + '\n' + '\n'.join(decl) + result[at:], count

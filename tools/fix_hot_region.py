"""NATIVE_HOT_REGION_V1 -- reversible cross-function register promotion skeleton.

Flatten a closed, acyclic set of *direct* guest calls into one generated
function and re-run ``promote_regs`` over the result, so guest registers stay in
host registers across the former call/return boundaries.  The original function
text is kept twice: once as a ``noinline`` baseline called whenever the fast
entry does not apply, and once verbatim in the ``NWII_NO_HOT_REGION`` branch of
the marker block.  This module is a *stage*, not builder integration: nothing
installs it, enables an entry, or selects a hot root yet.

Manifest (default ``reference/guest-regions/hot-regions.json``)::

    {
      "schema_version": 1,
      "dol_sha256": "<sha256 of orig/main.dol>",   # optional but verified
      "regions": [
        {
          "name": "short stable label",
          "enabled": false,                # defaults to false; opt in explicitly
          "root": "802DD268",              # 8 hex digits, entry of the region
          "owners": ["802DD268", "803CCCB4"],  # every flattened function
          "edges": {"802DD268": ["803CCCB4"]}, # caller -> flattened direct callees
          "max_depth": 1,                  # must equal the longest path from root
          "source_hashes": {               # required for enabled entries
            "802DD268": "<sha256 of the flattened region body>",
            "803CCCB4": "<sha256 of the flattened region body>"
          },
          "instruction_hashes": {          # required for enabled entries
            "802DD268": "<sha256 of the // 0xPC: WORD sequence in order>",
            "803CCCB4": "<sha256 of the // 0xPC: WORD sequence in order>"
          }
        }
      ]
    }

``source_hashes`` / ``instruction_hashes`` are optional in the schema but an
enabled entry without a complete map is rejected: an unpinned region is not
qualified.  They cover the *primary* body when one exists (the exact text the
stage flattens, see below); otherwise the ``func_X`` body.
``fix_hot_region.source_hash`` and ``instruction_hash`` produce the values from
the text the stage would extract, so a manifest is generated from the same tree
it is applied to.

Owner resolver
--------------

A later entry-specialisation stage can emit two definitions for one owner::

    static __attribute__((noinline,optimize("O2"))) void native_guest_primary_X(NWII_CTX_PARAM ctx) {...}
    void func_X(NWII_CTX_PARAM ctx) { ...; native_guest_primary_X(ctx); return; ... }
    static ... void native_guest_linked_X(NWII_CTX_PARAM ctx) {...}   # optional

The primary carries the ``loc_X`` labels and the canonical body shape, while the
``func_X`` wrapper is the public entry and the exact fallback.  For a selected
owner the stage flattens the primary when present and rewrites the ``func_X``
wrapper; the primary (and so the object code the wrapper falls back to) is left
untouched.  A definition with a ``native_guest_linked_X`` helper is rejected:
linked regions perform their own cross-function commit and are not a sound
flattening owner.

Invariants enforced here (all fail closed):

* the edge graph is closed over ``owners`` (every endpoint is an owner), every
  owner is reachable from ``root``, the graph is acyclic, and its computed
  longest path equals ``max_depth`` (capped at ``MAX_DEPTH``; owners capped at
  ``MAX_OWNERS``);
* root and every owner are defined in exactly one generated file, and the
  stage refuses to inline a body across translation units;
* every owner has exactly one entry label ``loc_XXXXXXXX: ;`` and the root's
  wrapper has exactly one ``ctx.pc != 0xXXXXXXXX`` / ``ctx.pc==0xXXXXXXXX``
  entry selector;
* every declared edge has at least one top-level ``ctx.pc = 0xT; func_T(ctx);``
  site whose following return-PC test names a label that is also an entry case
  of the caller (a primary body has no local entry switch, so only the label is
  required there); a block-nested edge site is rejected;
* source and instruction digests match, ``canonical_body`` accepts every body,
  and ``promote_regs`` must succeed (no un-promoted flattened fallback);
* the flattened unit contains no residual call to a declared edge target and is
  at most ``MAX_UNIT_BYTES`` bytes;
* every ``// 0xPC: WORD`` in every extracted body matches ``words_from_dol`` and
  no owner or instruction PC is in ``reference/guest-regions/effect-exclusions.json``.

Stage order (recommended): after ``fix_hot_split.py`` (the hot/split text and
its promotion exist), after every stage that rewrites promoted bodies, and
after ``fix_v13_runtime.py`` (``NWII_GUEST_ABI``/``NWII_CTX_PARAM`` and the
final dispatch forms exist).  At that point the text is stable and the hashes
in the manifest can be regenerated.  ``--restore`` must run before any earlier
stage restores, in the same position as the other reversible stages.

Production default: the stage is off.  It applies only with
``NWII_BUILD_HOT_REGION=1`` or ``--enable``; ``NWII_BUILD_HOT_REGION=0`` or an
unset variable with an enabled manifest is a strict no-op.  The generated
wrapper keeps the runtime gate in ``runtime/native_hot_region.h``: every
diagnostic/trace mode and a live call trace fall back to the exact baseline.
The wrapper refuses re-entry (``active != 0``) and increments/decrements
``active`` by hand; an IOS longjmp can leave ``active == 1`` and every later
call then falls back until ``native_hot_region::Reset()`` runs.  That is safe
(the baseline is always correct), only slower, so no recovery hook is installed
in the generated dispatch loop yet.
A malformed manifest is a hard error; an enabled region whose anchors/digests/
DOL/holds drifts is skipped with one recorded line unless ``--strict`` is set.
Both ``apply`` and ``apply_all`` restore every block in the unit before writing,
so a staged unit is a fixed point of a restage and a stale region cannot survive
a manifest change.  ``apply`` is deterministic; each marker header records the
``generated_sha256`` of the region text.

Still required before builder integration: (1) a candidate analysis that picks
the hot root and its *direct-call* owners, computes ``max_depth`` and the two
digest maps, and writes an enabled manifest entry; (2) ``build_native.py``
restore/apply wiring, a copy of ``native_hot_region.h`` into the runtime include
directory and a ``CMake`` gate so ``NWII_BUILD_HOT_REGION=0`` restores and
``NWII_NO_HOT_REGION`` compiles the baseline; (3) an A/B qualification on the
real corpus (the offline ``guest_execution_region`` differential harness);
(4) a decision on indirect calls -- this skeleton flattens direct calls only and
leaves every indirect site as an ordinary region barrier.  The flatten loop is a
deliberately reduced copy of ``guest_execution_region.flatten`` (direct edges, no
experimental poll/dispatch protocol); ``canonical_body`` is imported, not
copied.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import argparse
import base64
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guest_execution_region as E
import promote_regs as P
from guest_region_sources import words_from_dol

PORT = Path(__file__).resolve().parents[1]
MARK = 'NATIVE_HOT_REGION_V1'
BLOCK = re.compile(r'/\* ' + MARK + r' ([A-Za-z0-9+/=]+)(?: generated_sha256=[0-9a-f]{64})? \*/\n'
                   r'.*?/\* END_' + MARK + r' \*/', re.S)
TREES = ('crash-hot', 'crash-split')
ATTRIBUTE = r'(?:__attribute__\(\([^\n]*\)\)(?: |\n))*'
FUNC = re.compile(r'^(?:static )?(?:NWII_GUEST_ABI )?' + ATTRIBUTE
                  + r'void (func_([0-9A-F]{8}))\((?:NWII_CTX_PARAM|CPUContext&) ctx\) \{$', re.M)
PRIMARY = re.compile(r'^(?:static )?(?:NWII_GUEST_ABI )?' + ATTRIBUTE
                     + r'void native_guest_primary_([0-9A-F]{8})\((?:NWII_CTX_PARAM|CPUContext&) ctx\) \{$', re.M)
LINKED = re.compile(r'^(?:static )?(?:NWII_GUEST_ABI )?' + ATTRIBUTE
                    + r'void native_guest_linked_([0-9A-F]{8})\((?:NWII_CTX_PARAM|CPUContext&) ctx\) \{$', re.M)
DIRECT = re.compile(r'^ctx\.pc = 0x([0-9A-F]{8}); func_\1\(ctx\);$')
CONTINUE = re.compile(r'^if \(ctx\.pc != 0x([0-9A-F]{8})\) return;$')
CASE = re.compile(r'^\s*case 0x([0-9A-F]{8}): goto loc_[0-9A-F]{8};$', re.M)
ENTRY_SELECTOR = r'ctx\.pc\s*[!=]=\s*0x{}[uU]?\b'
INSTRUCTION = re.compile(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})')
COMMENT_BLOCK = re.compile(r'/\*.*?\*/')
HEX8 = re.compile(r'^[0-9A-F]{8}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
MAX_DEPTH = 8
MAX_OWNERS = 12
MAX_UNIT_BYTES = 1 << 20
UNSET = object()
EXCLUSIONS = PORT / 'reference/guest-regions/effect-exclusions.json'


class Skip(ValueError):
    """A fail-closed, non-fatal refusal to stage one enabled region."""

    def __init__(self, reason, message=None):
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class RegionSpec:
    name: str
    root: str
    owners: tuple
    edges: dict
    max_depth: int
    source_hashes: dict
    instruction_hashes: dict
    enabled: bool = False


@dataclass(frozen=True)
class Definition:
    """Every generated definition of one owner address (any may be absent)."""
    func: object = None
    primary: object = None
    linked: object = None


@dataclass(frozen=True)
class Manifest:
    regions: tuple
    dol_sha256: object = None


@dataclass(frozen=True)
class Qualified:
    """Resolved owner text for one unit: region body, fallback wrapper, shape."""
    bodies: dict
    wrappers: dict
    primary: frozenset


def restore(text):
    """Remove every marker block, restoring the exact prior text (no staging)."""
    text = BLOCK.sub(lambda m: base64.b64decode(m[1]).decode('utf-8', 'surrogateescape'), text)
    if MARK in text:
        raise ValueError('Partial hot-region transform')
    return text


def _wrap(old, new, digest):
    payload = base64.b64encode(old.encode('utf-8', 'surrogateescape')).decode()
    return f'/* {MARK} {payload} generated_sha256={digest} */\n{new}\n/* END_{MARK} */'


def _code(line):
    """Statement text of a line, ignoring // comments and leading block markers."""
    return P.code_of(COMMENT_BLOCK.sub('', line)).strip()


def source_hash(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogateescape')).hexdigest()


def instruction_hash(text):
    """Digest of the generator's ``// 0xPC: WORD`` comments, in program order."""
    return hashlib.sha256('\n'.join(f'{pc}:{word}' for pc, word in INSTRUCTION.findall(text)).encode()).hexdigest()


@lru_cache(maxsize=1)
def exclusions():
    """Held-out addresses that must not be admitted as an owner or instruction."""
    return frozenset(json.loads(EXCLUSIONS.read_text())['addresses'])


def _region_body(definition):
    """The text the stage flattens: the primary body when present, else func_X."""
    return definition.primary if definition.primary is not None else definition.func


def digests(text, owners):
    """Digest maps for a manifest entry built from the tree it will apply to.

    ``source_hash``/``instruction_hash`` cover the primary body when one exists
    (``native_guest_primary_X``, the exact text the stage flattens); otherwise the
    ``func_X`` body.
    """
    definitions = _functions(text)
    bodies = {owner: _region_body(definitions[owner]) for owner in owners}
    return ({owner: source_hash(body) for owner, body in bodies.items()},
            {owner: instruction_hash(body) for owner, body in bodies.items()})


def _hex8(value, what):
    if not isinstance(value, str) or not HEX8.fullmatch(value.upper()):
        raise ValueError(f'Hot-region {what} is not 8 hex digits: {value!r}')
    return value.upper()


def _digest_map(entry, key, owners, enabled):
    value = entry.get(key)
    if value is None:
        if enabled:
            raise ValueError(f'Hot-region entry {entry.get("name")!r} has no {key}')
        return None
    if not isinstance(value, dict):
        raise ValueError(f'Hot-region {key} is not an object')
    result = {}
    for owner in owners:
        digest = value.get(owner) or value.get(owner.lower())
        if not isinstance(digest, str) or not SHA256.fullmatch(digest.lower()):
            raise ValueError(f'Hot-region {key} missing/invalid for func_{owner}')
        result[owner] = digest.lower()
    return result


def _graph_depth(root, owners, edges):
    """Acyclic longest path from root, with an explicit closed-graph check."""
    owner_set = set(owners)
    state, depths = {}, {}

    def visit(owner):
        if state.get(owner) == 'done':
            return depths[owner]
        if state.get(owner) == 'active':
            raise ValueError('Hot-region owner graph is recursive at func_' + owner)
        state[owner] = 'active'
        best = 0
        for target in edges.get(owner, ()):
            if target not in owner_set:
                raise ValueError('Hot-region edge leaves owners: func_' + target)
            if target == owner:
                raise ValueError('Hot-region self edge at func_' + owner)
            best = max(best, 1 + visit(target))
        state[owner] = 'done'
        depths[owner] = best
        return best

    depth = visit(root)
    reached = set()
    todo = [root]
    while todo:
        owner = todo.pop()
        if owner in reached:
            continue
        reached.add(owner)
        todo.extend(edges.get(owner, ()))
    if reached != owner_set:
        raise ValueError('Hot-region owner graph is not closed: unreachable ' +
                         ','.join(sorted(owner_set - reached)))
    return depth


def region_spec(entry):
    """Validate one manifest entry and return an immutable RegionSpec."""
    if not isinstance(entry, dict):
        raise ValueError('Hot-region entry is not an object')
    name = entry.get('name')
    if not isinstance(name, str) or not name:
        raise ValueError('Hot-region entry has no name')
    enabled = bool(entry.get('enabled', False))
    root = _hex8(entry.get('root'), 'root')
    raw_owners = entry.get('owners')
    if not isinstance(raw_owners, list) or not raw_owners:
        raise ValueError(f'Hot-region entry {name!r} has no owners')
    owners = tuple(_hex8(owner, 'owner') for owner in raw_owners)
    if len(owners) > MAX_OWNERS:
        raise ValueError(f'Hot-region entry {name!r} has {len(owners)} owners, over {MAX_OWNERS}')
    if len(set(owners)) != len(owners):
        raise ValueError(f'Hot-region entry {name!r} repeats an owner')
    if root not in owners:
        raise ValueError(f'Hot-region entry {name!r} does not list its root')
    if owners[0] != root:
        raise ValueError(f'Hot-region entry {name!r} must list the root first')
    raw_edges = entry.get('edges', {})
    if not isinstance(raw_edges, dict):
        raise ValueError(f'Hot-region entry {name!r} edges is not an object')
    edges = {owner: () for owner in owners}
    for caller, targets in raw_edges.items():
        caller = _hex8(caller, 'edge source')
        if caller not in owners:
            raise ValueError(f'Hot-region entry {name!r} edge from non-owner func_{caller}')
        if not isinstance(targets, list):
            raise ValueError(f'Hot-region entry {name!r} edge list is not an array')
        edges[caller] = tuple(_hex8(target, 'edge target') for target in targets)
    depth = _graph_depth(root, owners, edges)
    max_depth = entry.get('max_depth')
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 0:
        raise ValueError(f'Hot-region entry {name!r} has no valid max_depth')
    if max_depth != depth:
        raise ValueError(f'Hot-region entry {name!r} max_depth {max_depth} != computed {depth}')
    if depth > MAX_DEPTH:
        raise ValueError(f'Hot-region entry {name!r} depth {depth} exceeds {MAX_DEPTH}')
    return RegionSpec(name=name, root=root, owners=owners, edges=edges, max_depth=depth,
                      source_hashes=_digest_map(entry, 'source_hashes', owners, enabled),
                      instruction_hashes=_digest_map(entry, 'instruction_hashes', owners, enabled),
                      enabled=enabled)


def load_manifest(path):
    """Return a validated Manifest, or None when the manifest is absent."""
    path = Path(path)
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported hot-region manifest schema_version')
    entries = manifest.get('regions')
    if not isinstance(entries, list):
        raise ValueError('Hot-region manifest has no regions array')
    dol_sha256 = manifest.get('dol_sha256')
    if dol_sha256 is not None and (not isinstance(dol_sha256, str) or not SHA256.fullmatch(dol_sha256.lower())):
        raise ValueError('Hot-region manifest dol_sha256 is not a sha256')
    specs = [region_spec(entry) for entry in entries]
    roots = [spec.root for spec in specs]
    if len(set(roots)) != len(roots):
        raise ValueError('Hot-region manifest repeats a root')
    return Manifest(regions=tuple(specs), dol_sha256=(dol_sha256.lower() if dol_sha256 else None))


def _scan(text):
    """Text without the ``#else`` fallback copies of staged regions."""
    def strip(match):
        body = match.group(0)
        start = body.find('\n#else\n')
        end = body.find('\n#endif', start)
        return body[:start] + body[end + len('\n#endif'):] if start >= 0 and end >= 0 else body
    return BLOCK.sub(strip, text)


def _definitions(scan, pattern):
    result = {}
    for match in pattern.finditer(scan):
        pc = match.groups()[-1]
        end = scan.find('\n}', match.end())
        if end < 0:
            raise ValueError('Unterminated generated function for ' + pc)
        if pc in result:
            raise ValueError('Duplicate generated definition for ' + pc)
        result[pc] = scan[match.start():end + 2]
    return result


def _functions(text):
    """{owner: Definition} capturing func_X, native_guest_primary_X, linked_X."""
    scan = _scan(text)
    funcs = _definitions(scan, FUNC)
    primaries = _definitions(scan, PRIMARY)
    linked = _definitions(scan, LINKED)
    result = {}
    for pc in set(funcs) | set(primaries) | set(linked):
        result[pc] = Definition(func=funcs.get(pc), primary=primaries.get(pc), linked=linked.get(pc))
    return result


def _entry_cases(source):
    return set(CASE.findall(source))


def _forward_return(lines, start):
    """Return the PC of the first continuation test after a call statement."""
    for index in range(start, len(lines)):
        code = _code(lines[index])
        if not code:
            continue
        match = CONTINUE.match(code)
        if match:
            return match[1]
        if re.search(r'\breturn\b|func_[0-9A-F]{8}\(|ctx\.pc\s*=(?!=)', code):
            raise Skip('call-continuation', 'Unqualified direct call continuation')
    raise Skip('call-continuation', 'Missing direct call continuation')


def qualify(text, spec, dol=None, excluded=None):
    """Check every anchor/digest/DOL/exclusion; return the resolved owner text.

    ``dol`` is a path to the pristine DOL, or None (which fails closed: an
    enabled region is never staged without instruction verification).
    """
    definitions = _functions(text)
    excluded = exclusions() if excluded is None else frozenset(excluded)
    if set(spec.owners) & excluded:
        raise Skip('excluded-owner', 'Hot-region owner intersects held-out exclusions: ' +
                   ','.join(sorted(set(spec.owners) & excluded)))
    bodies, wrappers, primary = {}, {}, set()
    for owner in spec.owners:
        definition = definitions.get(owner)
        if definition is None or definition.func is None:
            raise Skip('missing-owner', f'Missing hot-region owner func_{owner} in this unit')
        if definition.linked is not None:
            raise Skip('linked-owner', f'func_{owner} has a native_guest_linked_{owner} helper')
        body = _region_body(definition)
        if definition.primary is not None:
            primary.add(owner)
        if body.count(f'loc_{owner}: ;') != 1:
            raise Skip('entry-label', f'func_{owner} does not have exactly one entry label')
        try:
            E.canonical_body(body)
        except ValueError as error:
            raise Skip('canonical', f'func_{owner}: {error}')
        if spec.source_hashes is not None and source_hash(body) != spec.source_hashes[owner]:
            raise Skip('source-digest', f'Hot-region source changed for func_{owner}')
        if spec.instruction_hashes is not None and instruction_hash(body) != spec.instruction_hashes[owner]:
            raise Skip('instruction-digest', f'Hot-region instruction sequence changed for func_{owner}')
        bodies[owner] = body
        wrappers[owner] = definition.func
    root = wrappers[spec.root]
    if len(re.findall(ENTRY_SELECTOR.format(spec.root), root)) != 1:
        raise Skip('entry-selector', f'func_{spec.root} lost its exact entry selector')
    if not spec.edges.get(spec.root):
        raise Skip('no-edge', 'Hot-region root has no declared edge')
    expected = {}
    for owner in spec.owners:
        for pc, word in INSTRUCTION.findall(bodies[owner]):
            if pc in excluded:
                raise Skip('excluded-instruction', f'Held-out instruction 0x{pc} in func_{owner}')
            value = int(pc, 16)
            if value in expected and expected[value] != int(word, 16):
                raise Skip('instruction-conflict', f'func_{owner} repeats 0x{pc} with another word')
            expected[value] = int(word, 16)
    if dol is None:
        raise Skip('dol-unavailable', 'No DOL available for instruction verification')
    if words_from_dol(dol, set(expected)) != expected:
        raise Skip('dol-words', 'Hot-region instruction/DOL mismatch')
    return Qualified(bodies=bodies, wrappers=wrappers, primary=frozenset(primary))


def _region_text(qualified, spec):
    """Flatten direct edges into one CFG and return an unpromoted function."""
    owners, edges, root = spec.owners, spec.edges, spec.root
    fresh_id = 0x5e000000
    resumes, entries, bodies = {}, {}, []

    def fresh():
        nonlocal fresh_id
        value = f'{fresh_id:08X}'
        fresh_id += 4
        return value

    for owner in owners:
        entries[owner] = fresh()
    leave = fresh()

    def label(value):
        return 'loc_' + value + ': ;'

    def push(target, ret, resume):
        resumes[resume] = ret
        return (f'    region_expected[region_depth]=0x{ret}; region_resume[region_depth++]=0x{resume};\n'
                f'    ctx.pc=0x{target};\n'
                f'    goto loc_{entries[target]};')

    for owner in owners:
        lines = E.canonical_body(qualified.bodies[owner]).splitlines()
        cases = _entry_cases(qualified.bodies[owner])
        labels = '\n'.join(lines)
        out = []
        used = set()
        depth = 0
        for index, line in enumerate(lines):
            code = _code(line)
            match = DIRECT.match(code)
            if match and match[1] in edges.get(owner, ()):
                if depth != 0:
                    raise Skip('nested-site', f'func_{owner} declared edge is block-nested')
                target = match[1]
                ret = _forward_return(lines, index + 1)
                if owner not in qualified.primary and ret not in cases:
                    raise Skip('return-case', f'func_{owner} return 0x{ret} is not an entry case')
                if f'loc_{ret}: ;' not in labels:
                    raise Skip('return-label', f'func_{owner} return 0x{ret} has no label')
                resume = fresh()
                used.add(target)
                out.extend((push(target, ret, resume), label(resume)))
                depth += code.count('{') - code.count('}')
                continue
            if code == 'ctx.pc = ctx.lr; return;':
                out.extend(('    ctx.pc = ctx.lr;', f'    goto loc_{leave};'))
                depth += code.count('{') - code.count('}')
                continue
            out.append(line)
            depth += code.count('{') - code.count('}')
        missing = set(edges.get(owner, ())) - used
        if missing:
            raise Skip('missing-site', f'func_{owner} has no declared call site for ' + ','.join(sorted(missing)))
        bodies.append((entries[owner], '\n'.join(out)))

    body = '\n'.join(label(entry) + '\n' + source for entry, source in bodies)
    returns = '\n'.join(f'        case 0x{resume}: goto loc_{resume};' for resume in resumes)
    depth = spec.max_depth + 1
    result = '\n'.join((
        f'void func_{root}(NWII_CTX_PARAM ctx) {{',
        '    unsigned region_depth=0;',
        f'    uint32_t region_expected[{depth}],region_resume[{depth}];',
        f'    goto loc_{entries[root]};', body, label(leave),
        '    if (region_depth==0) return;',
        '    --region_depth;',
        '    if (ctx.pc != region_expected[region_depth]) return;',
        '    switch (region_resume[region_depth]) {', returns,
        '        default: return;', '    }', '}'))
    # Promotion tracks dirty state per labelled unit: a conditional transfer
    # must terminate its unit before the following barrier, so split every
    # top-level goto with a synthetic label (same obligation as the prototype).
    split, nesting = [], 0
    for line in result.splitlines():
        split.append(line)
        code = P.code_of(line)
        nesting += code.count('{') - code.count('}')
        if nesting == 1 and re.search(r'goto loc_[0-9A-F]{8};', code):
            split.append(label(fresh()))
    return '\n'.join(split)


def _generate(qualified, spec):
    flat = _region_text(qualified, spec)
    if len(flat.encode('utf-8', 'surrogateescape')) > MAX_UNIT_BYTES:
        raise Skip('unit-bytes', f'Hot-region {spec.name!r} flattened unit exceeds {MAX_UNIT_BYTES} bytes')
    for owner, targets in spec.edges.items():
        for target in targets:
            if re.search(rf'ctx\.pc = 0x{target}; func_{target}\(ctx\);', flat):
                raise Skip('residual-call', f'Residual call to flattened owner func_{target} in func_{owner}')
    promoted, stats = P.promote(flat, True, True, None)
    if promoted is None:
        raise Skip('promotion', 'Hot-region promotion failed: ' + str(stats))
    head = f'void func_{spec.root}('
    if promoted.count(head) != 1:
        raise Skip('promotion-head', 'Hot-region promotion changed the function head')
    return promoted.replace(head, f'static __attribute__((noinline)) void NativeHotRegion_{spec.root}(', 1)


def _render(old, region, spec):
    baseline = f'NativeHotRegionBaseline_{spec.root}'
    head = FUNC.match(old)
    if head is None:
        raise Skip('baseline-head', 'Cannot rename the hot-region baseline func_' + spec.root)
    prefix = head.group(0)
    marker = f'func_{spec.root}('
    if prefix.count(marker) != 1:
        raise Skip('baseline-head', 'Hot-region baseline head is not exact')
    tail = prefix[prefix.index(marker) + len(marker) - 1:]
    abi = 'NWII_GUEST_ABI ' if 'NWII_GUEST_ABI' in prefix else ''
    attributes = ' '.join(re.findall(r'__attribute__\(\([^\n]*\)\)', prefix))
    attributes = attributes + ' ' if attributes else ''
    rewritten = f'static {abi}{attributes}__attribute__((noinline)) void {baseline}' + tail + old[len(prefix):]
    return '\n'.join((
        '#ifndef NWII_NO_HOT_REGION',
        '#include "runtime/native_hot_region.h"',
        region,
        rewritten,
        f'NWII_GUEST_ABI void func_{spec.root}(NWII_CTX_PARAM ctx) {{',
        f'    if (!native_hot_region::Enabled()) {{ {baseline}(ctx); return; }}',
        f'    if (ctx.pc != 0x{spec.root} || native_hot_region::active != 0) {{ ++native_hot_region::fallbacks; {baseline}(ctx); return; }}',
        '    ++native_hot_region::entered; ++native_hot_region::active;',
        f'    NativeHotRegion_{spec.root}(ctx);',
        '    --native_hot_region::active;',
        '}',
        '#else',
        old,
        '#endif'))


def _apply_restored(text, spec, dol=None, excluded=None):
    """Transform a restored text (no hot-region block for this or another region)."""
    if not spec.enabled:
        return text
    qualified = qualify(text, spec, dol, excluded)
    old = qualified.wrappers[spec.root]
    if text.count(old) != 1:
        raise Skip('ambiguous-root', 'Ambiguous hot-region root replacement')
    region = _generate(qualified, spec)
    return text.replace(old, _wrap(old, _render(old, region, spec), source_hash(region)), 1)


def apply(text, spec, dol=None, excluded=None):
    """Apply one region, idempotently, wrapped in a reversible marker block.

    Every existing hot-region block is restored first, so one call is a fixed
    point and ``apply(apply(x, a), b)`` stages only ``b``. Use ``apply_all`` to
    stage several regions into one unit. ``dol`` is the pristine DOL used for
    instruction verification and must be supplied for an enabled region.
    """
    return _apply_restored(restore(text), spec, dol, excluded)


def apply_all(text, specs, dol=None, excluded=None):
    """Restore, then stage every enabled region in ``specs`` (one order, one file)."""
    text = restore(text)
    for spec in specs:
        text = _apply_restored(text, spec, dol, excluded)
    return text


def _build_root(runtime):
    """Generated guest trees live in the build directory that owns them."""
    runtime = Path(runtime)
    for candidate in (runtime, *runtime.parents[:3]):
        if (candidate / TREES[0]).is_dir() or (candidate / TREES[1]).is_dir():
            return candidate
    return runtime


def _files(root):
    result = []
    for tree in TREES:
        result.extend(sorted((root / tree).glob('*.cpp')))
    return result


def resolve_dol(port, dol):
    """DOL path for verification; None when the default is absent."""
    if dol is UNSET:
        for name in ('NWII_INPUT_DOL', 'NWII_DOL'):
            value = os.environ.get(name)
            if value:
                path = Path(value)
                break
        else:
            path = Path(port).parent / 'orig/main.dol'
        return path if path.is_file() else None
    if dol is None:
        return None
    path = Path(dol)
    if not path.is_file():
        raise ValueError(f'Hot-region DOL not found: {path}')
    return path


def _report(skipped, strict):
    if not skipped:
        return
    if strict:
        name, reason, message = skipped[0]
        raise Skip(reason, message)
    for name, reason, message in skipped:
        print(f'Hot region: skipped {name}: {message}')


def stage(port, runtime, manifest, undo=False, dry_run=False, enable=False, strict=False, dol=UNSET):
    """Restore, or stage the manifest, over the generated hot/split sources."""
    root = _build_root(runtime)
    files = _files(root)
    if undo:
        changed = 0
        for path in files:
            raw = path.read_bytes()
            crlf = b'\r\n' in raw
            text = raw.decode('utf-8', 'surrogateescape').replace('\r\n', '\n')
            restored = restore(text)
            data = (restored.replace('\n', '\r\n') if crlf else restored).encode('utf-8', 'surrogateescape')
            if data != raw:
                if not dry_run:
                    path.write_bytes(data)
                changed += 1
        print(f'Hot region restored: {changed} file(s)' + (' (dry run).' if dry_run else '.'))
        return changed
    environment = os.environ.get('NWII_BUILD_HOT_REGION')
    if environment == '0':
        enable = False
    elif environment == '1':
        enable = True
    loaded = load_manifest(manifest)
    if loaded is None:
        print('Hot region: no manifest, no-op.')
        return 0
    active = [spec for spec in loaded.regions if spec.enabled]
    if not enable or not active:
        print('Hot region: disabled or empty, no-op.')
        return 0
    dol_path = resolve_dol(port, dol)
    if dol_path is None:
        _report([(spec.name, 'dol-unavailable',
                  'No DOL available for instruction verification') for spec in active], strict)
        return 0
    if loaded.dol_sha256 is not None:
        digest = hashlib.sha256(dol_path.read_bytes()).hexdigest()
        if digest != loaded.dol_sha256:
            _report([(spec.name, 'dol-hash', 'DOL sha256 does not match the manifest') for spec in active], strict)
            return 0
    excluded = exclusions()
    by_root = {}
    for path in files:
        text = path.read_bytes().decode('utf-8', 'surrogateescape').replace('\r\n', '\n')
        for owner in _functions(restore(text)):
            by_root.setdefault(owner, []).append(path)
    groups, skipped = {}, []
    for spec in active:
        paths = by_root.get(spec.root, [])
        if len(paths) != 1:
            skipped.append((spec.name, 'root-unit',
                            f'root func_{spec.root} is defined in {len(paths)} generated files'))
            continue
        if any(by_root.get(owner, []) != paths for owner in spec.owners):
            skipped.append((spec.name, 'owner-unit', f'owner of func_{spec.root} is not in the root unit'))
            continue
        groups.setdefault(paths[0], []).append(spec)
    plans = []
    staged_count = 0
    for path, specs_here in sorted(groups.items()):
        raw = path.read_bytes()
        crlf = b'\r\n' in raw
        text = raw.decode('utf-8', 'surrogateescape').replace('\r\n', '\n')
        staged = restore(text)
        for spec in specs_here:
            try:
                staged = _apply_restored(staged, spec, dol_path, excluded)
                staged_count += 1
            except Skip as error:
                skipped.append((spec.name, error.reason, str(error)))
        data = (staged.replace('\n', '\r\n') if crlf else staged).encode('utf-8', 'surrogateescape')
        plans.append((path, raw, data))
    if strict and skipped:
        raise Skip(skipped[0][1], skipped[0][2])
    changed = 0
    for path, raw, data in plans:
        if data != raw:
            if not dry_run:
                path.write_bytes(data)
            changed += 1
    for name, reason, message in skipped:
        print(f'Hot region: skipped {name}: {message}')
    print(f'Hot region: {staged_count} region(s) in {len(groups)} file(s), '
          f'{changed} changed, {len(skipped)} skipped' + (' (dry run).' if dry_run else '.'))
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=Path, default=PORT)
    ap.add_argument('--runtime', type=Path,
                    help='build root containing crash-hot/crash-split (default: <port>/build)')
    ap.add_argument('--manifest', type=Path,
                    default=PORT / 'reference/guest-regions/hot-regions.json')
    ap.add_argument('--dol', type=Path, help='pristine DOL for instruction verification '
                                             '(default: NWII_INPUT_DOL/NWII_DOL, else <port>/../orig/main.dol)')
    ap.add_argument('--enable', action='store_true', help='stage even when NWII_BUILD_HOT_REGION is not 1')
    ap.add_argument('--strict', action='store_true', help='any skipped enabled region is an error')
    ap.add_argument('--restore', action='store_true', help='remove every hot-region block')
    ap.add_argument('--dry-run', action='store_true', help='report without writing')
    args = ap.parse_args()
    port = args.port.resolve()
    runtime = args.runtime if args.runtime else port / 'build'
    manifest = args.manifest if args.manifest.is_absolute() else port / args.manifest
    dol = args.dol if args.dol else UNSET
    stage(port, runtime, manifest, undo=args.restore, dry_run=args.dry_run,
          enable=args.enable, strict=args.strict, dol=dol)


if __name__ == '__main__':
    main()

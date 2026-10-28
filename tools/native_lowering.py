"""Shared machinery for source-backed native lowering of translated guest functions.

A candidate is a guest function whose C/C++ source was recovered in this
repository (src/c, src/external, src/radical or src/master_match) and whose
translated form is executed by the native port. The lowering replaces the
translated body with a host implementation written from that source.

This module owns the parts every stage needs:

  * the candidate manifest (data/native-lowering-candidates.json),
  * locating a guest function inside the generated chunk,
  * renaming the translated body and inserting the native entry,
  * rewriting the direct call sites so the caller's values stay in host
    registers instead of round-tripping through the CPUContext,
  * the entry conditions that keep every unsupported path on the translated
    body (interior entry, traced call, disabled lowering, candidate guards),
  * the source/DOL identity hashes that pin the qualification.

The translated body is never modified: it is renamed to `native_original_<addr>`
and stays reachable for every entry the native path does not cover.

Entry conditions
----------------

Every native entry - the definition wrapper and every rewritten call site -
carries the same conditions:

  * the ordinary entry: `ctx.pc` is the function's address (the definition
    wrapper only; a call site sets it itself),
  * `!nwii::runtime::g_trace_calls`: a traced call must reach the translated
    body so the call trace stays identical,
  * `!nwii::runtime::native_lower::disabled()`: the kill switch
    (NWII_NO_NATIVE_LOWERING) forces every lowering onto the translated path,
  * the candidate's own guards, for example `ctx.gqr[0] == 0` for the guest
    functions that use the paired-single unit.

A rewritten call site that does not satisfy the conditions calls the
translated function, which then takes the same decision again.
"""
from pathlib import Path
import hashlib
import json
import os
import re

PORT = Path(__file__).resolve().parents[1]
GENERATED = Path(os.environ.get('NWII_GENERATED') or (PORT / 'build/crash-generated'))
MANIFEST = PORT / 'data/native-lowering-candidates.json'

# The generated definition of a guest function.
# The pristine generated chunk spells the definition with the host ABI attribute;
# the later staging stages strip it (`void func_...`), so both spellings are accepted.
DEFINITION = re.compile(
    r'(?:NWII_GUEST_ABI )?void func_([0-9A-Fa-f]{8})\(NWII_CTX_PARAM ctx\) \{')
# A direct call to a guest function.
DIRECT_CALL = re.compile(r'ctx\.pc = 0x([0-9A-Fa-f]{8});\s*func_\1\(ctx\);')

BEGIN = '// BEGIN_NATIVE_LOWERING_'
END = '// END_NATIVE_LOWERING_'

# The conditions every native entry shares. The tracing test keeps a traced
# call on the translated body (the call trace must stay identical); the kill
# switch forces every lowering onto the translated path.
SHARED_CONDITIONS = ('!nwii::runtime::g_trace_calls',
                      '!nwii::runtime::native_lower::disabled()')


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogateescape')).hexdigest()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(path=MANIFEST):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def candidates(manifest=None, adopted_only=True):
    manifest = manifest or load_manifest()
    out = []
    for entry in manifest.get('candidates', []):
        if adopted_only and not entry.get('implementation'):
            continue
        out.append(entry)
    return out


def function_span(text, address):
    """Return (start, end) of the whole `void func_<address>(...) { ... }` definition."""
    m = re.search(r'(?:NWII_GUEST_ABI )?void func_' + address + r'\(NWII_CTX_PARAM ctx\) \{', text)
    if not m:
        return None
    i = text.index('{', m.start())
    depth = 0
    for j in range(i, len(text)):
        if text[j] == '{':
            depth += 1
        elif text[j] == '}':
            depth -= 1
            if depth == 0:
                return m.start(), j + 1
    return None


def call_sites(text, address):
    """The direct call sites of `address` in `text`."""
    return [m for m in DIRECT_CALL.finditer(text) if m[1].upper() == address.upper()]


def entry_conditions(candidate, include_pc=True):
    """The conditions a native entry must satisfy, in the order they are tested."""
    conditions = []
    if include_pc:
        conditions.append('ctx.pc == 0x' + candidate['address'].upper())
    conditions.extend(SHARED_CONDITIONS)
    conditions.extend(candidate.get('guards', []))
    return conditions


def native_call(candidate):
    """The C++ statement that runs the native lowering."""
    impl = candidate['implementation']
    args = ', '.join(candidate.get('arguments', []))
    call = 'native_lower::%s(%s);' % (impl, args)
    result = candidate.get('result')
    if result:
        call = '%s = %s;' % (result, call[:-1])
    return call


def entry_code(candidate):
    """The C++ body of the native entry, as inserted into the generated chunk.

    The entry keeps the translated function reachable: an interior entry, a
    traced call, a disabled lowering or a candidate whose guard does not hold
    falls back to `native_original_<addr>`.
    """
    address = candidate['address'].upper()
    conditions = ' && '.join(entry_conditions(candidate))
    call = native_call(candidate)
    # The translated function returns through `blr`, which the generated code
    # models as `ctx.pc = ctx.lr`. The native entry has to do the same: the
    # caller checks ctx.pc against its own return address.
    publish = ' '.join(candidate.get('publish', []))
    if publish:
        publish += ' '
    return (
        'NWII_GUEST_ABI void func_%s(NWII_CTX_PARAM ctx) {\n'
        '  if (%s) { %s %sctx.pc = ctx.lr; return; }\n'
        '  native_original_%s(ctx);\n'
        '}\n' % (address, conditions, call, publish, address))


def stage_block(candidate, original_body):
    """The staged block: the renamed original plus the native entry."""
    address = candidate['address'].upper()
    return (BEGIN + address + '\n' + original_body.rstrip() + '\n\n' +
            entry_code(candidate).rstrip() + '\n' + END + address)


def stage_chunk(text, candidate):
    """Apply one candidate to a generated chunk.

    Returns (new_text, stats). The translated body is renamed, not edited.
    A candidate whose native entry is only used at selected call sites
    (`native_entry: call_sites_only`) keeps the translated definition.
    """
    address = candidate['address'].upper()
    begin_marker = BEGIN + address
    end_marker = END + address
    if begin_marker in text:
        a = text.index(begin_marker)
        z = text.index(end_marker, a) + len(end_marker)
        while z < len(text) and text[z] == '\n':
            z += 1
        previous = text[a:z]
        renamed = previous[previous.index('static void native_original_' + address):]
        renamed = renamed[:renamed.index('\n\n' + 'NWII_GUEST_ABI void func_' + address)]
        block = stage_block(candidate, renamed)
        return text[:a] + block + text[z:], {'address': address, 'restaged': True}
    if candidate.get('native_entry') == 'call_sites_only':
        return text, {'address': address, 'definition': 'translated'}
    span = function_span(text, address)
    if not span:
        raise SystemExit('func_%s not found in the generated chunk' % address)
    start, end = span
    original = text[start:end]
    renamed = original.replace('void func_' + address, 'static void native_original_' + address, 1)
    block = stage_block(candidate, renamed)
    return text[:start] + block + text[end:], {'address': address, 'definition': 'native'}


def rewrite_direct_calls(text, candidate):
    """Rewrite the direct call sites of `address` to the native entry.

    The rewritten site carries the same conditions as the definition entry, so
    a traced call, a disabled lowering or a candidate whose guard does not hold
    keeps the translated function. A caller listed in `exclude_callers` keeps
    the translated call as well, because it observes a guest register the
    native path does not reproduce.
    """
    address = candidate['address'].upper()
    args = candidate.get('call_arguments')
    if not args:
        return text, 0
    excluded = {c.upper() for c in candidate.get('exclude_callers', [])}
    pattern = re.compile(r'ctx\.pc = 0x' + address + r';\s*func_' + address + r'\(ctx\);')
    count = 0
    skipped = []

    def rewrite(text):
        nonlocal count
        out = []
        position = 0
        for match in pattern.finditer(text):
            caller = enclosing_caller(text, match.start())
            if caller in excluded:
                skipped.append(caller)
                continue
            out.append(text[position:match.start()])
            out.append(call_site_code(candidate))
            position = match.end()
            count += 1
        out.append(text[position:])
        return ''.join(out)

    return rewrite(text), count


def call_site_code(candidate):
    """The rewritten direct call site.

    The conditions are the shared ones plus the candidate's own guards; the
    `ctx.pc` test is not repeated because the site sets `ctx.pc` itself.
    """
    address = candidate['address'].upper()
    conditions = ' && '.join(entry_conditions(candidate, include_pc=False))
    call = native_call(candidate)
    publish = ' '.join(candidate.get('publish', []))
    if publish:
        publish += ' '
    return ('ctx.pc = 0x%s; if (%s) { %s %sctx.pc = ctx.lr; } else func_%s(ctx);'
            % (address, conditions, call, publish, address))


CALLER_START = re.compile(r'(?m)^NWII_GUEST_ABI void func_([0-9A-Fa-f]{8})\(NWII_CTX_PARAM ctx\) \{')


def enclosing_caller(text, offset):
    """The address of the guest function whose body contains `offset`."""
    address = None
    for m in CALLER_START.finditer(text):
        if m.start() > offset:
            break
        address = m[1]
    return address.upper() if address else ''


def restore_direct_calls(text, candidate):
    """Undo the direct call site rewrite of one candidate.

    A rewritten site reads `ctx.pc = 0x<addr>; if (<conditions>) { <native call>
    ...; ctx.pc = ctx.lr; } else func_<addr>(ctx);` - the conditions and the
    published registers sit between the call and the return, so the whole line
    is matched.
    """
    address = candidate['address'].upper()
    pattern = re.compile(r'ctx\.pc = 0x' + address + r';[^\n]*?\} else func_' + address + r'\(ctx\);')
    return pattern.sub('ctx.pc = 0x%s; func_%s(ctx);' % (address, address), text)


def includes_for(candidates_):
    """The overlay headers the staged chunks need."""
    headers = []
    for entry in candidates_:
        header = entry.get('header')
        if header and header not in headers:
            headers.append(header)
    return headers


# ---------------------------------------------------------------------------
# Identity pinning
# ---------------------------------------------------------------------------
REPO = PORT.parent
# The decompilation tree exists only in a developer checkout. A launcher build ships the qualification data instead, pinned
# by builder-revision.json, so there is no local source to compare against.
SRC = REPO / 'src'


def check_identity(manifest, adopted):
    """Verify the pinned DOL and source hashes of the adopted candidates.

    The lowering is written from a recovered source; if that source or the
    game's main.dol changes, the lowering has to be requalified.
    """
    problems = []
    dol = manifest.get('dol_sha256')
    if dol:
        dol_file = REPO / 'orig/main.dol'
        recorded = (PORT / 'data/main.dol.sha256')
        if dol_file.is_file():
            actual = sha256_file(dol_file)
            if actual.lower() != dol.lower():
                problems.append('main.dol hash changed: expected %s, found %s' % (dol, actual))
        elif recorded.is_file():
            expected = recorded.read_text().split()[0]
            if expected.lower() != dol.lower():
                problems.append('manifest DOL hash %s does not match the recorded %s' % (dol, expected))
    if not SRC.is_dir():
        # A user build has no decompilation sources. The DOL is still pinned, and every qualification file is hashed into
        # builder-revision.json, so the shipped lowering cannot drift without the launcher refusing the pack.
        print('IDENTITY: decompilation sources absent (%s); source hashes not checked' % SRC)
        return problems
    for entry in adopted:
        source = entry.get('source')
        expected = entry.get('source_sha256')
        if not source or not expected:
            continue
        path = REPO / source
        if not path.is_file():
            problems.append('source missing: %s' % source)
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append('source %s changed: expected %s, found %s' % (source, expected, actual))
    return problems

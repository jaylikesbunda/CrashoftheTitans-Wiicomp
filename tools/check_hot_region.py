"""Offline checks for the reversible NATIVE_HOT_REGION_V1 stage skeleton.

No game build and no write outside a temporary directory.  The synthetic
fixtures are minimal generated guest functions in the exact shapes the stage
qualifies: the plain promoted ``func_X`` body, the entry-specialised
``native_guest_primary_X`` + ``func_X`` wrapper pair, and a block-nested edge
site.  A synthetic DOL (7 text sections, big-endian words) supplies the
instruction verification, and the runtime gate header is compiled and executed
when a host C++ compiler is available.  The real generated trees are only read.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import fix_hot_region as F

PORT = Path(__file__).resolve().parents[1]
ROOT = '80001000'
CALLEE = '80001100'
OWNERS = (ROOT, CALLEE)
EDGES = {ROOT: (CALLEE,)}
BLOCKED = ('NWII_NO_HOT_REGION', 'NWII_NO_HOT_REGIONS', 'NWII_SAMPLE', 'NWII_PEEK',
           'NWII_STARTUP_PROFILE', 'NWII_LOOPTRACE', 'NWII_RESCHED', 'NWII_WATCH',
           'NWII_WPAD_SCRIPT', 'NWII_WPAD_LIVE', 'NWII_LOAD_STATE')

FIXTURE = '''#include "runtime/native_guest_abi.h"

NWII_GUEST_ABI void func_80001000(NWII_CTX_PARAM ctx) {
    NWII_TRACE_CALL(0x80001000, ctx);
    if (ctx.pc != 0x80001000) {
        switch (ctx.pc) {
            case 0x80001010: goto loc_80001010;
            case 0x0: return; // idle: no runnable thread
            default: nwii::runtime::interpret_step(ctx); return;
        }
    }
    ++ctx.inst_count;
loc_80001000: ;
    // 0x80001000: 9421FFF0
    ctx.gpr[1] = ctx.gpr[1] + -16; // stwu r1, -16(r1)
    ++ctx.inst_count;
loc_80001004: ;
    // 0x80001004: 7C0802A6
    ctx.gpr[0] = ctx.lr; // mflr r0
    ++ctx.inst_count;
loc_80001008: ;
    // 0x80001008: 48000000
    ctx.lr = 0x80001010; // save return address
    ctx.pc = 0x80001100; func_80001100(ctx);
    if (ctx.pc != 0x80001010) return;
    ++ctx.inst_count;
loc_80001010: ;
    // 0x80001010: 38600007
    ctx.gpr[3] = 7; // li r3, 7
    ++ctx.inst_count;
loc_80001014: ;
    // 0x80001014: 4E800020
    ctx.pc = ctx.lr; return;
}

NWII_GUEST_ABI void func_80001100(NWII_CTX_PARAM ctx) {
    if (ctx.pc != 0x80001100) return;
    ++ctx.inst_count;
loc_80001100: ;
    // 0x80001100: 38800009
    ctx.gpr[4] = 9; // li r4, 9
    ++ctx.inst_count;
loc_80001104: ;
    // 0x80001104: 4E800020
    ctx.pc = ctx.lr; return;
}
'''

# Entry-specialised shape: the primary owns the labels, the func_X wrapper is the
# public entry (and the exact fallback), and there is no local entry switch.
PRIMARY = '''#include "runtime/native_guest_abi.h"

static __attribute__((noinline,optimize("O2"))) void native_guest_primary_80001000(NWII_CTX_PARAM ctx) {
    uint32_t g3;
    uint64_t ic;
    ++ic;
loc_80001000: ;
    // 0x80001000: 9421FFF0
    ctx.gpr[1] = ctx.gpr[1] + -16; // stwu r1, -16(r1)
    ++ic;
loc_80001004: ;
    // 0x80001004: 7C0802A6
    ctx.gpr[0] = ctx.lr; // mflr r0
    ++ic;
loc_80001008: ;
    // 0x80001008: 48000000
    ctx.lr = 0x80001010; // save return address
    ctx.pc = 0x80001100; func_80001100(ctx);
    if (ctx.pc != 0x80001010) return;
    ++ic;
loc_80001010: ;
    // 0x80001010: 38600007
    ctx.gpr[3] = 7; // li r3, 7
    ++ic;
loc_80001014: ;
    // 0x80001014: 4E800020
    ctx.pc = ctx.lr; return;
}

static __attribute__((noinline,optimize("O2"))) void native_guest_primary_80001100(NWII_CTX_PARAM ctx) {
    uint32_t g4;
    uint64_t ic;
    ++ic;
loc_80001100: ;
    // 0x80001100: 38800009
    ctx.gpr[4] = 9; // li r4, 9
    ++ic;
loc_80001104: ;
    // 0x80001104: 4E800020
    ctx.pc = ctx.lr; return;
}

__attribute__((optimize("O2")))
void func_80001000(NWII_CTX_PARAM ctx) {
    if(!native_guest_entry::Enabled()){native_guest_entry::Record(false);native_guest_resume_80001000(ctx);return;}
    if(ctx.pc==0x80001000){native_guest_entry::Record(true);native_guest_primary_80001000(ctx);return;}
    if(native_guest_entry::ContinuationsEnabled()){switch(ctx.pc){
    case 0x80001010:
        native_guest_entry::RecordContinuation();native_guest_continuation_80001000(ctx);return;
    default: break;
    }}
    native_guest_entry::Record(false);native_guest_resume_80001000(ctx);
}

__attribute__((optimize("O2")))
void func_80001100(NWII_CTX_PARAM ctx) {
    if(!native_guest_entry::Enabled()){native_guest_entry::Record(false);native_guest_resume_80001100(ctx);return;}
    if(ctx.pc==0x80001100){native_guest_entry::Record(true);native_guest_primary_80001100(ctx);return;}
    native_guest_entry::Record(false);native_guest_resume_80001100(ctx);
}
'''

passed = 0


def check(condition, message):
    global passed
    if not condition:
        raise AssertionError(message)
    passed += 1


def expect(exception, message, fragment, action):
    global passed
    try:
        action()
    except exception as error:
        check(fragment in str(error), f'{message}: unexpected message {error!r}')
        return
    raise AssertionError(message + ': no exception')


def entry(text, owners=OWNERS, edges=EDGES, max_depth=1, enabled=True,
          source_hashes='text', instruction_hashes='text', root=ROOT):
    value = {'name': 'fixture', 'enabled': enabled, 'root': root,
             'owners': list(owners), 'max_depth': max_depth}
    value['edges'] = {caller: list(targets) for caller, targets in edges.items()}
    hashes, instructions = F.digests(text, list(owners))
    if source_hashes == 'text':
        value['source_hashes'] = hashes
    elif source_hashes is not None:
        value['source_hashes'] = source_hashes
    if instruction_hashes == 'text':
        value['instruction_hashes'] = instructions
    elif instruction_hashes is not None:
        value['instruction_hashes'] = instruction_hashes
    return F.region_spec(value)


def renamed(text, pairs):
    for old, new in pairs:
        text = text.replace(old, new)
    return text


SECOND = renamed(FIXTURE, [('80001104', '80031004'), ('80001100', '80031000'),
                           ('80001014', '80021014'), ('80001010', '80021010'),
                           ('80001008', '80021008'), ('80001004', '80021004'),
                           ('80001000', '80021000')])

NESTED = FIXTURE.replace(
    '    ctx.lr = 0x80001010; // save return address\n'
    '    ctx.pc = 0x80001100; func_80001100(ctx);\n',
    '    if (ctx.gpr[5]) {\n'
    '    ctx.lr = 0x80001010; // save return address\n'
    '    ctx.pc = 0x80001100; func_80001100(ctx);\n'
    '    }\n')

EXCLUDED = renamed(FIXTURE, [('80001104', '800079FC'), ('80001100', '800079F8')])
EXCLUDED_OWNERS = (ROOT, '800079F8')
EXCLUDED_EDGES = {ROOT: ('800079F8',)}


def dol_words(text):
    return {int(pc, 16): int(word, 16) for pc, word in F.INSTRUCTION.findall(text)}


def fake_dol(path, words):
    """A minimal valid DOL whose single text section maps [0x80000000, +0x40000)."""
    base, size, offset = 0x80000000, 0x40000, 0x100
    data = bytearray(offset + size)
    struct.pack_into('>I', data, 0, offset)
    struct.pack_into('>I', data, 0x48, base)
    struct.pack_into('>I', data, 0x90, size)
    for pc, word in words.items():
        struct.pack_into('>I', data, offset + pc - base, word)
    Path(path).write_bytes(bytes(data))
    return Path(path)


def make_manifest(path, regions, dol_sha256=None):
    value = {'schema_version': 1, 'regions': regions}
    if dol_sha256 is not None:
        value['dol_sha256'] = dol_sha256
    Path(path).write_text(json.dumps(value), encoding='utf-8')
    return Path(path)


def gate_fixture(work):
    """Compile and run the runtime gate so the disabled/re-entry paths are real."""
    compiler = shutil.which('g++')
    if not compiler:
        print('Hot-region check: g++ not found, runtime-gate compile skipped.')
        return
    source = work / 'gate.cpp'
    source.write_text('''#include <cstdio>
namespace nwii::runtime { bool g_trace_calls = false; }
#include "native_hot_region.h"
int main() {
    std::printf("A%d ", native_hot_region::Enabled() ? 1 : 0);
    nwii::runtime::g_trace_calls = true;
    std::printf("B%d ", native_hot_region::Enabled() ? 1 : 0);
    nwii::runtime::g_trace_calls = false;
    native_hot_region::active = 1;
    std::printf("C%d ", native_hot_region::Enterable() ? 1 : 0);
    native_hot_region::Reset();
    std::printf("D%d\\n", native_hot_region::Enterable() ? 1 : 0);
    return 0;
}
''', encoding='utf-8')
    exe = work / 'gate.exe'
    result = subprocess.run([compiler, '-std=c++20', '-O0', '-I', str(PORT / 'runtime-overlays'),
                             str(source), '-o', str(exe)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError('runtime gate did not compile: ' + result.stderr[-2000:])

    def run(environment):
        return subprocess.run([str(exe)], capture_output=True, text=True, env=environment).stdout.strip()

    clean = dict(os.environ)
    for name in BLOCKED:
        clean.pop(name, None)
    check(run(clean) == 'A1 B0 C0 D1', 'gate: enabled/trace/re-entry/reset behaviour')
    for name in BLOCKED:
        environment = dict(clean)
        environment[name] = '1'
        check(run(environment).startswith('A0 '), f'gate: {name} must disable the region')


def main():
    # 1. Manifest: schema-valid, placeholder disabled, and a strict no-op.
    manifest_path = PORT / 'reference/guest-regions/hot-regions.json'
    manifest = F.load_manifest(manifest_path)
    check(manifest is not None and len(manifest.regions) == 1, 'placeholder manifest must load one entry')
    check(manifest.regions[0].enabled is False, 'placeholder entry must stay disabled')
    check(manifest.dol_sha256 is None, 'placeholder manifest has no dol_sha256')
    check(F.stage(PORT, PORT / 'build', manifest_path, dry_run=True) == 0,
          'disabled manifest must be a stage no-op')

    work = Path(tempfile.mkdtemp(prefix='hot-region-'))
    dol = fake_dol(work / 'main.dol', {**dol_words(FIXTURE), **dol_words(SECOND)})
    bad_dol = fake_dol(work / 'bad.dol', {**dol_words(FIXTURE), 0x80001100: 0})

    # 2. Unstaged text is untouched by restore, and a DOL is mandatory.
    check(F.restore(FIXTURE) == FIXTURE, 'restore must be a no-op on unstaged text')
    spec = entry(FIXTURE)
    expect(F.Skip, 'missing DOL', 'DOL', lambda: F.apply(FIXTURE, spec))

    # 3. Apply the stage.
    staged = F.apply(FIXTURE, spec, dol=dol)
    check(staged != FIXTURE, 'stage must change the fixture')
    check(staged.count(F.MARK) == 2, 'one marker header and one marker tail expected')
    check(F.apply(staged, spec, dol=dol) == staged, 'apply must be idempotent')
    check(F.restore(staged) == FIXTURE, 'restore must restore exact prior bytes')
    check(re.search(r'generated_sha256=[0-9a-f]{64}', staged) is not None,
          'marker header must record the generated_sha256')
    check(F.apply(FIXTURE, spec, dol=dol) == staged, 'apply must be deterministic')

    # 4. Fallback: NWII_NO_HOT_REGION branch is the exact original function, and
    #    the wrapper uses the runtime gate plus a hand-managed re-entry guard.
    old_root = F._functions(FIXTURE)[ROOT].func
    start = staged.index('#else\n') + len('#else\n')
    end = staged.index('\n#endif', start)
    check(staged[start:end] == old_root, 'NWII_NO_HOT_REGION branch must be the exact original')
    check(f'void NativeHotRegionBaseline_{ROOT}(NWII_CTX_PARAM ctx) {{' in staged,
          'baseline copy must keep the exact signature')
    check('#include "runtime/native_hot_region.h"' in staged,
          'the generated block must include the runtime gate header')
    wrapper = staged[staged.index(f'NWII_GUEST_ABI void func_{ROOT}('):]
    check(f'ctx.pc != 0x{ROOT}' in wrapper, 'wrapper must gate on the exact entry PC')
    check('!native_hot_region::Enabled()' in wrapper, 'wrapper must gate on the runtime Enable()')
    check('native_hot_region::active != 0' in wrapper, 'wrapper must refuse re-entry by hand')
    check('++native_hot_region::active;' in wrapper and '--native_hot_region::active;' in wrapper,
          'wrapper must increment/decrement active by hand')
    check('++native_hot_region::entered;' in wrapper and '++native_hot_region::fallbacks;' in wrapper,
          'wrapper must count entered/fallbacks')
    check('struct' not in wrapper and '~' not in wrapper, 'wrapper must not use RAII')

    # 5. Cross-function promotion: the edge is inlined and g3/g4/ic stay local.
    start = staged.index(f'static __attribute__((noinline)) void NativeHotRegion_{ROOT}(')
    end = staged.index(f'void NativeHotRegionBaseline_{ROOT}(', start)
    impl = staged[start:staged.rindex('\n', start, end)]
    check(f'func_{CALLEE}(ctx);' not in impl, 'declared direct edge must be flattened')
    check(impl.count('g3 = ctx.gpr[3]') == 1, 'g3 must be loaded once at region entry')
    check(impl.count('ctx.gpr[3] = g3') >= 1, 'g3 must be written back at a region exit')
    check(impl.index('g3 = 7;') > impl.index('g3 = ctx.gpr[3]'),
          'post-call g3 must use the promoted local')
    check(impl.index('g3 = 7;') < impl.index('ctx.gpr[3] = g3'),
          'post-call g3 must stay local until an exit')
    check('g4 = 9;' in impl, 'callee body must be present in the flattened unit')

    # 6. Entry-specialised primary body is preferred; the func_X wrapper is the fallback.
    primary_spec = entry(PRIMARY)
    primary_staged = F.apply(PRIMARY, primary_spec, dol=dol)
    check(primary_staged != PRIMARY, 'primary stage must change the fixture')
    wrapper_start = primary_staged.index('#else\n') + len('#else\n')
    wrapper_end = primary_staged.index('\n#endif', wrapper_start)
    fallback = F._functions(PRIMARY)[ROOT].func
    check('loc_80001000: ;' not in fallback, 'the func_X wrapper has no entry label')
    check(primary_staged[wrapper_start:wrapper_end] == fallback,
          'the exact func_X wrapper must remain the NWII_NO_HOT_REGION fallback')
    check('__attribute__((optimize("O2"))) __attribute__((noinline)) void NativeHotRegionBaseline_' + ROOT in primary_staged,
          'the baseline copy must keep the wrapper optimisation attribute')
    check('native_guest_primary_80001000' in primary_staged,
          'the primary body must survive outside the region')
    check(f'ctx.pc != 0x{ROOT}' in primary_staged[primary_staged.index(f'NWII_GUEST_ABI void func_{ROOT}('):],
          'the generated wrapper selects the exact entry PC')
    start = primary_staged.index(f'static __attribute__((noinline)) void NativeHotRegion_{ROOT}(')
    end = primary_staged.index(f'void NativeHotRegionBaseline_{ROOT}(', start)
    primary_impl = primary_staged[start:primary_staged.rindex('\n', start, end)]
    check(f'func_{CALLEE}(ctx);' not in primary_impl, 'primary edge must be flattened')
    check('g4 = 9;' in primary_impl, 'primary callee body must be flattened')
    check(F.restore(primary_staged) == PRIMARY, 'primary restore must be exact')
    check(F.digests(PRIMARY, OWNERS)[0][ROOT] == F.source_hash(F._functions(PRIMARY)[ROOT].primary),
          'source digest must cover the primary body')

    # 7. Disabled spec leaves text alone and removes an already staged block.
    disabled = entry(FIXTURE, enabled=False, source_hashes=None, instruction_hashes=None)
    check(F.apply(FIXTURE, disabled) == FIXTURE, 'disabled spec must be a no-op')
    check(F.apply(staged, disabled) == FIXTURE, 'disabled spec must unstage')

    # 8. Multiple regions in one file: restore+restage is per-file and exact.
    combined = FIXTURE + SECOND[SECOND.index('NWII_GUEST_ABI void func_80021000'):]
    first = entry(combined)
    second = dict(root='80021000', owners=('80021000', '80031000'), edges={'80021000': ('80031000',)})
    two = F.apply_all(combined, [first, entry(combined, **second)], dol=dol)
    check(two.count(F.MARK) == 4, 'two regions need two marker blocks')
    check(F.restore(two) == combined, 'restoring two regions must be exact')
    second_only = F.apply_all(two, [first], dol=dol)
    check(second_only.count(F.MARK) == 2, 'restaging one region must drop the other')
    check('NativeHotRegion_80021000' not in second_only, 'dropped region must leave no code')
    check(F.restore(second_only) == combined, 'the kept region must restore exactly')
    check(F.apply_all(second_only, [first], dol=dol) == second_only, 'restaging must be a fixed point')

    # 9. Fail closed on changed anchors/hashes/DOL and on nested or held-out sites.
    expect(ValueError, 'source digest', 'source changed',
           lambda: F.apply(FIXTURE.replace('ctx.gpr[4] = 9;', 'ctx.gpr[4] = 8;'), spec, dol=dol))
    mutated_comment = FIXTURE.replace('38800009', '3880000A')
    expect(ValueError, 'instruction digest', 'instruction sequence',
           lambda: F.apply(mutated_comment, entry(mutated_comment,
                                                  instruction_hashes=F.digests(FIXTURE, OWNERS)[1]), dol=dol))
    mutated_label = FIXTURE.replace('loc_80001100: ;', 'loc_80001110: ;')
    expect(ValueError, 'entry label', 'entry label',
           lambda: F.apply(mutated_label, entry(mutated_label), dol=dol))
    missing_case = FIXTURE.replace('            case 0x80001010: goto loc_80001010;\n', '')
    expect(ValueError, 'return entry case', 'not an entry case',
           lambda: F.apply(missing_case, entry(missing_case), dol=dol))
    missing_owner = FIXTURE[FIXTURE.index('NWII_GUEST_ABI void func_80001100'):]
    expect(ValueError, 'missing owner', 'Missing hot-region owner',
           lambda: F.apply(missing_owner, spec, dol=dol))
    expect(ValueError, 'DOL word mismatch', 'DOL mismatch',
           lambda: F.apply(FIXTURE, spec, dol=bad_dol))
    expect(ValueError, 'held-out owner', 'held-out',
           lambda: F.apply(EXCLUDED, entry(EXCLUDED, owners=EXCLUDED_OWNERS, edges=EXCLUDED_EDGES), dol=dol))
    expect(ValueError, 'nested edge site', 'block-nested',
           lambda: F.apply(NESTED, entry(NESTED), dol=dol))
    linked_text = PRIMARY + '''
static void native_guest_linked_80001000(NWII_CTX_PARAM ctx) {
    ctx.pc = ctx.lr;
}
'''
    expect(ValueError, 'linked owner', 'linked',
           lambda: F.apply(linked_text, entry(linked_text), dol=dol))
    expect(ValueError, 'recursive graph', 'recursive',
           lambda: F.region_spec({'name': 'cycle', 'root': ROOT, 'owners': list(OWNERS),
                                  'edges': {ROOT: [CALLEE], CALLEE: [ROOT]}, 'max_depth': 1,
                                  'enabled': False}))
    expect(ValueError, 'unreachable owner', 'not closed',
           lambda: F.region_spec({'name': 'open', 'root': ROOT,
                                  'owners': [ROOT, CALLEE, '80001200'],
                                  'edges': {ROOT: [CALLEE]}, 'max_depth': 1, 'enabled': False}))
    expect(ValueError, 'max depth', 'max_depth',
           lambda: F.region_spec({'name': 'depth', 'root': ROOT, 'owners': list(OWNERS),
                                  'edges': {ROOT: [CALLEE]}, 'max_depth': 0, 'enabled': False}))
    expect(ValueError, 'owner cap', 'owners, over',
           lambda: F.region_spec({'name': 'wide', 'root': ROOT,
                                  'owners': [ROOT] + ['%08X' % (0x80002000 + 4 * i) for i in range(12)],
                                  'edges': {}, 'max_depth': 0, 'enabled': False}))
    expect(ValueError, 'duplicate root', 'repeats a root', _duplicate_manifest)
    expect(ValueError, 'malformed dol_sha256', 'dol_sha256', _bad_dol_manifest)
    expect(ValueError, 'default enabled is false', 'no source_hashes',
           lambda: F.region_spec({'name': 'on', 'enabled': True, 'root': ROOT,
                                  'owners': list(OWNERS), 'edges': {ROOT: [CALLEE]}, 'max_depth': 1}))
    check(F.region_spec({'name': 'default-off', 'root': ROOT, 'owners': list(OWNERS),
                         'edges': {ROOT: [CALLEE]}, 'max_depth': 1}).enabled is False,
          'a manifest entry without enabled must default to disabled')
    expect(ValueError, 'depth cap', 'exceeds 8', _deep_region)
    saved_budget = F.MAX_UNIT_BYTES
    F.MAX_UNIT_BYTES = 64
    try:
        expect(ValueError, 'unit byte budget', 'exceeds',
               lambda: F.apply(FIXTURE, spec, dol=dol))
    finally:
        F.MAX_UNIT_BYTES = saved_budget

    # 10. Real stage path on a throw-away build tree, including default-off,
    #     soft-skip and strict modes.
    tree = work / 'crash-hot'
    tree.mkdir(exist_ok=True)
    source = tree / 'output_0.cpp'
    source.write_text(FIXTURE, encoding='utf-8')
    original = source.read_bytes()
    manifest = make_manifest(work / 'manifest.json', [{
        'name': 'fixture', 'enabled': True, 'root': ROOT, 'owners': list(OWNERS),
        'edges': {ROOT: [CALLEE]}, 'max_depth': 1,
        'source_hashes': F.digests(FIXTURE, OWNERS)[0],
        'instruction_hashes': F.digests(FIXTURE, OWNERS)[1]}])
    check(F.stage(work, work, manifest, dry_run=True, dol=dol) == 0,
          'unset NWII_BUILD_HOT_REGION must be a strict no-op')
    check(source.read_bytes() == original, 'the default-off no-op must not write')
    os.environ['NWII_BUILD_HOT_REGION'] = '0'
    try:
        check(F.stage(work, work, manifest, enable=True, dol=dol) == 0,
              'NWII_BUILD_HOT_REGION=0 must override --enable')
    finally:
        os.environ.pop('NWII_BUILD_HOT_REGION', None)
    check(source.read_bytes() == original, 'NWII_BUILD_HOT_REGION=0 must not write')
    saved_input = os.environ.pop('NWII_INPUT_DOL', None)
    saved_dolenv = os.environ.pop('NWII_DOL', None)
    try:
        expected = PORT.parent / 'orig/main.dol'
        check(F.resolve_dol(PORT, F.UNSET) == (expected if expected.is_file() else None),
              'the default DOL path must be <port>/../orig/main.dol')
        os.environ['NWII_DOL'] = str(dol)
        check(F.resolve_dol(PORT, F.UNSET) == dol, 'NWII_DOL must select the default DOL')
        os.environ.pop('NWII_DOL', None)
        os.environ['NWII_INPUT_DOL'] = str(dol)
        check(F.resolve_dol(PORT, F.UNSET) == dol, 'NWII_INPUT_DOL must select the default DOL')
    finally:
        for key, value in (('NWII_INPUT_DOL', saved_input), ('NWII_DOL', saved_dolenv)):
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
    schema2 = make_manifest(work / 'schema2.json', [])
    schema2.write_text('{"schema_version": 2, "regions": []}', encoding='utf-8')
    expect(ValueError, 'malformed manifest is hard', 'schema_version',
           lambda: F.stage(work, work, schema2, enable=True, dol=dol))
    check(F.stage(work, work, manifest, dry_run=True, enable=True, strict=True, dol=dol) == 1,
          'dry run must report one changed file')
    check(source.read_bytes() == original, 'dry run must not write')
    saved = os.environ.pop('NWII_BUILD_HOT_REGION', None)
    os.environ['NWII_BUILD_HOT_REGION'] = '1'
    try:
        check(F.stage(work, work, manifest, strict=True, dol=dol) == 1, 'stage must write one file')
    finally:
        os.environ.pop('NWII_BUILD_HOT_REGION', None)
        if saved is not None:
            os.environ['NWII_BUILD_HOT_REGION'] = saved
    check(source.read_bytes() != original, 'staged file must differ')
    check(F.stage(work, work, manifest, enable=True, strict=True, dol=dol) == 0,
          'restaging must be a fixed point')
    check(F.stage(work, work, manifest, undo=True) == 1, 'restore must write one file')
    check(source.read_bytes() == original, 'restore must restore exact bytes')
    # drift: the pinned digest no longer matches, so the baseline is untouched.
    source.write_text(FIXTURE.replace('ctx.gpr[4] = 9;', 'ctx.gpr[4] = 8;'), encoding='utf-8')
    drifted = source.read_bytes()
    check(F.stage(work, work, manifest, enable=True, dol=dol) == 0, 'drifted region must soft-skip')
    check(source.read_bytes() == drifted, 'a soft skip must leave the baseline untouched')
    expect(ValueError, 'strict drift', 'source changed',
           lambda: F.stage(work, work, manifest, enable=True, strict=True, dol=dol))
    expect(ValueError, 'strict missing DOL', 'DOL',
           lambda: F.stage(work, work, manifest, enable=True, strict=True, dol=None))
    source.write_text(FIXTURE, encoding='utf-8')
    check(F.stage(work, work, manifest, enable=True, dol=dol) == 1, 'a verified DOL must stage')
    check(F.stage(work, work, manifest, undo=True) == 1, 'restore after the DOL test')
    hash_manifest = make_manifest(work / 'hashed.json',
                                  json.loads(manifest.read_text())['regions'],
                                  dol_sha256='0' * 64)
    check(F.stage(work, work, hash_manifest, enable=True, dol=dol) == 0,
          'a wrong dol_sha256 must soft-skip')
    check(source.read_bytes() == original, 'a dol-hash skip must not write')
    verified = make_manifest(work / 'verified.json',
                             json.loads(manifest.read_text())['regions'],
                             dol_sha256=hashlib.sha256(dol.read_bytes()).hexdigest())
    check(F.stage(work, work, verified, enable=True, strict=True, dol=dol) == 1,
          'a matching dol_sha256 must stage')
    check(F.stage(work, work, verified, undo=True) == 1, 'restore the verified-dol stage')
    empty = make_manifest(work / 'empty.json', [])
    check(F.stage(work, work, empty, enable=True, dol=dol) == 0, 'empty manifest must be a no-op')
    check(F.stage(work, work, work / 'absent.json', enable=True, dol=dol) == 0,
          'absent manifest must be a no-op')

    # 11. Runtime gate header: compiled and executed when a compiler exists.
    gate_fixture(work)

    # 12. Real generated text is only read; restore must round-trip cleanly.
    for tree_name in F.TREES:
        for path in sorted((PORT / 'build' / tree_name).glob('*.cpp'))[:2]:
            raw = path.read_bytes()
            text = raw.decode('utf-8', 'surrogateescape').replace('\r\n', '\n')
            restored = F.restore(text)
            if F.MARK not in text:
                check(restored == text, f'{path.name} must be unchanged by restore')
    print(f'Hot-region check: PASS, {passed} assertions.')


def _deep_region():
    owners = ['%08X' % (0x80010000 + 4 * i) for i in range(10)]
    edges = {owners[i]: [owners[i + 1]] for i in range(9)}
    return F.region_spec({'name': 'deep', 'root': owners[0], 'owners': owners,
                          'edges': edges, 'max_depth': 9, 'enabled': False})


def _duplicate_manifest():
    work = Path(tempfile.mkdtemp(prefix='hot-region-'))
    path = work / 'manifest.json'
    value = {'schema_version': 1, 'regions': [
        {'name': 'a', 'root': ROOT, 'owners': [ROOT], 'max_depth': 0, 'enabled': False},
        {'name': 'b', 'root': ROOT, 'owners': [ROOT], 'max_depth': 0, 'enabled': False}]}
    path.write_text(json.dumps(value), encoding='utf-8')
    return F.load_manifest(path)


def _bad_dol_manifest():
    work = Path(tempfile.mkdtemp(prefix='hot-region-'))
    path = work / 'manifest.json'
    path.write_text(json.dumps({'schema_version': 1, 'dol_sha256': 'nope', 'regions': []}),
                    encoding='utf-8')
    return F.load_manifest(path)


if __name__ == '__main__':
    main()

"""NATIVE_LLVM_IR_V1: hybrid LLVM-IR backend stage.

Emits a *slice* of guest functions as LLVM IR (native-port/compiler/emit_llvm_ir.py),
compiles them with clang / llvm-mingw to COFF objects whose symbols are the exact
linker names of the generated functions (functions.h declares every
func_XXXXXXXX with C linkage), and makes CMake link those objects *instead of*
the corresponding generated C++ definitions.  The C++ backend stays the oracle:
with the switch off nothing here changes the tree.

How to add or remove a function (the only knob):
  1. edit native-port/reference/backend-reports/llvm-ir-functions.json
  2. add/remove the 8-hex entry in "functions".  Requirements (checked here):
     the function must be liftable by emit_llvm_ir.py, must live in a normal
     build/crash-generated/output_*.cpp chunk (not a hot unit, not main_output),
     and must not appear in reference/guest-regions/effect-exclusions.json.
     Prove equivalence with the generated C++ first (native-port/tools/check_llvm_hybrid.py).
  3. delete the entry to move the function back to C++; this stage then drops its
     stripped copy and restores the chunk path.  To force everything back to C++
     run `python tools/fix_llvm_ir_backend.py --restore` or build with
     NWII_BUILD_IR_BACKEND=cpp (the default).

Layout (all under native-port/build/, gitignored, regenerated deterministically):
  crash-ir/ir_<ADDR>.src.cpp  extracted C++ (the oracle text, for inspection)
  crash-ir/ir_<ADDR>.ll       emitted LLVM IR
  crash-ir/ir_<ADDR>.o        clang object that provides the func_<ADDR> symbol
  crash-ir-split/output_N.cpp chunk copy with the IR-owned functions cut out
  reports/llvm-ir-slice.json  stage report (status, functions, objects, hashes)

The CMake edit is additive and reversible, following the fix_hot_split.py /
fix_chunk_size_flags.py marker convention: one `# NATIVE_LLVM_IR_V1` block is
appended, and the stripped chunk paths are rewritten to crash-ir-split/ so the
existing per-chunk compile flags follow.  `--restore` (or any disabled run)
removes the block and rewrites every crash-ir-split/ path back to its chunk.

Env:
  NWII_BUILD_IR_BACKEND=cpp|hybrid|llvm   cpp (default) disables; hybrid/llvm enable
                                          the listed slice (full-corpus LLVM is future work)
  NWII_CLANG=<clang.exe|bin dir>          clang to use; default llvm-mingw 20260908 below
  NWII_CXX=<g++.exe|bin dir>              C++ compiler for the CPUContext offset probe
  NWII_IR_OPT=O2|Os|O1                    IR optimisation level (default O2)
"""
from pathlib import Path
import hashlib, json, os, re, shutil, subprocess, sys

PORT = Path(__file__).resolve().parents[1]
GEN = PORT / 'build/crash-generated'
SPLIT = PORT / 'build/crash-split'
HOT = PORT / 'build/crash-hot'
IR = PORT / 'build/crash-ir'
IR_SPLIT = PORT / 'build/crash-ir-split'
CM = PORT / 'build/nwii-full-source/CMakeLists.txt'
EMIT = PORT / 'compiler/emit_llvm_ir.py'
SHIM = PORT / 'runtime-overlays/llvm_ir_support.cpp'
SETS = PORT / 'reference/backend-reports/llvm-ir-functions.json'
EXCLUSIONS = PORT / 'reference/guest-regions/effect-exclusions.json'
REPORT = PORT / 'reports/llvm-ir-slice.json'
INC = PORT / 'build/nwii-full-source/nWiiRuntime/include'
MARK = '# NATIVE_LLVM_IR_V1'
NL = '\n'

# ----- toolchain discovery ------------------------------------------------
# Documented default: the llvm-mingw 20260908 ucrt x86_64 spike unpacked under the
# OpenCode scratch dir.  NWII_CLANG wins when set (file or bin dir).
DEFAULT_LLVM_BIN = (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) /
                    'llvm-mingw/bin')
DEFAULT_WINLIBS_BIN = Path(
    str(Path.home() / 'AppData/Local/Microsoft/WinGet/Packages') +
    r'\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin')


def _exe(raw, name):
    if not raw:
        return None
    p = Path(raw)
    if p.is_dir():
        p = p / name
    return p if p.is_file() else None


def find_clang():
    raw = os.environ.get('NWII_CLANG', '')
    if raw.strip().lower() in ('0', 'none', 'off'):
        return None                       # explicit "no clang": exercise the clean skip path
    for env, name in (('NWII_CLANG', 'clang.exe'), ('LLVM_MINGW_BIN', 'clang.exe')):
        p = _exe(os.environ.get(env), name)
        if p:
            return p
    p = DEFAULT_LLVM_BIN / 'clang.exe'
    if p.is_file():
        return p
    which = shutil.which('clang') or shutil.which('clang.exe')
    return Path(which) if which else None


def find_cxx():
    for env in ('NWII_CXX', 'WINLIBS_BIN'):
        p = _exe(os.environ.get(env), 'g++.exe')
        if p:
            return p
    p = DEFAULT_WINLIBS_BIN / 'g++.exe'
    if p.is_file():
        return p
    which = shutil.which('g++') or shutil.which('g++.exe')
    return Path(which) if which else None


# ----- deterministic file writes ------------------------------------------
def write_if_changed(path, text, errors='surrogateescape'):
    """Idempotent text write in the pipeline's newline convention (read_text
    normalises newlines, write_text emits os.linesep on Windows, matching
    fix_hot_split.py / fix_chunk_size_flags.py)."""
    if path.exists() and path.read_text(errors=errors) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, errors=errors)
    return True


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ----- deterministic object output ---------------------------------------
def normalize_coff_timestamp(path):
    """Zero the COFF TimeDateStamp so repeated builds are byte-identical.

    clang stamps every COFF object with the wall clock (IMAGE_FILE_HEADER
    TimeDateStamp, bytes 4..7); the linker may propagate it into the PE.  The
    field is informational only, so pinning it to 0 makes the IR object - and
    therefore the linked executable - reproducible.  No-op when already zero,
    so Ninja does not see a spurious rebuild."""
    data = bytearray(path.read_bytes())
    if len(data) >= 8 and data[0:2] == b'\x64\x86' and data[4:8] != b'\x00\x00\x00\x00':
        data[4:8] = b'\x00\x00\x00\x00'
        path.write_bytes(bytes(data))


def run(cmd, cwd=None):
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True)
    if p.returncode:
        sys.stderr.write('FAILED: %s\n%s\n' % (' '.join(map(str, cmd)), (p.stdout + p.stderr)[-4000:]))
        raise SystemExit(p.returncode)
    return p.stdout


# ----- CMake marker handling ----------------------------------------------
def clean_block(text):
    """Drop our appended block (marker .. next '# NATIVE_' marker or EOF)."""
    if MARK in text:
        a = text.index(MARK)
        b = text.find(NL + '# NATIVE_', a + len(MARK))
        text = text[:a].rstrip(NL) + NL + (text[b + 1:] if b >= 0 else '')
    return text


def canonical_chunk(name):
    return 'crash-split' if (SPLIT / name).exists() else 'crash-generated'


def restore_paths(text):
    """Rewrite every crash-ir-split path back to its chunk (split if hot-split)."""
    def repl(m):
        return '/build/%s/%s"' % (canonical_chunk(m.group(1)), m.group(1))
    return re.sub(r'/build/crash-ir-split/([^"]+)"', repl, text)


def point_at_ir(text, name):
    """Point a chunk's add_executable / flag-list entry at the stripped copy."""
    for kind in ('crash-split', 'crash-generated'):
        text = text.replace('/build/%s/%s"' % (kind, name), '/build/crash-ir-split/%s"' % name)
    return text


# ----- function set -------------------------------------------------------
def load_functions():
    data = json.loads(SETS.read_text())
    addrs = []
    for a in data['functions']:
        a = str(a).upper()
        if a.startswith('0X'):
            a = a[2:]
        addrs.append(a.rjust(8, '0'))
    if len(set(addrs)) != len(addrs):
        raise SystemExit('duplicate address in %s' % SETS)
    excluded = set(json.loads(EXCLUSIONS.read_text())['addresses'])
    clash = sorted(a for a in addrs if a in excluded)
    if clash:
        raise SystemExit('refusing effect-excluded address(es) in the IR set: ' + ', '.join(clash))
    return addrs


# ----- upstream chunk + strip --------------------------------------------
def upstream_chunk(name):
    """The file the current pipeline would compile for chunk `name`."""
    for base in (SPLIT, GEN):
        f = base / name
        if f.exists():
            return f
    return None


def find_definition(func):
    """Locate func_<ADDR>'s definition; returns (kind, path, text) or None."""
    for f in sorted(HOT.glob('hot_*.cpp')):
        if re.search(r'^void %s\(NWII_CTX_PARAM ctx\) \{' % func, f.read_text(errors='surrogateescape'), re.M):
            return 'hot', f, None
    for f in sorted(GEN.glob('output_*.cpp')):
        up = upstream_chunk(f.name)
        if up is None:
            continue
        text = up.read_text(errors='surrogateescape')
        if re.search(r'^void %s\(NWII_CTX_PARAM ctx\) \{' % func, text, re.M):
            return 'chunk', up, text
    return None


def function_span(text, func):
    m = re.search(r'^void ' + func + r'\(NWII_CTX_PARAM ctx\) \{$', text, re.M)
    if not m:
        return None
    end = text.find(NL + '}' + NL, m.start())
    if end < 0:
        return None
    return m.start(), end + len(NL + '}')


def strip_function(text, func):
    span = function_span(text, func)
    if span is None:
        raise SystemExit('%s definition not found in expected chunk text' % func)
    a, b = span
    return text[:a] + text[b:]


# ----- offset probe -------------------------------------------------------
OFFSETS_PROBE = r'''
#include "runtime/cpu_context.h"
#include <cstdio>
#include <cstddef>
using namespace nwii::runtime;
int main() {
  printf("{");
  printf("\"gpr\": %zu,", offsetof(CPUContext, gpr));
  printf("\"cr\": %zu,", offsetof(CPUContext, cr));
  printf("\"pc\": %zu,", offsetof(CPUContext, pc));
  printf("\"lr\": %zu,", offsetof(CPUContext, lr));
  printf("\"xer\": %zu,", offsetof(CPUContext, xer));
  printf("\"inst_count\": %zu,", offsetof(CPUContext, inst_count));
  printf("\"guest_bytes\": %zu,", offsetof(CPUContext, backup_stack));
  printf("\"mem1_base\": %zu,", offsetof(CPUContext, mmu) + offsetof(MMU, mem1) + offsetof(NativeWatchedRam, base_));
  printf("\"mem2_base\": %zu,", offsetof(CPUContext, mmu) + offsetof(MMU, mem2) + offsetof(NativeWatchedRam, base_));
  printf("\"ctx_size\": %zu}", sizeof(CPUContext));
  return 0;
}
'''


def context_offsets(cxx):
    """CPUContext byte offsets for the MinGW/libstdc++ runtime layout."""
    probe_src = IR / 'offsets_probe.cpp'
    probe_exe = IR / 'offsets_probe.exe'
    IR.mkdir(parents=True, exist_ok=True)
    write_if_changed(probe_src, OFFSETS_PROBE)
    run([cxx, '-std=c++20', '-Wno-invalid-offsetof', '-I', INC, probe_src, '-o', probe_exe])
    return json.loads(run([probe_exe]))


# ----- linker symbol ------------------------------------------------------
def ir_symbol(func):
    """The exact linker symbol of a generated guest function.

    build/crash-generated/functions.h declares every func_XXXXXXXX with C
    linkage (`extern "C" { ... }`) and the chunks include it before the
    definition, so the definition inherits C linkage: the symbol is the plain
    `func_8023B080`, not the C++ mangling.  The C ABI still passes the
    `CPUContext&` reference as a pointer in RCX, which is exactly what the
    emitted `define void @func_8023B080(ptr %ctx)` uses."""
    return func


# ----- main ---------------------------------------------------------------
def main():
    restore = '--restore' in sys.argv[1:]
    backend = (os.environ.get('NWII_BUILD_IR_BACKEND') or 'cpp').strip().lower()
    backend = {'0': 'cpp', '1': 'hybrid', 'off': 'cpp', 'on': 'hybrid'}.get(backend, backend)
    if backend not in ('cpp', 'llvm', 'hybrid'):
        raise SystemExit('NWII_BUILD_IR_BACKEND must be cpp|llvm|hybrid, got %r' % backend)
    enabled = (not restore) and backend in ('llvm', 'hybrid')

    text = CM.read_text()
    text = restore_paths(clean_block(text))

    def finish(status, note, funcs=(), objects=()):
        write_if_changed(CM, text)
        for d in (IR, IR_SPLIT):
            if not enabled:
                shutil.rmtree(d, ignore_errors=True)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(dict(
            status=status, backend=backend, note=note, functions=list(funcs),
            objects=[{'path': p.as_posix(), 'sha256': sha(p)} for p in objects]),
            indent=2) + NL)
        print('LLVM-IR slice: %s (%s)' % (status, note))

    if not enabled:
        finish('disabled' if not restore else 'restored',
               'C++ backend only')
        return

    clang = find_clang()
    cxx = find_cxx()
    missing = [n for n, t in (('clang', clang), ('g++ (offset probe)', cxx)) if t is None]
    if missing:
        finish('skipped', 'not found: ' + ', '.join(missing) +
               ' (set NWII_CLANG / NWII_CXX); C++ backend unchanged')
        return

    funcs = load_functions()
    # 1. locate every function before touching anything; a function that has
    #    moved into a hot unit is skipped (hot membership is dynamic) rather
    #    than breaking the build.
    plan = {}
    for addr in funcs:
        func = 'func_' + addr
        found = find_definition(func)
        if found is None:
            raise SystemExit('IR set lists %s but it is not a generated chunk function' % func)
        kind, path, _ = found
        if kind == 'hot':
            print('LLVM-IR slice: skipping %s (now in hot unit %s; C++ definition kept)'
                  % (func, path.name))
            continue
        plan[addr] = (func, path)
    funcs = [a for a in funcs if a in plan]
    if not funcs:
        finish('skipped', 'every listed function is now in a hot unit')
        return

    off = context_offsets(cxx)
    write_if_changed(IR / 'offsets.json', json.dumps(off, indent=2) + NL)
    IR.mkdir(parents=True, exist_ok=True)
    IR_SPLIT.mkdir(parents=True, exist_ok=True)
    opt = os.environ.get('NWII_IR_OPT', 'O2')
    if opt not in ('O2', 'Os', 'O1'):
        raise SystemExit('NWII_IR_OPT must be O2|Os|O1')
    irflags = ['-march=x86-64-v3', '-ffp-contract=off', '-fwrapv']
    if os.environ.get('NWII_HOT_FMA', '1') != '0':
        irflags.append('-mfma')

    # 2. strip the C++ definitions out of private chunk copies
    stripped = {}       # chunk name -> text
    for addr in funcs:
        func, path = plan[addr]
        name = path.name
        chunk = stripped.get(name, path.read_text(errors='surrogateescape'))
        stripped[name] = strip_function(chunk, func)
    for name, body in stripped.items():
        write_if_changed(IR_SPLIT / name, body)

    # 3. emit + compile the IR objects (deterministic: run from the IR dir, basename only)
    objects = []
    for addr in funcs:
        func, _ = plan[addr]
        src = IR / ('ir_%s.src.cpp' % addr)
        ll = IR / ('ir_%s.ll' % addr)
        obj = IR / ('ir_%s.o' % addr)
        found = find_definition(func)
        # the pre-strip definition text is the oracle the emitter lifts from
        original = found[2] if found[2] is not None else found[1].read_text(errors='surrogateescape')
        span = function_span(original, func)
        write_if_changed(src, original[span[0]:span[1]])
        out = run([sys.executable, EMIT, '--source', src.name, '--name', ir_symbol(func),
                   '--offsets', (IR / 'offsets.json').name, '-o', ll.name], cwd=IR)
        print(out.strip())
        run([clang, '-c', '-' + opt] + irflags + [ll.name, '-o', obj.name], cwd=IR)
        normalize_coff_timestamp(obj)
        objects.append(obj)

    # 4. point CMake at the stripped chunks and append the IR objects
    for name in sorted(stripped):
        text = point_at_ir(text, name)
    block = NL + MARK + NL
    block += '# Emit/compile this slice with clang; see tools/fix_llvm_ir_backend.py.' + NL
    block += 'target_include_directories(CrashNative PRIVATE "%s")' % GEN.as_posix() + NL
    block += 'target_sources(CrashNative PRIVATE' + NL
    for obj in objects:
        block += '    "%s"%s' % (obj.as_posix(), NL)
    block += '    "%s"%s' % (SHIM.as_posix(), NL)
    block += ')' + NL
    text = text.rstrip(NL) + NL + block
    finish('enabled', '%d function(s) via clang %s at -%s' % (len(funcs), clang.name, opt),
           funcs, objects)

    # 5. drop stale outputs from a previous, different set
    keep = {('ir_%s.%s' % (a, e)) for a in funcs for e in ('src.cpp', 'll', 'o')}
    for old in IR.iterdir():
        if old.is_file() and old.name not in keep and old.name != 'offsets.json' and not old.name.startswith('offsets_probe'):
            old.unlink()
    for old in IR_SPLIT.glob('output_*.cpp'):
        if old.name not in stripped:
            old.unlink()


if __name__ == '__main__':
    main()

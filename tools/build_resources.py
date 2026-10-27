"""Build scheduling and verified cache recovery; never changes compiler flags."""
from pathlib import Path
import ctypes, hashlib, json, os

GIB = 1024 ** 3
ORCHESTRATION = {'tools/build_resources.py', 'tools/build_native.py',
                 'tools/clean_room_build.py', 'tools/builder_revision.py'}

def available_memory():
    if os.name == 'nt':
        class Status(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    'physical', 'available_physical', 'commit', 'available_commit',
                    'virtual', 'available_virtual', 'extended')]
        state = Status(); state.length = ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return min(state.available_physical, state.available_commit)
    else:
        try: return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (ValueError, OSError, AttributeError): pass
    return None

def compile_jobs(requested, memory=None, cpus=None):
    # Large promoted translation units can consume gigabytes each. Leave room
    # for the OS and launcher rather than starting one compiler per CPU thread.
    requested = max(1, int(requested)); cpus = cpus or os.cpu_count() or 2
    memory = available_memory() if memory is None else memory
    limit = max(1, (memory - 2 * GIB) // (3 * GIB)) if memory is not None else 2
    return min(requested, cpus, 8, limit)

def memory_failure(log):
    text = str(log).lower()
    return ('out of memory allocating' in text or 'virtual memory exhausted' in text or
            'std::bad_alloc' in text or 'killed signal terminated program cc1plus' in text)

def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()

def request(identity, dol, index):
    return dict(schema=1, identity=identity, dol_sha256=sha(dol), index_sha256=sha(index),
                environment={k:v for k,v in sorted(os.environ.items()) if k.startswith('NWII_')})

def cache_state(dest, wanted):
    """Accept only the same native patch inputs, disc, index and build environment."""
    dest = Path(dest); port = dest/'native-port'
    try:
        saved = json.loads((port/'builder-revision.json').read_text())
        current = wanted['identity']
        if saved['revision'] != current['revision']: return None
        old_inputs = saved['required_inputs']; new_inputs = current['required_inputs']
        if {k:v for k,v in old_inputs.items() if k not in ORCHESTRATION} != {
                k:v for k,v in new_inputs.items() if k not in ORCHESTRATION}: return None
        # Check the cache's declared source inputs, including its original build tools.
        for name, digest in old_inputs.items():
            path = (port/name).resolve()
            if not path.is_relative_to(port.resolve()) or sha(path) != digest: return None
        if sha(dest/'orig/main.dol') != wanted['dol_sha256'] or sha(dest/'export/index.tsv') != wanted['index_sha256']: return None
        recorded = dest/'build-request.json'
        if recorded.exists():
            previous = json.loads(recorded.read_text())
            if any(previous.get(k) != wanted[k] for k in ('dol_sha256','index_sha256','environment')): return None
        elif wanted['environment']:
            # Older builders did not record compiler experiment environment values.
            return None
        exe = dest/'storage/toolchain/CrashNative.exe'
        metadata = exe.with_name('runtime-build.json')
        if exe.is_file() and metadata.is_file():
            data = json.loads(metadata.read_text())
            from builder_revision import digest as inputs_digest
            if (data.get('builder_revision') == current['revision'] and data.get('builder_inputs') == inputs_digest(current) and
                    data.get('dol_sha256') == wanted['dol_sha256'] and
                    data.get('executable_sha256') == sha(exe)):
                return 'complete'
            # Never bless an altered executable merely because Ninja considers
            # its timestamp current. A broken completed cache needs a fresh build.
            return None
        stages = json.loads((port/'reports/native-build-stages.json').read_text())
        if (stages and stages[-1]['stage'] == 'native-game-build' and
                all(row['exit_code'] == 0 for row in stages[:-1]) and
                (dest/'storage/toolchain/CMakeCache.txt').is_file()):
            return 'compile'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None

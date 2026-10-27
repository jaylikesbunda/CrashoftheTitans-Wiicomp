"""Snapshots of original translated functions that stages compare against or restore.

They are copies of generated game code, so they live beside the generated tree in the build storage, never in the
repository. A stage takes the snapshot from the freshly generated source the first time it runs and checks it against
the hash recorded when the transform was qualified.
"""
from pathlib import Path
import hashlib
import region

PORT = Path(__file__).resolve().parents[1]


def directory():
    d = (PORT / 'build/crash-generated').resolve().parent / 'guest-baselines'
    d.mkdir(parents=True, exist_ok=True)
    return d


def path(name):
    """Location of snapshot `name`; a developer tree's older copy under runtime-overlays/ is migrated on first use."""
    target = directory() / name
    legacy = PORT / 'runtime-overlays' / name
    if not target.exists() and legacy.is_file() and region.active().reference:
        target.write_text(legacy.read_text())
    return target


def function_text(source, addr):
    """The complete `void func_<addr>(CPUContext& ctx) {...}` definition in `source`."""
    a = source.index('void func_' + addr + '(CPUContext& ctx) {')
    depth = 0
    for i in range(source.index('{', a), len(source)):
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[a:i + 1]
    raise ValueError('Unterminated function ' + addr)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def snapshot(name, generated_file, addr, expected_sha256=None, suffix='\n\n'):
    """Return snapshot `name` of func_<addr>, taking it from `generated_file` if it does not exist yet."""
    target = path(name)
    if target.exists():
        text = target.read_text()
    else:
        text = function_text(Path(generated_file).read_text(), addr) + suffix
        if expected_sha256 and not region.active().trusted(expected_sha256, sha256(text), int(addr, 16)):
            raise SystemExit('Generated source of ' + addr + ' differs from the qualified source: requalify it')
        target.write_text(text)
    if expected_sha256:
        assert region.active().trusted(expected_sha256, sha256(text), int(addr, 16)), 'Baseline ' + name + ' changed: requalify ' + addr
    return text

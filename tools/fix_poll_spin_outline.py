"""NATIVE_POLL_SPIN_OUTLINE_V1: the poll-site "Spinning at PC" console line out of the promoted hot units' code.

Every generated poll site ends with

    if ((ctx.inst_count % 10000000) == 0) { std::cout << "Spinning at PC: 0x" << std::hex << 0xADDR << " LR: 0x" << std::hex << ctx.lr << std::dec << std::endl; }

which inlines a chain of iostream calls into each site. In build/crash-hot this stage makes it

    if ((ctx.inst_count % 10000000) == 0) native_guest_fast::Spinning(0xADDR, ctx.lr); /* NATIVE_POLL_SPIN_OUTLINE_V1 */

(runtime-overlays/native_guest_fast.h: one noinline cold function printing the same line), so the site carries only the
inst_count test. Same condition, same output. Runs after every stage that matches the original poll text (poll credit,
event mask, reload elision, guest batch, mem proofs, arena) and restores before all of them; idempotent.
NWII_BUILD_POLL_SPIN_OUTLINE=0 stages nothing (and restores).
"""
from pathlib import Path
import argparse, os, re

MARK = 'NATIVE_POLL_SPIN_OUTLINE_V1'
ORIGINAL = re.compile(r'if \(\(ctx\.inst_count % 10000000\) == 0\) \{ std::cout << "Spinning at PC: 0x" << std::hex << (0x[0-9A-Fa-f]+) '
                      r'<< " LR: 0x" << std::hex << ctx\.lr << std::dec << std::endl; \}')
OUTLINED = re.compile(r'if \(\(ctx\.inst_count % 10000000\) == 0\) native_guest_fast::Spinning\((0x[0-9A-Fa-f]+), ctx\.lr\); /\* ' + MARK + r' \*/')


def restore(text):
    return OUTLINED.sub(lambda m: 'if ((ctx.inst_count % 10000000) == 0) { std::cout << "Spinning at PC: 0x" << std::hex << ' + m[1] +
                        ' << " LR: 0x" << std::hex << ctx.lr << std::dec << std::endl; }', text)


def outline(text):
    return ORIGINAL.sub(lambda m: 'if ((ctx.inst_count % 10000000) == 0) native_guest_fast::Spinning(' + m[1] + ', ctx.lr); /* ' + MARK + ' */',
                        restore(text))


def apply(port, undo=False):
    enabled = os.environ.get('NWII_BUILD_POLL_SPIN_OUTLINE', '1') != '0' and not undo
    sites = files = 0
    for path in sorted((port / 'build/crash-hot').glob('*.cpp')):
        old = path.read_bytes().decode('utf-8', errors='surrogateescape')
        # Only units that see native_guest_fast.h (every promoted hot unit includes it).
        new = outline(old) if enabled and 'runtime/native_guest_fast.h' in old else restore(old)
        if new != old:
            path.write_bytes(new.encode('utf-8', errors='surrogateescape'))
            files += 1
        sites += len(OUTLINED.findall(new))
    print('Poll spin outline ' + ('staged: %d sites' % sites if enabled else 'restored/disabled') + ' (%d files changed)' % files)
    return sites


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args()
    apply(a.port, a.restore)

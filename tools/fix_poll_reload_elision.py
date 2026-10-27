"""NATIVE_POLL_RELOAD_ELISION_V1: drop the promoted-register reload at ordinary poll sites.

The promoter treats `if (nwii::runtime::native_poll_due()) { ... process_pending_callbacks(ctx); ... }`
as an opaque barrier: it writes the dirty promoted registers into ctx before the call and reads the
live ones back afterwards.  In the hottest function (func_803143EC, 2,128 guest instructions) that
is 48 stores plus 59 loads per *taken* poll, and the credit is forced to 1 while an IPC/DI interrupt
delay is counting (hw_ipc.cpp arms g_ipc_interrupt_delay with 50 or 5000), so those transfers land on
every backward branch for the whole countdown.

The reload half is dead code.  The emitted site is

    if (nwii::runtime::native_poll_due()) { <spill dirty> if (process_pending_callbacks(ctx)) return; <reload live> }

so the reload can only matter when the call returns false.  `tools/check_poll_reload_elision.py`
proves (and re-proves on every check run) that `process_pending_callbacks` returning false leaves
every promoted register unchanged: in the whole runtime the only writes to ctx.gpr/ctr/inst_count/
xer/fpr/ps1/cr reachable from the poll are

  * native_process_callbacks_slow - all 14 writes end in `longjmp(ctx.exception_jmp_buf, 1)` (the
    caller's frame is discarded, so its reload never runs) or are followed by `return true`
    (the site then executes its own `return;`), and
  * hle_load_context_from_guest - writes ctr/xer only after its early `return false` and then
    returns true,

and no other function on the poll path (native_wpad_poll/advance/packet, hle_drive_thread_queue,
native_gx_publish, the EventScheduler callbacks, the fp-context probe, watch_hit) writes a promoted
register at all.  On a false return the locals therefore still hold the guest state, and the spilled
ctx agrees with them, so removing the reload changes nothing observable.

What stays: the spill (the callee reads guest state), the call, the `return` on true, and the whole
slow-path behaviour - no runtime guard, no extra branch, no ordering change.  The same argument does
not cover the `ctx.pc != RET` checks of the inlined-indirect poll form, so those sites keep their
reload.

Scope: the promoted units (build/crash-hot/hot_*.cpp) and any promoted body inside a chunk (the
NATIVE_VERTEX_EMIT_V1 kernel, when this stage runs after it).  Idempotent; it re-derives the trimmed
text from the emitted line every pass.
Rollback: NWII_BUILD_POLL_RELOAD_ELISION=0.
"""
from pathlib import Path
import argparse
import os
import re
import sys

MARK = 'NATIVE_POLL_RELOAD_ELISION_V1'
ROOT = Path(__file__).resolve().parents[1]
NL = '\n'

# The generator can retain this read-only diagnostic between the poll and reload.
# Match its exact shape and preserve it; arbitrary statements still reject the site.
DIAGNOSTIC = (
    r'(?: if \(\(ctx\.inst_count % 10000000\) == 0\) \{ std::cout << "Spinning at PC: 0x"'
    r' << std::hex << 0x[0-9a-fA-F]+ << " LR: 0x" << std::hex << ctx\.lr << std::dec << std::endl; \})?')

POLL = re.compile(
    r'^(?P<indent>\s*)if \(nwii::runtime::native_poll_due\(\)\) \{ ?'
    r'(?P<spill>(?:ctx\.(?:gpr\[\d+\]|ctr|inst_count|xer|fpr\[\d+\]|ps1\[\d+\]|cr\[\d\]\.(?:lt|gt|eq|so)) = \w+; )*)'
    r'(?P<call>if \(process_pending_callbacks\(ctx\)\) return;)'
    r'(?P<diagnostic>' + DIAGNOSTIC + r')'
    r'(?P<reload>(?: \w+ = ctx\.(?:gpr\[\d+\]|ctr|inst_count|xer|fpr\[\d+\]|ps1\[\d+\]|cr\[\d\]\.(?:lt|gt|eq|so));)*)'
    r' \}$')
UNINSTALL = re.compile(
    r'^(?P<indent>\s*)if \(nwii::runtime::native_poll_due\(\)\) \{ ?'
    r'(?P<spill>(?:ctx\.(?:gpr\[\d+\]|ctr|inst_count|xer|fpr\[\d+\]|ps1\[\d+\]|cr\[\d\]\.(?:lt|gt|eq|so)) = \w+; )*)'
    r'(?P<call>if \(process_pending_callbacks\(ctx\)\) return;)'
    r'(?P<diagnostic>' + DIAGNOSTIC + r') \}$')


def trim(text):
    """Return (text, sites_trimmed, reload_transfers_removed).  Only the reload text is deleted."""
    trimmed = 0
    removed = 0
    out = []
    for line in text.split(NL):
        m = POLL.match(line)
        if m and m.group('reload'):
            removed += len(re.findall(r'\w+ = ctx\.', m.group('reload')))
            trimmed += 1
            out.append(line[:m.end('diagnostic')] + ' }')
        else:
            out.append(line)
    return NL.join(out), trimmed, removed


def census(text):
    trimmed = sum(1 for line in text.split(NL) if UNINSTALL.match(line) and 'native_poll_due' in line)
    with_reload = sum(1 for line in text.split(NL) if POLL.match(line) and POLL.match(line).group('reload'))
    return trimmed, with_reload


def units(root):
    files = sorted((root / 'build/crash-hot').glob('hot_*.cpp'))
    if files:
        return files
    # No promoted unit exists (fix_hot_split has not run, or was rolled back): trim the promoted
    # bodies that live in a chunk, which is where the NATIVE_VERTEX_EMIT_V1 kernel is installed.
    out = []
    for path in sorted((root / 'build/crash-generated').glob('output_*.cpp')):
        text = path.read_text(errors='surrogateescape')
        if 'native_vertex_emit_' in text or 'native_guest_resume_' in text:
            out.append(path)
    return out


def apply(root, enabled):
    total_trimmed = total_removed = 0
    for path in units(root):
        text = path.read_text(errors='surrogateescape')
        if enabled:
            new, trimmed, removed = trim(text)
        else:
            # Rollback: the emitted text is derived, so a rollback pass simply leaves whatever the
            # earlier stages (fix_hot_split / vertex_emit_kernel) produced - the untrimmed form.
            # Report how many sites are still in the trimmed form for the log.
            trimmed, removed = census(text)
            new = text
        if new != text:
            path.write_text(new, errors='surrogateescape')
        total_trimmed += trimmed
        total_removed += removed
    return total_trimmed, total_removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=ROOT)
    args = ap.parse_args()
    root = args.root.resolve()
    enabled = os.environ.get('NWII_BUILD_POLL_RELOAD_ELISION', '1') != '0'
    trimmed, removed = apply(root, enabled)
    if enabled:
        print('%s: %d poll sites trimmed, %d promoted-register reloads removed' % (MARK, trimmed, removed))
    else:
        print('%s disabled: %d sites already in the trimmed form' % (MARK, trimmed))
    return 0


if __name__ == '__main__':
    sys.exit(main())

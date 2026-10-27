"""Route quantised psq_load/psq_store through the already-inline pair helpers.

The psq_load/psq_store instruction helpers send every non-F32 GQR type to the
out-of-line native_psq_load_slow / native_psq_store_slow. The inline pair helpers
(psq_load_pair / psq_store_val, installed by fix_psq_value_helpers.py as
NATIVE_PSQ_QUANT_INLINE_V1) already implement types 0 and 4-7 inline and fall
back to the value-passing slow paths for types 1/2/3, whose bodies are textually
derived from the originals. This stage reuses them from the instruction helpers
so quantised loads/stores inline instead of making an out-of-line call per
access, which is exactly what the GX vertex emitters do (every access is
quantised). Semantics are unchanged; check_guest_psq_pairs.py already proves the
pair helpers equal the legacy forms across all GQR types and scales.

Build rollback: NWII_BUILD_PSQ_INSTRUCTION_INLINE=0 (leaves the helpers on the
out-of-line slow paths). Idempotent; the clean-room tree is regenerated each build.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_PSQ_INSTRUCTION_INLINE_V1'


def apply(s):
    if MARK in s:
        return s
    old_load = '    native_psq_load_slow(frD,addr,W,I);\n'
    new_load = ('    // ' + MARK + ': reuse the inline pair helper '
                '(types 0/4-7 inline; 1/2/3 via the value-passing slow path).\n'
                '    { auto pq_=psq_load_pair(addr,W,I); fpr[frD]=pq_.f; ps1[frD]=pq_.p; }\n')
    old_store = '    native_psq_store_slow(frS,addr,W,I);\n'
    new_store = ('    // ' + MARK + ': reuse the inline pair helper.\n'
                 '    psq_store_val(fpr[frS],ps1[frS],addr,W,I);\n')
    n_load = s.count(old_load)
    n_store = s.count(old_store)
    if (n_load, n_store) != (1, 1):
        raise SystemExit('psq instruction anchors not unique: load=%d store=%d' % (n_load, n_store))
    return s.replace(old_load, new_load, 1).replace(old_store, new_store, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_PSQ_INSTRUCTION_INLINE', '1') == '0':
        print('psq instruction inline disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('psq instruction inline staged')


if __name__ == '__main__':
    main()

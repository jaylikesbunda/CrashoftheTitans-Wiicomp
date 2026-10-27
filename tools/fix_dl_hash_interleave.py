"""Break the serial dependency chain in the display-list content digest.

gx.dl_hash is a full-list FNV-style chain over the snapshot bytes with a
per-8-byte serial dependency (ch ^= w; ch *= prime; ch ^= ch>>29), so each
iteration waits on the previous. Four independent accumulators, combined at the
end, remove the serialisation while keeping an exact, deterministic digest. The
digest is only ever computed and compared by this same code within one process
(it is the display-list cache validity check), so the changed value is not
observable. Build rollback: NWII_BUILD_DL_HASH_INTERLEAVE=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_DL_HASH_INTERLEAVE_V1'

OLD = ('            for (; i + 8 <= size; i += 8) { uint64_t w; std::memcpy(&w, src + i, 8); '
       'ch ^= w; ch *= 1099511628211ull; ch ^= ch >> 29; }\n'
       '            for (; i < size; i++) { ch ^= src[i]; ch *= 1099511628211ull; }\n')

NEW = ('            // ' + MARK + ': four independent chains, same mixing, combined at the end.\n'
       '            uint64_t c0 = ch, c1 = ch ^ 0x9E3779B97F4A7C15ull, c2 = ch ^ 0xC2B2AE3D27D4EB4Full, c3 = ch ^ 0x165667B19E3779F9ull;\n'
       '            for (; i + 32 <= size; i += 32) {\n'
       '                uint64_t w0, w1, w2, w3;\n'
       '                std::memcpy(&w0, src + i, 8); std::memcpy(&w1, src + i + 8, 8);\n'
       '                std::memcpy(&w2, src + i + 16, 8); std::memcpy(&w3, src + i + 24, 8);\n'
       '                c0 ^= w0; c0 *= 1099511628211ull; c0 ^= c0 >> 29;\n'
       '                c1 ^= w1; c1 *= 1099511628211ull; c1 ^= c1 >> 29;\n'
       '                c2 ^= w2; c2 *= 1099511628211ull; c2 ^= c2 >> 29;\n'
       '                c3 ^= w3; c3 *= 1099511628211ull; c3 ^= c3 >> 29;\n'
       '            }\n'
       '            for (; i + 8 <= size; i += 8) { uint64_t w; std::memcpy(&w, src + i, 8); c0 ^= w; c0 *= 1099511628211ull; c0 ^= c0 >> 29; }\n'
       '            for (; i < size; i++) { c0 ^= src[i]; c0 *= 1099511628211ull; }\n'
       '            ch = c0 ^ (c1 * 1099511628211ull) ^ (c2 * 1099511628211ull) ^ (c3 * 1099511628211ull);\n')


def apply(s):
    if MARK in s:
        return s
    if s.count(OLD) != 1:
        raise SystemExit('dl_hash anchor not unique: %d' % s.count(OLD))
    return s.replace(OLD, NEW, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_DL_HASH_INTERLEAVE', '1') == '0':
        print('dl hash interleave disabled')
        return
    path = args.port / 'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('dl hash interleave staged')


if __name__ == '__main__':
    main()

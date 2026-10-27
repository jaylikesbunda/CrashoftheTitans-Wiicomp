"""NATIVE_MISC_HOT_PATHS_V1: two contained guest-thread trims.

S1: the out-of-line `native_write{16,32,f32}_slow` bodies store via four/five separate byte
    statements; with -march=x86-64-v3 (movbe) a single byte-swapped `memcpy` is one instruction
    and strictly fewer stores. Byte-identical to the host.
S2: `idle_wait` inside `native_process_callbacks_poll` is a function-local `static const bool`
    evaluated on every poll; hoist it to namespace scope (same pattern as NATIVE_POLL_GUARD_OUTLINE_V1).
Build rollback: NWII_BUILD_MISC_HOT_PATHS=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_MISC_HOT_PATHS_V1'

CTX_REPLACEMENTS = [
    (
        "    uint8_t *ptr = get_ptr(addr);\n"
        "    if (ptr) { ptr[0] = value >> 8; ptr[1] = value & 0xFF; }\n"
        "  }",
        "    uint8_t *ptr = get_ptr(addr);\n"
        "    if (ptr) { uint16_t bits = __builtin_bswap16(value); std::memcpy(ptr, &bits, 2); } // NATIVE_MISC_HOT_PATHS_V1\n"
        "  }",
    ),
    (
        "    uint8_t *ptr = get_ptr(addr);\n"
        "    if (ptr) { ptr[0] = value >> 24; ptr[1] = (value >> 16) & 0xFF; ptr[2] = (value >> 8) & 0xFF; ptr[3] = value & 0xFF; }\n"
        "  }",
        "    uint8_t *ptr = get_ptr(addr);\n"
        "    if (ptr) { uint32_t bits = __builtin_bswap32(value); std::memcpy(ptr, &bits, 4); } // NATIVE_MISC_HOT_PATHS_V1\n"
        "  }",
    ),
    (
        "    uint8_t *ptr = get_ptr(addr);\n"
        "    if (ptr) {\n"
        "      ptr[0] = v >> 24; ptr[1] = (v >> 16) & 0xFF; ptr[2] = (v >> 8) & 0xFF; ptr[3] = v & 0xFF;\n"
        "    }",
        "    uint8_t *ptr = get_ptr(addr);\n"
        "    if (ptr) { uint32_t bits = __builtin_bswap32(v); std::memcpy(ptr, &bits, 4); } // NATIVE_MISC_HOT_PATHS_V1",
    ),
]

IOS_REPLACEMENTS = [
    (
        "static inline bool native_process_callbacks_poll(CPUContext &ctx) {",
        "// NATIVE_MISC_HOT_PATHS_V1: launch-fixed idle-wait flag at namespace scope (guard load removed).\n"
        "static const bool native_poll_launch_idle_wait = std::getenv(\"NWII_WALLTB\") && !std::getenv(\"NWII_NO_IDLE_WAIT\");\n"
        "static inline bool native_process_callbacks_poll(CPUContext &ctx) {",
    ),
    (
        "  static const bool idle_wait = std::getenv(\"NWII_WALLTB\") && !std::getenv(\"NWII_NO_IDLE_WAIT\");",
        "  const bool idle_wait = native_poll_launch_idle_wait; // NATIVE_MISC_HOT_PATHS_V1",
    ),
]


def _apply(path, replacements):
    s = path.read_text()
    if MARK in s:
        return False
    for old, new in replacements:
        n = s.count(old)
        if n != 1:
            raise SystemExit('%s: anchor matched %d times (expected 1): %r' % (path.name, n, old[:60]))
        s = s.replace(old, new, 1)
    path.write_text(s)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_MISC_HOT_PATHS', '1') == '0':
        print('misc hot paths disabled')
        return
    port = args.port
    a = _apply(port / 'build/nwii-full-source/nWiiRuntime/include/runtime/cpu_context.h', CTX_REPLACEMENTS)
    b = _apply(port / 'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp', IOS_REPLACEMENTS)
    print('misc hot paths staged (cpu_context=%s ios=%s)' % (a, b))


if __name__ == '__main__':
    main()

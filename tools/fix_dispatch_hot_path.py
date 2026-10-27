"""NATIVE_DISPATCH_HOT_PATH_V1: remove two per-dispatch costs in the run_game loop.

1. The NATIVE_DISPATCH_HOT_TRIM_V1 PC-history flag was declared *inside* the `while (ctx.is_running)`
   loop, so its function-local-static guard was evaluated on every dispatch (~9M/capture). Move it to
   file scope.
2. The PC alias normalisation ran three compares for the common 0x8xxxxxxx/0x9xxxxxxx case; a single
   unsigned range test covers it and falls through to the identical C0/D0/low branches otherwise.
Build rollback: NWII_BUILD_DISPATCH_HOT_PATH=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_DISPATCH_HOT_PATH_V1'

REPLACEMENTS = [
    (
        "extern \"C\" void run_game(nwii::runtime::CPUContext& ctx) {",
        "// NATIVE_DISPATCH_HOT_PATH_V1: launch-fixed PC-history flag at file scope (was a guard\n"
        "// evaluated on every dispatch inside the loop).\n"
        "static const bool native_dispatch_history=std::getenv(\"NWII_DISPATCH_HISTORY\")!=nullptr;\n"
        "extern \"C\" void run_game(nwii::runtime::CPUContext& ctx) {",
    ),
    (
        "      static const bool native_dispatch_history=std::getenv(\"NWII_DISPATCH_HISTORY\")!=nullptr; // NATIVE_DISPATCH_HOT_TRIM_V1\n"
        "      if(native_dispatch_history){ pc_history[pc_history_idx] = ctx.pc; if (++pc_history_idx == 10) pc_history_idx = 0; }",
        "      if(native_dispatch_history){ pc_history[pc_history_idx] = ctx.pc; if (++pc_history_idx == 10) pc_history_idx = 0; } // NATIVE_DISPATCH_HOT_PATH_V1",
    ),
    (
        "        if ((ctx.pc & 0xF0000000) == 0xC0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x80000000;\n"
        "        else if ((ctx.pc & 0xF0000000) == 0xD0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x90000000;\n"
        "        else if (ctx.pc < 0x80000000) ctx.pc |= 0x80000000;",
        "        // NATIVE_DISPATCH_PC_NORMALIZE_V1: one range test for the common cached/uncached case.\n"
        "        if (__builtin_expect(ctx.pc - 0x80000000u < 0x20000000u, 1)) { }\n"
        "        else if ((ctx.pc & 0xF0000000) == 0xC0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x80000000;\n"
        "        else if ((ctx.pc & 0xF0000000) == 0xD0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x90000000;\n"
        "        else if (ctx.pc < 0x80000000) ctx.pc |= 0x80000000;",
    ),
]


def apply(s):
    if MARK in s:
        return s
    out = s
    for old, new in REPLACEMENTS:
        n = out.count(old)
        if n != 1:
            raise SystemExit('dispatch hot-path anchor matched %d times (expected 1): %r' % (n, old[:70]))
        out = out.replace(old, new, 1)
    # the identical normalisation in native_call_indirect (4-space indent)
    old2 = ("    if ((ctx.pc & 0xF0000000) == 0xC0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x80000000;\n"
            "    else if ((ctx.pc & 0xF0000000) == 0xD0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x90000000;\n"
            "    else if (ctx.pc < 0x80000000) ctx.pc |= 0x80000000;")
    new2 = ("    // NATIVE_DISPATCH_PC_NORMALIZE_V1\n"
            "    if (__builtin_expect(ctx.pc - 0x80000000u < 0x20000000u, 1)) { }\n"
            "    else if ((ctx.pc & 0xF0000000) == 0xC0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x80000000;\n"
            "    else if ((ctx.pc & 0xF0000000) == 0xD0000000) ctx.pc = (ctx.pc & 0x0FFFFFFF) | 0x90000000;\n"
            "    else if (ctx.pc < 0x80000000) ctx.pc |= 0x80000000;")
    if out.count(old2) == 1:
        out = out.replace(old2, new2, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_DISPATCH_HOT_PATH', '1') == '0':
        print('dispatch hot path disabled')
        return
    path = args.port / 'build/crash-generated/main_output.cpp'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('dispatch hot path staged')


if __name__ == '__main__':
    main()

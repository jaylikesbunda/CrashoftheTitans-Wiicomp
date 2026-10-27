#pragma once
// NATIVE_WATCH_FOLD_V1: compile-time switch for the per-store guest-RAM watch test.
//
// 1 (shipping): the debug watch (NWII_WATCH, scheduler recorder) is compiled out. native_watch_span_may_hit is
//   constant false, the MMU store fast paths drop their `g_watch_addr == snapshot` and write-gather watch tests,
//   and g_watch_addr stays 0, so every inline guest store is "bank check + store".
// 0 (diagnostic): the sources are byte-identical to the unfolded staging.
// tools/fix_watch_fold.py rewrites the value on the next line from NWII_BUILD_WATCH_FOLD (default 1) and applies
// or removes the matching edits; every translation unit includes this header via cpu_context.h, so the value
// cannot differ between the generated code and the runtime.
#define NWII_WATCH_FOLDED 1 // NATIVE_WATCH_FOLD_MODE

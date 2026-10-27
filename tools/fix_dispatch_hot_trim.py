"""NATIVE_DISPATCH_HOT_TRIM_V1: shrink the exact-tag dispatch cache and gate the diagnostic PC ring.

find_dispatch_target's hot cache is 4096 x 16 B = 64 KiB, larger than the 32 KiB L1D, so every
indirect dispatch pays an L2 hit before the (already O(1)) dense resolver. Halving it to 2048
entries (32 KiB) makes the probe L1-resident. The PC-history ring writes two globals on every
dispatch (~9 M/capture) for crash diagnostics only; gate it behind NWII_DISPATCH_HISTORY.
Build rollback: NWII_BUILD_DISPATCH_HOT_TRIM=0. Idempotent.
"""
from pathlib import Path
import argparse
import os

MARK = 'NATIVE_DISPATCH_HOT_TRIM_V1'

REPLACEMENTS = [
    # NATIVE_DISPATCH_DENSE_HOIST_V1: the -8 measurement showed find_dispatch_target_dense rising
    # (1.01% -> 1.48%) after the 2048-slot shrink, so the cache stays at 4096 and the dense tables
    # move to file scope instead -- no per-call guard branch and no heap-vector indirection on a miss.
    (
        """static DispatchChoice find_dispatch_target_dense(uint32_t target) {
    constexpr size_t count=std::size(g_func_bounds)-1;
    constexpr size_t overrides=std::size(g_dispatch_overrides)-1;
    static_assert(count+overrides<65536);
    constexpr uint32_t low=g_func_bounds[0].start;
    static const uint32_t high=[](){uint32_t h=low;for(auto& b:g_func_bounds)if(b.end>h)h=b.end;return h;}();
    static const auto words=[](){
        std::vector<uint16_t> table((high-low+3u)/4u);
        // Reverse fill retains the original first-containing-range priority.
        for(size_t i=count;i-->0;){auto& b=g_func_bounds[i];
            const size_t first=(uint64_t(b.start)-low+3u)/4u;
            const size_t end=(uint64_t(b.end)-low+3u)/4u;
            std::fill(table.begin()+first,table.begin()+end,uint16_t(i+1));
        }
        for(size_t i=0;i<overrides;++i){auto& o=g_dispatch_overrides[i];
            if(!(o.pc&3u) && o.pc>=low && o.pc<high)table[(o.pc-low)/4u]=uint16_t(count+i+1);
        }
        return table;
    }();
    const uint32_t offset=target-low;
    if((target&3u) || offset>=high-low)return find_dispatch_target_uncached(target);
    const unsigned id=words[offset/4u];
    if(id>count){auto out=g_dispatch_overrides[id-count-1];out.pc=target;return out;}
    return {target,id?g_func_bounds[id-1].func:nullptr,false};
}""",
        """// NATIVE_DISPATCH_DENSE_HOIST_V1: launch-constant dense tables at file scope (no per-call
// guard on `high`/`words` and no heap-vector indirection through a function-local static).
static constexpr size_t native_dense_count=std::size(g_func_bounds)-1;
static constexpr size_t native_dense_overrides=std::size(g_dispatch_overrides)-1;
static_assert(native_dense_count+native_dense_overrides<65536);
static constexpr uint32_t native_dense_low=g_func_bounds[0].start;
static const uint32_t native_dense_high=[](){uint32_t h=native_dense_low;for(auto& b:g_func_bounds)if(b.end>h)h=b.end;return h;}();
static const std::vector<uint16_t> native_dense_words=[](){
    std::vector<uint16_t> table((native_dense_high-native_dense_low+3u)/4u);
    // Reverse fill retains the original first-containing-range priority.
    for(size_t i=native_dense_count;i-->0;){auto& b=g_func_bounds[i];
        const size_t first=(uint64_t(b.start)-native_dense_low+3u)/4u;
        const size_t end=(uint64_t(b.end)-native_dense_low+3u)/4u;
        std::fill(table.begin()+first,table.begin()+end,uint16_t(i+1));
    }
    for(size_t i=0;i<native_dense_overrides;++i){auto& o=g_dispatch_overrides[i];
        if(!(o.pc&3u) && o.pc>=native_dense_low && o.pc<native_dense_high)table[(o.pc-native_dense_low)/4u]=uint16_t(native_dense_count+i+1);
    }
    return table;
}();
static DispatchChoice find_dispatch_target_dense(uint32_t target) {
    const uint32_t offset=target-native_dense_low;
    if((target&3u) || offset>=native_dense_high-native_dense_low)return find_dispatch_target_uncached(target);
    const unsigned id=native_dense_words[offset/4u];
    if(id>native_dense_count){auto out=g_dispatch_overrides[id-native_dense_count-1];out.pc=target;return out;}
    return {target,id?g_func_bounds[id-1].func:nullptr,false};
}""",
    ),
    (
        """      pc_history[pc_history_idx] = ctx.pc;
      if (++pc_history_idx == 10) pc_history_idx = 0; // NATIVE_DISPATCH_HISTORY_V1""",
        """      static const bool native_dispatch_history=std::getenv("NWII_DISPATCH_HISTORY")!=nullptr; // NATIVE_DISPATCH_HOT_TRIM_V1
      if(native_dispatch_history){ pc_history[pc_history_idx] = ctx.pc; if (++pc_history_idx == 10) pc_history_idx = 0; }""",
    ),
]


# Applied-probes per replacement, because NATIVE_DISPATCH_HOT_PATH_V1 (which runs later, on the
# same file) rewrites replacement 1's line and drops the `// NATIVE_DISPATCH_HOT_TRIM_V1` comment
# with it. Without these probes a second pass sees no MARK, re-runs the anchors against the
# already-rewritten dispatcher and asserts.
PROBES = (
    ('NATIVE_DISPATCH_DENSE_HOIST_V1',),                     # replacement 0, untouched by the later stage
    ('NATIVE_DISPATCH_HOT_TRIM_V1',                          # replacement 1 as emitted here
     '// NATIVE_DISPATCH_HOT_PATH_V1'),                      # ... and after the later stage moved the flag
)


def apply(s):
    if MARK in s:
        return s
    out = s
    for (old, new), probes in zip(REPLACEMENTS, PROBES):
        if any(p in out for p in probes):
            continue
        n = out.count(old)
        if n != 1:
            raise SystemExit('dispatch trim anchor matched %d times (expected 1): %r' % (n, old[:70]))
        out = out.replace(old, new, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()
    if os.environ.get('NWII_BUILD_DISPATCH_HOT_TRIM', '1') == '0':
        print('dispatch hot trim disabled')
        return
    path = args.port / 'build/crash-generated/main_output.cpp'
    before = path.read_text()
    after = apply(before)
    if before != after:
        path.write_text(after)
    print('dispatch hot trim staged')


if __name__ == '__main__':
    main()

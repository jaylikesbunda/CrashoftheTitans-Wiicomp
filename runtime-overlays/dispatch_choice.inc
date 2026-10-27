static DispatchChoice find_dispatch_target_uncached(uint32_t target) {
    size_t lo = 0, hi = sizeof(g_dispatch_overrides) / sizeof(g_dispatch_overrides[0]) - 1;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (g_dispatch_overrides[mid].pc < target) lo = mid + 1;
        else hi = mid;
    }
    if (g_dispatch_overrides[lo].pc == target && g_dispatch_overrides[lo].func)
        return g_dispatch_overrides[lo];
    const auto* fb = find_func_bound(target);
    return {target, fb ? fb->func : nullptr, false};
}

// NATIVE_DISPATCH_CACHE_V1: immutable translated map; full-PC tag preserves
// interior entry points, gaps, byte alignment and special HLE return semantics.
static DispatchChoice find_dispatch_target(uint32_t target) {
    static std::array<DispatchChoice, 4096> cache{};
    auto& entry=cache[((target>>2)^(target>>14))&4095u];
    if(entry.pc==target)return entry;
    entry=find_dispatch_target_uncached(target);
    return entry;
}

// NATIVE_DISPATCH_CACHE_V1: immutable translated map; full-PC tag preserves
// interior entry points, gaps, byte alignment and special HLE return semantics.
static DispatchChoice find_dispatch_target(uint32_t target) {
    static std::array<DispatchChoice, 4096> cache{};
    auto& entry=cache[((target>>2)^(target>>14))&4095u];
    if(entry.pc==target)return entry;
    entry=find_dispatch_target_uncached(target);
    return entry;
}

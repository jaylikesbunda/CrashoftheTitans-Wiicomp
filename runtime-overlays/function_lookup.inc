// NATIVE_BINARY_FUNCTION_LOOKUP_V1: retain the first containing range,
// including overlapping imported extents, using monotonic prefix maxima.
static const FuncBound* find_func_bound(uint32_t target) {
    constexpr size_t count = sizeof(g_func_bounds) / sizeof(g_func_bounds[0]) - 1;
    static const auto prefix_ends = []() {
        std::array<uint32_t, count> ends{};
        uint32_t high = 0;
        for (size_t i = 0; i < count; ++i) {
            if (g_func_bounds[i].end > high) high = g_func_bounds[i].end;
            ends[i] = high;
        }
        return ends;
    }();
    size_t lo = 0, hi = count;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (prefix_ends[mid] <= target) lo = mid + 1;
        else hi = mid;
    }
    if (lo == count) return nullptr;
    const auto& fb = g_func_bounds[lo];
    return fb.start <= target ? &fb : nullptr;
}

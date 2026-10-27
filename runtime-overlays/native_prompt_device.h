#pragma once
#include <atomic>
#include <cstdint>

namespace native_prompts {
// Shared between the SDL host thread and the rendering thread. No guest state is changed.
inline std::atomic<unsigned> device{2}; // Xbox, PlayStation, keyboard/mouse
struct Activity {
    unsigned selected = 2;
    unsigned pending = 2;
    uint64_t changed = 0;
    bool select(unsigned candidate,uint64_t now) {
        if(candidate>=3)return false;
        pending=candidate;const bool different=selected!=candidate;
        selected=candidate;changed=now;return different;
    }
    bool update(unsigned candidate, uint64_t now) {
        if(candidate < 3) pending = candidate;
        if (pending == selected || now - changed < 250) return false;
        selected = pending; changed = now;
        return true;
    }
};
}

#pragma once
// NATIVE_DIRECT_FIFO_STREAM_V1
//
// Ordered, drain-local replacement for std::vector<GXCommand>.
//
// Register packets (BP 0x61 / CP 0x08 / seam RMW 0x78) are stored inline as
// 12-byte POD entries: no GXCommand is constructed for them. XF bursts, draws
// and the rare BP 0x65 TLUT snapshot keep a full GXCommand in `heavy`,
// referenced by index. `entries` order is the FIFO order.
//
// This is emitted directly by FifoParser::ParseStream; it is not a post-parse
// conversion of a GXCommand vector.
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <vector>
#include "runtime/gx_command.h"

namespace nwii::runtime::gx {

inline bool NativeDirectFifoEnabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("NWII_DIRECT_FIFO");
        return value && std::strcmp(value, "1") == 0 &&
               std::getenv("NWII_NO_DIRECT_FIFO") == nullptr;
    }();
    return enabled;
}

struct StreamEntry {
    static constexpr uint32_t kNoHeavy = 0xffffffffu;
    uint32_t val   = 0;                // BP/CP register value
    uint32_t heavy = kNoHeavy;         // index into CommandStream::heavy
    uint8_t  kind  = 0;                // GXCommandType
    uint8_t  reg   = 0;                // BP/CP register; XF address for XF kinds
    uint16_t flags = 0;
};
static_assert(sizeof(StreamEntry) == 12, "Compact FIFO record layout changed");

enum : uint16_t {
    kStreamSignalled    = 1u << 0, // PE token/finish already delivered for this entry
    kStreamTlutSnapshot = 1u << 1, // heavy GXCommand carries native_tlut bytes
    kStreamXfNoopBoundary = 1u << 2, // reuse an XF payload; retain draw boundary
};

struct CommandStream {
    std::vector<StreamEntry> entries;
    std::vector<GXCommand>   heavy;

    bool empty() const { return entries.empty(); }
    void clear() { entries.clear(); heavy.clear(); }
    void reserve(size_t entries_n, size_t heavy_n = 0) { entries.reserve(entries_n); if (heavy_n) heavy.reserve(heavy_n); }

    GXCommand*       Heavy(StreamEntry& e)       { return e.heavy == StreamEntry::kNoHeavy ? nullptr : &heavy[e.heavy]; }
    const GXCommand* Heavy(const StreamEntry& e) const { return e.heavy == StreamEntry::kNoHeavy ? nullptr : &heavy[e.heavy]; }

    // Direct emission used by the parser. `EmitHeavy` returns a reference into
    // `heavy` that stays valid until the next heavy emit; the entry is appended
    // in FIFO order at the same time.
    StreamEntry& EmitRegister(GXCommandType type, uint8_t reg, uint32_t val) {
        entries.push_back(StreamEntry{val, StreamEntry::kNoHeavy, (uint8_t)type, reg, 0});
        return entries.back();
    }
    GXCommand& EmitHeavy(GXCommandType type, uint16_t reg = 0, uint32_t val = 0) {
        heavy.emplace_back();
        GXCommand& c = heavy.back();
        c.type = type;
        c.reg = reg;
        entries.push_back(StreamEntry{val, (uint32_t)(heavy.size() - 1), (uint8_t)type, (uint8_t)reg, 0});
        return c;
    }

    // DL-cache replay: copy src's ordered entries, rewriting heavy indices.
    void AppendFrom(const CommandStream& src) {
        for (const StreamEntry& e : src.entries) {
            StreamEntry copy = e;
            if (e.heavy != StreamEntry::kNoHeavy) {
                heavy.push_back(src.heavy[e.heavy]);
                copy.heavy = (uint32_t)(heavy.size() - 1);
            }
            entries.push_back(copy);
        }
    }
    // DL-cache store: move src's storage wholesale (src becomes empty).
    void TakeFrom(CommandStream& src) {
        const uint32_t hbase = (uint32_t)heavy.size();
        const size_t ebase = entries.size();
        entries.insert(entries.end(), std::make_move_iterator(src.entries.begin()),
                       std::make_move_iterator(src.entries.end()));
        heavy.insert(heavy.end(), std::make_move_iterator(src.heavy.begin()),
                     std::make_move_iterator(src.heavy.end()));
        for (size_t i = ebase; i < entries.size(); ++i) {
            StreamEntry& e = entries[i];
            if (e.heavy != StreamEntry::kNoHeavy) e.heavy += hbase;
        }
        src.entries.clear(); src.heavy.clear();
    }
};

} // namespace nwii::runtime::gx

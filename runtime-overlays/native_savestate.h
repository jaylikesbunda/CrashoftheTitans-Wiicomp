// NATIVE_SAVESTATE_V1: snapshot of guest-visible state at a dispatcher boundary (run_game loop head).
//
// At that point no host frame holds guest state: callbacks and thread switches unwind to run_game, so the CPU context and guest
// memory are complete. Each device module owns one function that both writes and reads its file-scope state through Stream
// (symmetric serialization) and, when loading, re-arms the scheduler events its saved registers imply. ios.cpp orchestrates.
//
// Saving waits for a quiet point: no callback in flight or queued, no IPC/DI/ARAM transfer pending, the DSP idle and the GX FIFO
// drained by the render thread. Loading happens at the first dispatcher step after a normal boot, before guest code runs, so all
// host devices are freshly initialised and the scheduler is empty.
#pragma once
#include <atomic>
#include <cstdint>
#include <cstring>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

namespace native_ss {

inline constexpr uint32_t kFormat = 1;   // launcher savestates.rs FORMAT

struct Stream {
  bool loading = false;
  std::vector<uint8_t> buf;
  size_t pos = 0;

  void raw(void* p, size_t n) {
    if (loading) {
      if (n > buf.size() - pos) throw std::runtime_error("save state is truncated");
      std::memcpy(p, buf.data() + pos, n); pos += n;
    } else {
      const auto* b = static_cast<const uint8_t*>(p);
      buf.insert(buf.end(), b, b + n);
    }
  }
  template <class T> void pod(T& v) { static_assert(std::is_trivially_copyable_v<T>); raw(&v, sizeof v); }
  template <class T> void atom(std::atomic<T>& a) { T v = a.load(); pod(v); if (loading) a.store(v); }
  void str(std::string& s) {
    uint32_t n = uint32_t(s.size()); pod(n);
    if (loading) { if (n > (64u << 20)) throw std::runtime_error("save state string too long"); s.resize(n); }
    if (n) raw(s.data(), n);
  }
  template <class T> void vec(std::vector<T>& v) {
    static_assert(std::is_trivially_copyable_v<T>);
    uint32_t n = uint32_t(v.size()); pod(n);
    if (loading) { if (uint64_t(n) * sizeof(T) > buf.size() - pos) throw std::runtime_error("save state vector too long"); v.resize(n); }
    if (n) raw(v.data(), size_t(n) * sizeof(T));
  }
  template <class T> void deq(std::deque<T>& d) { std::vector<T> v(d.begin(), d.end()); vec(v); if (loading) d.assign(v.begin(), v.end()); }
  // Section marker: a mismatch on load means the file and this runtime disagree about the layout.
  void tag(const char (&t)[5]) {
    uint32_t want; std::memcpy(&want, t, 4);
    uint32_t got = want; pod(got);
    if (loading && got != want) throw std::runtime_error(std::string("save state section mismatch at ") + t);
  }
};

// 1 = save requested (F1 on the host thread), 2 = load requested (NWII_LOAD_STATE at startup). Serviced by the guest thread.
inline std::atomic<int> request{0};

// Screenshot for the save state's thumbnail: the host thread fills it at its next present after `want` is set.
struct Thumb { std::mutex mutex; bool want = false, ready = false; std::vector<uint8_t> bmp; };
inline Thumb thumb;

}  // namespace native_ss

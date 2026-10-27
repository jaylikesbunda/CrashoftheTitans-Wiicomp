// Harness for the GL render queue (NATIVE_RENDER_QUEUE_V1; tools/fix_render_queue.py, tools/check_render_queue.py).
//
// A generated stream of drains and frame tasks is run two ways:
//   inline   - every item executes on the publishing thread at Publish(); the reference, equal to the synchronous runtime.
//   threaded - items execute on a second thread through OrderedQueue, with randomized sleeps on both sides.
// The front end (producer) follows the runtime's rules: flush before the early signal of a drain that contains an EFB copy
// or that the guest reads, then signal, then submit. Compared per seed: the GL call sequence, the uploaded/copied bytes
// (guest RAM at the end), the values the guest read at each signal, and the signal order. Invariants: in-flight items never
// exceed the capacity, and a signal taken after a flush sees an empty queue.
//
// Negative control: --no-flush disables the flush rule. It must produce at least one mismatch, or the harness cannot
// tell a violation from a pass.
//
// Build/run: g++ -std=c++20 -O2 -pthread -I runtime-overlays tools/render_queue_tests.cpp -o q.exe && q.exe [seeds] [--no-flush]
#include "native_render_queue.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>
#include <random>
#include <string>
#include <thread>
#include <vector>

using nwii::runtime::native_render_queue::OrderedQueue;

namespace {

constexpr int kRamWords = 64;
constexpr size_t kCapacity = 3;

// What a queue item carries. A batch is a drain: optional EFB copy into guest RAM, optional guest read of RAM at its signal.
// A task is a frame: it reads the "XFB" word and logs a present.
struct Item {
    bool task = false;
    int id = -1;
    bool copy = false, read = false;
    int copy_dst = 0, read_addr = 0;
    std::vector<uint32_t> words; // draw payload, folded into the copy value
};

struct Model {
    std::vector<uint64_t> gl_log;          // every GL-side observation, in execution order (GL thread only)
    std::atomic<uint32_t> ram[kRamWords] = {}; // guest RAM: written by the copy, read by the guest's signal
    std::vector<uint32_t> signals;         // values the guest saw at each signal, in signal order (producer only)
    size_t max_in_flight = 0;
    bool flush_violation = false;
    std::vector<uint32_t> RamSnapshot() const {
        std::vector<uint32_t> out(kRamWords);
        for (int i = 0; i < kRamWords; ++i) out[i] = ram[i].load(std::memory_order_relaxed);
        return out;
    }
};

uint32_t Fold(const std::vector<uint32_t>& w, uint32_t seed) {
    uint32_t h = seed;
    for (uint32_t v : w) h = h * 33u + v;
    return h;
}

// GL side. Runs with exclusive use of the model's GL log and RAM writes.
void Execute(Model& m, Item& it) {
    if (it.task) {
        m.gl_log.push_back((uint64_t(3) << 56) | uint64_t(it.id));
        m.gl_log.push_back((uint64_t(4) << 56) | m.ram[0].load(std::memory_order_relaxed));
        return;
    }
    m.gl_log.push_back((uint64_t(1) << 56) | uint64_t(it.id));
    m.gl_log.push_back((uint64_t(2) << 56) | Fold(it.words, uint32_t(it.id)));
    if (it.copy) m.ram[it.copy_dst].store(Fold(it.words, 0x9e37u + uint32_t(it.id)), std::memory_order_relaxed);
}

struct Rng {
    std::mt19937 gen;
    explicit Rng(uint32_t seed) : gen(seed) {}
    int below(int n) { return std::uniform_int_distribution<int>(0, n - 1)(gen); }
    bool chance(int percent) { return below(100) < percent; }
};

std::vector<Item> Generate(uint32_t seed, int count) {
    Rng r(seed);
    std::vector<Item> out;
    for (int i = 0; i < count; ++i) {
        Item it;
        it.id = i;
        if (r.chance(12)) {
            it.task = true;
        } else {
            it.copy = r.chance(25);
            it.read = r.chance(20);
            it.copy_dst = r.below(kRamWords);
            it.read_addr = r.below(kRamWords);
            const int n = 1 + r.below(6);
            for (int k = 0; k < n; ++k) it.words.push_back(uint32_t(r.gen()));
        }
        out.push_back(std::move(it));
    }
    return out;
}

// Producer rules, as ProcessGXFifo applies them: flush before signalling a drain that copies or is read; signal; submit.
void Produce(Model& m, const std::vector<Item>& stream, OrderedQueue<Item>* queue, bool flush_rule, uint32_t seed) {
    Rng jitter(seed ^ 0x5bd1e995u);
    for (const Item& src : stream) {
        if (src.task) {
            if (!queue) {
                Item copy = src;
                Execute(m, copy);
            } else {
                Item& slot = queue->Acquire();
                slot = src;
                queue->Publish();
                m.max_in_flight = std::max(m.max_in_flight, queue->InFlight());
            }
            continue;
        }
        if (flush_rule && (src.copy || src.read) && queue) {
            queue->Flush();
            if (queue->InFlight() != 0) m.flush_violation = true;
        }
        // Signal: the guest reads RAM here when the drain asks for it. Copies of this drain follow the signal, as today.
        m.signals.push_back(src.read ? m.ram[src.read_addr].load(std::memory_order_relaxed) : uint32_t(src.id));
        if (!queue) {
            Item copy = src;
            Execute(m, copy);
        } else {
            if (jitter.chance(50)) std::this_thread::sleep_for(std::chrono::microseconds(jitter.below(120)));
            Item& slot = queue->Acquire();
            slot = src;
            queue->Publish();
            m.max_in_flight = std::max(m.max_in_flight, queue->InFlight());
        }
    }
}

struct Result {
    std::vector<uint64_t> gl_log;
    std::vector<uint32_t> signals, ram;
    size_t max_in_flight = 0;
    bool flush_violation = false;
};

Result RunInline(const std::vector<Item>& stream, bool flush_rule, uint32_t seed) {
    Model m;
    Produce(m, stream, nullptr, flush_rule, seed);
    return {m.gl_log, m.signals, m.RamSnapshot(), 0, m.flush_violation};
}

Result RunThreaded(const std::vector<Item>& stream, bool flush_rule, uint32_t seed) {
    Model m;
    OrderedQueue<Item> queue(kCapacity);
    std::thread gl([&] {
        Rng jitter(seed ^ 0x27d4eb2du);
        queue.Run([&](Item& it) {
            if (jitter.chance(50)) std::this_thread::sleep_for(std::chrono::microseconds(jitter.below(150)));
            Execute(m, it);
        });
    });
    Produce(m, stream, &queue, flush_rule, seed);
    queue.Flush();
    queue.Stop();
    gl.join();
    return {m.gl_log, m.signals, m.RamSnapshot(), m.max_in_flight, m.flush_violation};
}

bool Same(const Result& a, const Result& b, const char* what, uint32_t seed) {
    if (a.gl_log == b.gl_log && a.signals == b.signals && a.ram == b.ram) return true;
    std::printf("  mismatch (%s) seed %u: gl %zu/%zu signals %zu/%zu\n", what, seed, a.gl_log.size(), b.gl_log.size(),
                a.signals.size(), b.signals.size());
    return false;
}

} // namespace

int main(int argc, char** argv) {
    int seeds = 300;
    bool flush_rule = true;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--no-flush") == 0) flush_rule = false;
        else seeds = std::atoi(argv[i]);
    }

    int failures = 0;
    size_t max_in_flight = 0;
    int mismatches = 0;
    int flush_violations = 0;
    for (uint32_t seed = 1; seed <= uint32_t(seeds); ++seed) {
        const std::vector<Item> stream = Generate(seed, 40);
        const Result reference = RunInline(stream, true, seed);
        const Result threaded = RunThreaded(stream, flush_rule, seed);
        // Each seed is also run twice threaded: the schedule differs, the observable result must not.
        const Result threaded_again = RunThreaded(stream, flush_rule, seed + 1000003u);
        if (!Same(reference, threaded, "threaded", seed)) ++mismatches;
        if (!Same(reference, threaded_again, "threaded again", seed)) ++mismatches;
        if (threaded.flush_violation || threaded_again.flush_violation) ++flush_violations;
        max_in_flight = std::max(max_in_flight, threaded.max_in_flight);
        if (threaded.max_in_flight > kCapacity) ++failures;
    }

    std::printf("render queue harness: %d seeds, capacity %zu, flush rule %s\n", seeds, kCapacity, flush_rule ? "on" : "OFF");
    std::printf("  max in flight observed: %zu (bound %zu)\n", max_in_flight, kCapacity);
    std::printf("  inline vs threaded mismatches: %d\n", mismatches);
    std::printf("  signals taken with work still in flight: %d\n", flush_violations);
    if (!flush_rule) {
        // Negative control: the harness must see the violation.
        const bool detected = mismatches > 0 || flush_violations > 0;
        std::printf("%s negative control\n", detected ? "PASS" : "FAIL");
        return detected ? 0 : 1;
    }
    const bool ok = failures == 0 && mismatches == 0 && flush_violations == 0;
    std::printf("%s queue equivalence\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}

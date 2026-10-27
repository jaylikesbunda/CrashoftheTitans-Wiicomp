"""Decode a drain's draws on helper threads (NWII_DECODE_THREADS=1 disables; default min(4, cores/3)).

Capture frame-cost-20261006-121918-356500: the guest emits most of its GX stream late in its frame, so the renderer
lane (33.6 ms CPU/frame) runs after submission and the guest idles 18.8 ms/frame. gx.decode is 12.2 ms/frame of that,
run serially in ProcessGXFifo before rendering. FifoParser::DecodeDraw reads only its command's DrawRaw snapshot and guest
RAM and writes only that command (no g_state access), so draws are independent; they are split with an atomic index
and the order and contents of every decoded command are unchanged. The first helper-less path is the old loop.
"""
from pathlib import Path
p=Path(__file__).resolve().parents[1];f=p/'build/nwii-full-source/nWiiRuntime/src/hle/gx.cpp';s=f.read_text()
marker='NATIVE_PARALLEL_DECODE_V1'
if marker not in s:
    anchor='void ProcessGXFifo() {\n'
    assert s.count(anchor)==1
    pool='''// NATIVE_PARALLEL_DECODE_V1
#include <thread>
namespace {
struct NativeDecodePool {
    std::mutex mutex;
    std::condition_variable wake, finished;
    nwii::runtime::gx::GXCommand** jobs = nullptr;
    size_t count = 0;
    std::atomic<size_t> next{0};
    unsigned generation = 0, outstanding = 0, helpers = 0;
    void work() {
        for (;;) {
            const size_t i = next.fetch_add(1, std::memory_order_relaxed);
            if (i >= count) break;
            nwii::runtime::gx::FifoParser::DecodeDraw(*jobs[i]);
        }
    }
    void loop() {
        if (native_trace::enabled()) native_trace::local_buffer();
        unsigned seen = 0;
        std::unique_lock<std::mutex> lock(mutex);
        for (;;) {
            wake.wait(lock, [&] { return generation != seen; });
            seen = generation;
            lock.unlock();
            work();
            lock.lock();
            if (--outstanding == 0) finished.notify_one();
        }
    }
    void run(nwii::runtime::gx::GXCommand** list, size_t n) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            jobs = list; count = n; next.store(0, std::memory_order_relaxed);
            outstanding = helpers; ++generation;
        }
        wake.notify_all();
        // NATIVE_DECODE_BALANCE_V1: jobs are sorted heavy-first; let a helper
        // claim the head job before the owner takes one, so the owner does not
        // serialise the heaviest draw onto the render lane. Bounded spin.
        static const bool balance = std::getenv("NWII_NO_DECODE_BALANCE") == nullptr;
        if (balance && helpers)
            for (int spin = 0; spin < 200000 && next.load(std::memory_order_relaxed) == 0; ++spin)
                std::this_thread::yield();
        work();
        std::unique_lock<std::mutex> lock(mutex);
        finished.wait(lock, [&] { return outstanding == 0; });
    }
};
NativeDecodePool* NativeGetDecodePool() {
    static NativeDecodePool* pool = [] () -> NativeDecodePool* {
        unsigned total = std::min(4u, std::max(1u, std::thread::hardware_concurrency() / 3));
        if (const char* e = std::getenv("NWII_DECODE_THREADS")) total = std::max(1, std::atoi(e));
        if (total <= 1) return nullptr;
        auto* created = new NativeDecodePool;
        created->helpers = total - 1;
        for (unsigned i = 0; i < created->helpers; ++i) std::thread([created] { created->loop(); }).detach();
        return created;
    }();
    return pool;
}
}
'''
    s=s.replace(anchor,pool+anchor,1)
    old='''    for (auto &c : frame) {
        if (c.type == nwii::runtime::gx::GXCommandType::DrawPrimitive) {
            nwii::runtime::gx::FifoParser::DecodeDraw(c);
            ++g_stat_draws;
        }
    }
'''
    assert s.count(old)==1
    new='''    {
        // NATIVE_PARALLEL_DECODE_V1: independent draws decode concurrently; counts and order are unchanged.
        static thread_local std::vector<nwii::runtime::gx::GXCommand*> draws;
        draws.clear();
        for (auto &c : frame)
            if (c.type == nwii::runtime::gx::GXCommandType::DrawPrimitive) draws.push_back(&c);
        g_stat_draws += draws.size();
        NativeDecodePool* pool = draws.size() >= 8 ? NativeGetDecodePool() : nullptr;
        if (pool) pool->run(draws.data(), draws.size());
        else for (auto* c : draws) nwii::runtime::gx::FifoParser::DecodeDraw(*c);
    }
'''
    s=s.replace(old,new,1);f.write_text(s)
print('Parallel decode installed.')

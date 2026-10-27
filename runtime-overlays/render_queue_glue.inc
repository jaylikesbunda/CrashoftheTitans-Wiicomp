// NATIVE_RENDER_QUEUE_V1: GL thread and batch queue. Included by gx.cpp just before ProcessGXFifo; tools/fix_render_queue.py.
//
// Ownership. The front end (the thread that calls ProcessGXFifo, the main thread) parses, decodes, runs the texture CPU
// prepass and signals the guest. It hands each drain to the GL thread as a batch and each frame's GL work as a task.
// The GL thread is the only thread that calls GL or writes RenderState(), g_renderer, the renderer's BP shadow or the
// texture cache's GL side (gl_ops, slot_names, efb_textures, mip_ready). Everything a batch needs from the front end
// travels with it: the command stream, the parse snapshot, the texture ops and the draw list. Items run in publish
// order, so a frame task sees every batch published before it.
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "runtime/native_render_queue.h"
#include "runtime/native_render_queue_stats.h"
#include "runtime/texture_cache_gl.h"

// Renderer TU (texture_cpu_gl_split_walk.inc): the prepass draw lists, selected per batch.
namespace nwii::runtime::gx {
uint32_t NativeLastPrepassSeq();
void NativeSelectDrawTextures(uint32_t seq);
}

namespace nwii::runtime::render_queue {
struct Item {
    gx::CommandStream stream;                                             // batch: swapped in by SubmitBatch
    std::function<void()> task;                                           // task: runs on the GL thread
    gx::ParseSnapshot snapshot{};                                         // batch: parse-owned view for RenderStateBegin
    std::vector<hle::TextureCache::TextureOp> texture_ops;                // batch: uploads and deletes queued by the prepass
    uint32_t draw_seq = 0;                                                // batch: which prepass draw list it uses
};
using Queue = native_render_queue::OrderedQueue<Item>;

static Queue* s_queue = nullptr;     // created by Start on the main thread, deleted by Stop on the main thread
static std::thread s_thread;
static std::function<void()> s_bind_frame; // GL thread: binds the EFB and its viewport before a batch
static gx::ParseSnapshot s_gl_snapshot{};  // GL thread: the snapshot of the batch being rendered
static std::mutex s_title_mutex;
static std::string s_title;
static bool s_title_pending = false;

bool Active() { return s_queue != nullptr; }

// GL thread. Batches render through the renderer; tasks run their closure.
static void ExecuteItem(Item& item) {
    if (item.task) {
        std::function<void()> task = std::move(item.task);
        item.task = nullptr;
        task();
        return;
    }
    s_bind_frame();
    if (!g_renderer) {
        g_renderer = nwii::runtime::gx::IRenderer::Create();
        if (g_renderer) g_renderer->Initialize(nullptr);
    }
    s_gl_snapshot = item.snapshot;
    native_render_queue_stats::executed.fetch_add(1, std::memory_order_relaxed);
    hle::TextureCache::get().adopt_ops(item.texture_ops);
    gx::NativeSelectDrawTextures(item.draw_seq);
    if (g_renderer) g_renderer->Render(item.stream);
}

// Main thread. The caller has released the context. make_current runs on the new thread, release when it stops.
void Start(std::function<void()> make_current, std::function<void()> release, std::function<void()> bind_frame) {
    if (s_queue) return;
    static const size_t depth = [] {
        const char* e = std::getenv("NWII_RENDER_QUEUE_DEPTH");
        return size_t(std::clamp(e ? std::atoi(e) : 3, 1, 8));
    }();
    s_bind_frame = std::move(bind_frame);
    s_queue = new Queue(depth);
    native_render_queue_stats::enabled.store(true);
    s_thread = std::thread([make_current, release] {
        make_current();
        s_queue->Run(ExecuteItem);
        release();
    });
}

// Front end. Waits until the GL thread has completed everything published so far.
void Flush() {
    if (!s_queue) return;
    if (s_queue->InFlight() == 0) return;
    const auto start = std::chrono::steady_clock::now();
    s_queue->Flush();
    native_render_queue_stats::flush_waits.fetch_add(1, std::memory_order_relaxed);
    native_render_queue_stats::flush_wait_us.fetch_add(uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - start).count()), std::memory_order_relaxed);
}

// Front end: the reason is counted, so F3 shows what kept the GL thread from overlapping the next parse.
void FlushForCopy() {
    if (!s_queue) return;
    native_render_queue_stats::flush_copy.fetch_add(1, std::memory_order_relaxed);
    Flush();
}

void FlushForSavestate() {
    if (!s_queue) return;
    native_render_queue_stats::flush_savestate.fetch_add(1, std::memory_order_relaxed);
    Flush();
}

// Main thread, at shutdown. Completes queued work, stops the GL thread and waits for it.
void Stop() {
    if (!s_queue) return;
    s_queue->Flush();
    s_queue->Stop();
    s_thread.join();
    delete s_queue;
    s_queue = nullptr;
}

// Front end. True when the drain contains an EFB copy (BP 0x52). Its results reach guest RAM, so the guest may only be
// signalled after the batches queued before it have completed.
bool BatchNeedsFlush(const gx::CommandStream& stream) {
    for (const auto& e : stream.entries)
        if (e.kind == (uint8_t)gx::GXCommandType::BPRegister && e.reg == 0x52) return true;
    return false;
}

// Front end. Moves a parsed drain into the next free slot and leaves the slot's old, recycled stream in `frame`.
void SubmitBatch(gx::CommandStream& frame) {
    Item& slot = s_queue->Acquire();
    const size_t before = s_queue->InFlight();
    native_render_queue_stats::batches.fetch_add(1, std::memory_order_relaxed);
    if (before) native_render_queue_stats::drains_queued.fetch_add(1, std::memory_order_relaxed);
    gx::NativeRecycleStream(slot.stream); // the GL thread has finished this slot's previous contents
    slot.stream.clear();
    std::swap(slot.stream, frame);
    slot.snapshot = gx::RenderBatchSnapshot();
    hle::TextureCache::get().take_pending_ops(slot.texture_ops);
    slot.draw_seq = gx::NativeLastPrepassSeq();
    s_queue->Publish();
    const uint64_t depth = before + 1;
    native_render_queue_stats::depth_sum.fetch_add(depth, std::memory_order_relaxed);
    native_render_queue_stats::depth_samples.fetch_add(1, std::memory_order_relaxed);
    if (depth > native_render_queue_stats::depth_max.load(std::memory_order_relaxed))
        native_render_queue_stats::depth_max.store(depth, std::memory_order_relaxed);
}

// Main thread. Frame work (composite, present, HUD, thumbnail, screenshots) that must run on the GL thread after the
// batches published before it.
void Post(std::function<void()> task) {
    Item& slot = s_queue->Acquire();
    native_render_queue_stats::tasks.fetch_add(1, std::memory_order_relaxed);
    gx::NativeRecycleStream(slot.stream);
    slot.stream.clear();
    slot.task = std::move(task);
    s_queue->Publish();
}

// Front end. The BP shadow and one-shot mask as the renderer will hold them at the start of the drain being prepared.
// The early pass (gx.cpp, NativeSignalGpuCompletionEarly) keeps this copy in command order, with the renderer's rule.
void FrontBpShadow(uint32_t* bp, uint32_t& mask) {
    std::memcpy(bp, s_early_bp_shadow, sizeof(s_early_bp_shadow));
    mask = s_early_bp_mask;
}

// GL thread (renderer): the parse snapshot for the batch being rendered. Inactive, it is the batch snapshot as before.
gx::ParseSnapshot& BatchSnapshotForRender() {
    return Active() ? s_gl_snapshot : gx::RenderBatchSnapshot();
}

// GL thread: the window title is applied by the main thread (SDL window calls stay on the window's thread).
void SetTitle(const std::string& title) {
    std::lock_guard<std::mutex> lock(s_title_mutex);
    s_title = title;
    s_title_pending = true;
}

// Main thread.
bool TakeTitle(std::string& out) {
    std::lock_guard<std::mutex> lock(s_title_mutex);
    if (!s_title_pending) return false;
    out = s_title;
    s_title_pending = false;
    return true;
}

} // namespace nwii::runtime::render_queue

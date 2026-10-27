#pragma once
#include <cstddef>
#include <vector>
// NATIVE_GX_PUBLISH_GATE_V1
//
// native_gx_publish() is called from the poll path on every real poll to flush whatever the
// guest thread has staged, and from the FIFO writers when the staging buffer reaches the
// 4096-byte publish boundary. The poll-path call almost always finds the buffer empty, so
// it pays an out-of-line call, a prologue and two vector loads to learn nothing.
//
// The emptiness test only needs the staging buffer, so it moves here and the poll path
// inlines it; the real work (take g_fifo_mutex, append, account pending bytes, maybe wake
// the drainer) stays out of line behind the same name and the same ordering.
namespace nwii::runtime {

// CPU-thread-only staging buffer (NATIVE_GX_PRIVATE_FIFO_V1), defined in gx.cpp.
extern std::vector<uint8_t> g_cpu_fifo;

// Take the FIFO lock, append the staged bytes, account them and maybe wake the drainer.
void native_gx_publish();

// NATIVE_GX_PUBLISH_GATE_V1: what the poll path calls.
inline void native_gx_publish_staged() {
    if (__builtin_expect(!g_cpu_fifo.empty(), 0)) native_gx_publish();
}

} // namespace nwii::runtime

#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include "runtime/gx_command.h"
namespace nwii::runtime::gx {
struct NativeChunkedDraw;
std::shared_ptr<NativeChunkedDraw> NativePrepareDecodeChunks(GXCommand& command);
bool NativeChunksNeedWork(const std::shared_ptr<NativeChunkedDraw>& plan);
void NativeRunDecodeChunk(const std::shared_ptr<NativeChunkedDraw>& plan,size_t first,size_t count);
void NativeFinishDecodeChunks(const std::shared_ptr<NativeChunkedDraw>& plan);
struct NativeDecodeTask {
 GXCommand* command=nullptr;std::shared_ptr<NativeChunkedDraw> chunks;
 size_t first=0,count=0,weight=0;
};
}

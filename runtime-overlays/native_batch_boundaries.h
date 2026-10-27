#pragma once
#include <cstdint>
namespace native_batch_boundaries {
// These registers prepare a later BP command. They change no state consumed by
// a pending draw; the eventual TLUT load/copy/clear retains its own boundary.
inline bool Deferred(uint8_t reg) {
  switch(reg){case 0xfe:case 0x64:case 0x49:case 0x4a:case 0x4b:case 0x4d:case 0x4f:case 0x50:return true;default:return false;}
}
}

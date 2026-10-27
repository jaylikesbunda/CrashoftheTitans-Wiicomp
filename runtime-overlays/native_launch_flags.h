#pragma once
#include <cstdlib>
#include <cstdint>
namespace native_launch_flags {
// NATIVE_SCHED_RECORDER_OPT_IN_V2: same default as g_watch_addr (tools/fix_sched_recorder.py).
inline const uint32_t watch=[] {const char* value=std::getenv("NWII_WATCH");if(value)return uint32_t(std::strtoul(value,nullptr,16));
  const char* rec=std::getenv("NWII_SCHED_RECORDER");
  const bool on=rec?rec[0]!='0':(std::getenv("NWII_RUNTIME_DIAG")&&!std::getenv("NWII_NO_DIAG"));
  return on?0x80597790u:0u;}();
inline const bool wg_watch_possible=watch && ((watch&0x3ffffff8u)-0x0c007ff0u)<0x1020u;
}

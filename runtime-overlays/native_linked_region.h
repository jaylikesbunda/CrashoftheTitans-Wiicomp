#pragma once
#include <cstdlib>
namespace native_linked_region {
inline bool Enabled() {
  static const bool enabled=[] {
    const char* rollback=std::getenv("NWII_NO_LINKED_REGIONS");
    const char* watch=std::getenv("NWII_WATCH");
    return !(rollback && *rollback && *rollback!='0') && !(watch && *watch && *watch!='0');
  }();
  return enabled && !nwii::runtime::g_trace_calls;
}
}

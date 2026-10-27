#pragma once
#include <cstdint>
namespace nwii::runtime { struct CPUContext; }

// Experimental region API. The builder does not install this header or apply
// its source transform by default. Only control fields in CPUContext are live
// during Prepare; architectural GPR/FP/CR/PC/LR/IC values may still be local.
// in_callback, msr, dispatch_saved_ctx, vblank_pending and pending_cb_count must
// remain authoritative in control; unsupported writes to these end a region.
namespace native_region_poll {
enum class Ticket : uint32_t { Complete, Restart, Slow, SlowAfterWpad };
Ticket Prepare(nwii::runtime::CPUContext& control,uint32_t pc,uint64_t count);
// Publish all architectural state before Finish. Complete needs no Finish.
// Restart has consumed no poll effects. Other tickets have consumed the common
// poll prelude and one non-due clock phase, exactly once.
bool Finish(nwii::runtime::CPUContext& context,Ticket ticket);
}

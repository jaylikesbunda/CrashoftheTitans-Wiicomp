#pragma once
#include "runtime/native_perf_census.h"
#include <algorithm>
#include <iomanip>
#include <sstream>
#include <vector>

namespace native_perf_census {
inline const char* names[Count] = {
 "frames", "entry_normal", "entry_original", "entry_continuation",
 "switch_le24", "switch_le64", "switch_le256", "switch_gt256",
 "dispatch_lookups", "dispatch_misses", "backward_poll_checks", "full_polls",
 "quant_u8_pairs", "quant_u16_pairs", "quant_s8_pairs", "quant_s16_pairs",
 "quant_ram_pair_candidates", "quant_fallback_pair_candidates", "gather_span_success_calls", "gather_span_success_bytes",
 "publish_calls", "publish_bytes", "texture_lookups", "texture_misses",
 "packed_buffer_requests", "packed_buffer_reuse_hits", "packed_allocations", "packed_words",
 "multiple_word_requests", "multiple_word_fast", "multiple_word_fast_words",
 "fill_block_requests", "fill_block_fast", "xf_block_requests", "xf_block_cached",
 "slow_callback_polls", "slow_polls_with_delay",
 "texture_format_0", "texture_format_1",
 "texture_format_2", "texture_format_3", "texture_format_4", "texture_format_5",
 "texture_format_6", "texture_format_7", "texture_format_8", "texture_format_9",
 "texture_format_10", "texture_format_11", "texture_format_12", "texture_format_13",
 "texture_format_14", "texture_format_15"
};
struct PcRow { uint32_t pc; uint64_t calls; };
inline std::vector<PcRow> PcRows(PcSlot* slots) {
 std::vector<PcRow> rows;
 if(!pc_histograms)return rows;
 const auto* base=slots==entry_pcs?entry_baseline:switch_baseline;
 for (unsigned i=0;i<kPcSlots;++i) {
  const auto key=slots[i].key.load(std::memory_order_acquire);
  const auto calls=Delta(slots[i].calls.load(std::memory_order_relaxed),base[i]);
  if(key && calls) rows.push_back({uint32_t(key-1),calls});
 }
 std::sort(rows.begin(),rows.end(),[](const PcRow&a,const PcRow&b){return a.calls>b.calls || (a.calls==b.calls && a.pc<b.pc);});
 return rows;
}
inline std::string Json() {
 std::ostringstream s;
 s << "{\"enabled\":" << (enabled?"true":"false")
   << ",\"pc_histograms_enabled\":" << (pc_histograms?"true":"false")
   << ",\"since_process_start\":" << (window_reset?"false":"true")
   << ",\"batched_boundary_counts_are_approximate\":true,\"batch_events\":128,\"counters\":{";
 for(unsigned i=0;i<Count;++i){if(i)s<<',';s<<'"'<<names[i]<<"\":";if(i==PollBranch)s<<"null";else s<<Value(i);}
 s << "},\"entry_histogram_overflow\":" << Delta(entry_overflow.load(),entry_overflow_baseline)
   << ",\"switch_histogram_overflow\":" << Delta(switch_overflow.load(),switch_overflow_baseline);
 for(auto pair : {std::pair<const char*,PcSlot*>{"entry_pcs",entry_pcs},{"switch_pcs",switch_pcs}}){
  s << ",\""<<pair.first<<"\":[";bool comma=false;
  for(auto row:PcRows(pair.second)){if(comma)s<<',';comma=true;s<<"{\"pc\":\"0x"<<std::hex<<std::setw(8)<<std::setfill('0')<<row.pc<<std::dec<<"\",\"calls\":"<<row.calls<<'}';}s<<']';
 }
 return s.str()+'}';
}
inline std::string Text() {
 if(!enabled)return "Performance census: disabled (launch with NWII_PERF_CENSUS=1 to record independently of F3).\n";
 std::ostringstream s;
 s << "Performance census: " << (window_reset?"since F9 reset":"since process start") << "; owned counters batch 128 operations, so window edges are approximate. PC histograms and frame count are unbatched.\n";
 s << "PC histograms: " << (pc_histograms?"enabled (direct PC lookup)":"disabled; enable NWII_PERF_PC_HISTOGRAM for detailed attribution") << ".\n";
 const auto frames=Value(Frames);
 for(unsigned i=0;i<Count;++i){if(i==PollBranch){s<<"  backward_poll_checks: not measured (avoid an extra check at every backward branch)\n";continue;}const auto n=Value(i);s<<"  "<<names[i]<<": "<<n;if(frames && i!=Frames)s<<" ("<<std::fixed<<std::setprecision(2)<<double(n)/frames<<"/XFB)";s<<'\n';}
 s<<"  PC histogram overflow entry/switch: "<<Delta(entry_overflow.load(),entry_overflow_baseline)<<'/'<<Delta(switch_overflow.load(),switch_overflow_baseline)<<'\n';
 for(auto pair : {std::pair<const char*,PcSlot*>{"routed entry PCs",entry_pcs},{"switch entry PCs",switch_pcs}}){
  s<<"  Top "<<pair.first<<":\n";auto rows=PcRows(pair.second);
  for(size_t i=0;i<std::min<size_t>(16,rows.size());++i)s<<"    0x"<<std::hex<<std::setw(8)<<std::setfill('0')<<rows[i].pc<<std::dec<<": "<<rows[i].calls<<'\n';
 }
 return s.str();
}
}

"""Offline region poll-contract prototype; no production integration.

Preserve every poll, credit update, clock phase and WPAD sampling point. Only a
non-due, empty, non-diagnostic path may complete with architectural state local.
Source changes are bound to the inspected 2026.10.17-2 runtime shape. The new
API is not emitted into ordinary translated functions or enabled by a builder.
"""
from pathlib import Path
import argparse
import hashlib
import json

PORT=Path(__file__).resolve().parents[1]
MARKER='NATIVE_REGION_POLL_EXPERIMENT_V1'
QUALIFIED_SOURCE_SHA256='e58a6c4918724de14763d857812fdb951dda1b6f96a4dc27d1bedfef8a1282df'

IMPLEMENTATION=r'''
// NATIVE_REGION_POLL_EXPERIMENT_V1
namespace native_region_poll {
Ticket Prepare(nwii::runtime::CPUContext& control,uint32_t pc,uint64_t count){
  // These checks have no side effects. Restart delegates the complete original
  // poll, including its phase advance. Never consume a phase speculatively and
  // then run the prelude twice.
  if(native_poll_launch_poll_diagnostics || g_fp_context_probe_enabled ||
     native_trace::active.load(std::memory_order_relaxed) ||
     *g_native_gx_staged_size || native_poll_launch_no_throttle ||
     ((native_region_clock_phase+1u)&15u)==0 ||
     pc==0 || pc==0xFFFFFFFCu || pc==0x803BBFA0u ||
     !native_poll_not_periodic(count) || g_trace_after_dec ||
     g_ipc_interrupt_delay!=0 || hw::g_di_interrupt_delay!=0)
    return Ticket::Restart;
  const bool skip_wpad=control.in_callback || !(control.msr&0x8000u) || control.dispatch_saved_ctx!=0;
  if(!skip_wpad && (native_region_wpad_no_throttle ||
                   ((native_region_wpad_phase+1u)&127u)==0))return Ticket::Restart;

  native_region_poll_begin();
  ++native_region_clock_phase; // due=false, established without side effects

  // Read producer flags after the original prelude/phase position. A newly
  // arriving event must take the already-prepared slow path, not restart.
  if(g_trace_after_dec || g_ipc_interrupt_delay!=0 || hw::g_di_interrupt_delay!=0 ||
     control.vblank_pending || (hw::pi_intsr.load() & hw::pi_intmr.load()) ||
     control.pending_cb_count.load(std::memory_order_relaxed)!=0)
    return Ticket::Slow;
  if(skip_wpad)return Ticket::Complete;
  ++native_region_wpad_phase; // original non-due native_wpad_poll returns here
  if(control.pending_cb_count.load(std::memory_order_relaxed)!=0)return Ticket::SlowAfterWpad;
  return Ticket::Complete;
}
bool Finish(nwii::runtime::CPUContext& context,Ticket ticket){
  switch(ticket){
    case Ticket::Complete:return false;
    case Ticket::Restart:return process_pending_callbacks(context);
    case Ticket::Slow:return native_process_callbacks_slow(context,false,false);
    case Ticket::SlowAfterWpad:return native_process_callbacks_slow(context,false,true);
  }
  std::abort();
}
}
'''


def edit(source,old,new):
    if source.count(old)!=1:raise ValueError('Runtime source drift: '+old[:100])
    return source.replace(old,new,1)


def transform(source):
    if MARKER in source:raise ValueError('Already transformed')
    if hashlib.sha256(source.encode()).hexdigest()!=QUALIFIED_SOURCE_SHA256:
        raise ValueError('Requalify changed IOS runtime before changing its observation contract')
    if 'NATIVE_POLL_PERIODIC_FACTOR_V1' not in source or 'NATIVE_EVENT_MASK_V1: always_inline' in source:
        raise ValueError('Expected the qualified factored-periodic, non-event-mask runtime')
    source=edit(source,
        '  { static const bool no_thr=std::getenv("NWII_NO_POLL_THROTTLE")!=nullptr; static uint32_t wpad_phase=0; if (!no_thr && (++wpad_phase & 127u)!=0) return; }',
        '  if (!native_region_wpad_no_throttle && (++native_region_wpad_phase & 127u)!=0) return;')
    source=edit(source,'static __attribute__((always_inline)) inline void native_wpad_poll(CPUContext& ctx) {',
        'static const bool native_region_wpad_no_throttle=std::getenv("NWII_NO_POLL_THROTTLE")!=nullptr;\n'
        'static uint32_t native_region_wpad_phase=0;\n'
        'static __attribute__((always_inline)) inline void native_wpad_poll(CPUContext& ctx) {')
    source=edit(source,'  static uint32_t poll_clock_phase=0; // NATIVE_POLL_CLOCK_THROTTLE_V1','  // Clock phase is shared with the experimental prepare/finish API.')
    source=edit(source,'((++poll_clock_phase)&15u)==0','((++native_region_clock_phase)&15u)==0')
    source=edit(source,'static inline bool native_process_callbacks_poll(CPUContext&);',
        'static uint32_t native_region_clock_phase=0;\nstatic inline bool native_process_callbacks_poll(CPUContext&);')
    start=source.index('bool process_pending_callbacks(CPUContext &ctx) {')
    probe=source.index('  native_fp_context_probe(ctx,"poll");',start)
    begin=start+len('bool process_pending_callbacks(CPUContext &ctx) {\n')
    prelude=source[begin:probe]
    if 'PollFull' not in prelude or 'g_native_poll_credit=' not in prelude or 'ctx.' in prelude:
        raise ValueError('Unqualified common poll prelude')
    source=source[:start]+'static inline void native_region_poll_begin(){\n'+prelude+'}\n'+source[start:begin]+'  native_region_poll_begin();\n'+source[probe:]
    # The implementation must share the original functions' lexical scope. This
    # inspected runtime defines process_pending_callbacks in the global scope.
    source='#include "native_region_poll.h"\n'+source+'\n'+IMPLEMENTATION
    return source


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--work',type=Path,required=True)
    args=ap.parse_args();work=args.work.resolve()
    if work==PORT or PORT in work.parents:raise ValueError('Experiment output must be outside repo')
    work.mkdir(parents=True,exist_ok=True)
    source=args.source.read_text();result=transform(source)
    (work/'ios.cpp').write_text(result)
    header=PORT/'runtime-overlays/native_region_poll.h'
    (work/header.name).write_bytes(header.read_bytes())
    metadata=dict(source=str(args.source.resolve()),source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        transformed_sha256=hashlib.sha256(result.encode()).hexdigest(),production_enabled=False,
        contract='No architectural state reads/writes in Prepare; original slow paths require publication')
    (work/'poll-contract.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps(metadata,indent=2))


if __name__=='__main__':main()

"""Opt-in production work counters; no guest-state or scheduling changes."""
from pathlib import Path
import re
import argparse

INCLUDE = '#include "runtime/native_perf_census.h"\n'
MARKER = 'NATIVE_PERF_CENSUS_V1'

def edit(source, old, new):
    if new in source:
        return source
    assert source.count(old) == 1, (old, source.count(old))
    return source.replace(old, new, 1)

def include(source):
    return source if INCLUDE in source else INCLUDE + source

def patch_ios(source):
    # Upstream stages can insert the FP probe before an existing census hook.
    # Canonicalize our hook before reinserting it, so replay never doubles it.
    hook = f'  native_perf_census::GuestAdd(native_perf_census::PollFull); /* {MARKER} */\n'
    source = source.replace(hook, '')
    anchor = 'bool process_pending_callbacks(CPUContext &ctx) {'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + '\n' + hook.rstrip('\n'), 1)
    slow_hook = f'  native_perf_census::GuestAdd(native_perf_census::SlowPoll); /* {MARKER} */\n'
    old_delay_hook = (f'  if (g_ipc_interrupt_delay != 0 || hw::g_di_interrupt_delay != 0)\n'
                  f'    native_perf_census::GuestAdd(native_perf_census::DelayPoll); /* {MARKER} */\n')
    delay_hook = (f'  if (native_perf_census::Enabled() && (g_ipc_interrupt_delay != 0 || hw::g_di_interrupt_delay != 0))\n'
                  f'    native_perf_census::GuestAdd(native_perf_census::DelayPoll); /* {MARKER} */\n')
    source = source.replace(slow_hook, '').replace(old_delay_hook, '').replace(delay_hook, '')
    slow_anchor = 'static bool native_process_callbacks_slow(CPUContext &ctx, bool poll_clock_due, bool wpad_polled) {'
    assert source.count(slow_anchor) == 1
    source = source.replace(slow_anchor, slow_anchor + '\n' + (slow_hook + delay_hook).rstrip('\n'), 1)
    return include(source)

def switches(source):
    # Entry selectors contain only case/default goto or return clauses. Avoid
    # instrumenting any more complex switch rather than guessing its scope.
    edits=[]
    for match in re.finditer(r'switch\s*\(ctx\.pc\)\s*\{', source):
        if 'native_perf_census::GuestSwitch' in source[max(0,match.start()-150):match.start()]:
            continue
        end=source.index('}',match.end())
        body=source[match.end():end]
        if '{' in body:
            continue
        count=len(re.findall(r'\bcase\s+0x[0-9A-Fa-f]+\s*:',body))
        if not count:
            continue
        edits.append((match.start(),f'native_perf_census::GuestSwitch(ctx.pc,{count}); /* {MARKER} */\n        '))
    for pos,text in reversed(edits):
        source=source[:pos]+text+source[pos:]
    return source, len(edits)

def patch_cpp(source, main=False):
    if main:
        source=edit(source,'    if(entry.pc==target)return {target,entry.func,entry.hle};',
            f'    native_perf_census::GuestAdd(native_perf_census::DispatchLookup); /* {MARKER} */\n'
            '    if(entry.pc==target)return {target,entry.func,entry.hle};\n'
            '    native_perf_census::GuestAdd(native_perf_census::DispatchMiss);')
    for old,kind in [('native_guest_entry::Record(true);',0),
                     ('native_guest_entry::Record(false);',1),
                     ('native_guest_entry::RecordContinuation();',2)]:
        new=f'native_perf_census::GuestEntry(ctx.pc,{kind});'+old
        source=source.replace(new,old).replace(old,new)
    source,n=switches(source)
    if 'native_perf_census::' in source:
        source=include(source)
    return source,n

def restore_cpp(source):
    # Hot splitting and promotion consume canonical generated code. Remove
    # late diagnostic hooks before those upstream transforms run again.
    source=re.sub(r'native_perf_census::GuestEntry\(ctx\.pc,[012]\);','',source)
    source=re.sub(r'native_perf_census::GuestSwitch\(ctx\.pc,\d+\); /\* NATIVE_PERF_CENSUS_V1 \*/\n        ','',source)
    source=source.replace('    native_perf_census::GuestAdd(native_perf_census::DispatchLookup); /* NATIVE_PERF_CENSUS_V1 */\n','')
    source=source.replace('    native_perf_census::GuestAdd(native_perf_census::DispatchMiss);\n','')
    return source

def restore(port):
    changed=0
    for tree in ('crash-generated','crash-hot','crash-split'):
        for path in sorted((port/'build'/tree).glob('*.cpp')):
            source=path.read_text(errors='surrogateescape');new=restore_cpp(source)
            if new!=source:path.write_text(new,errors='surrogateescape');changed+=1
    print(f'Late census hooks restored to canonical input in {changed} translation units.')

def apply(port):
    rt=port/'build/nwii-full-source/nWiiRuntime'
    for name in ('native_perf_census.h','native_perf_census_export.h'):
        target=rt/'include/runtime'/name
        value=(port/'runtime-overlays'/name).read_text()
        if not target.exists() or target.read_text()!=value:
            target.write_text(value)
    def change(rel,fn):
        path=rt/rel
        old=path.read_text();new=fn(old)
        if new!=old:path.write_text(new)
    change('src/hle/ios.cpp',patch_ios)
    def gx(s):
        s=edit(s,'    native_trace::Scope trace_publish("gx.publish");',
            '    native_trace::Scope trace_publish("gx.publish");\n'
            f'    native_perf_census::GuestAdd(native_perf_census::PublishCalls); /* {MARKER} */\n'
            '    native_perf_census::GuestAdd(native_perf_census::PublishBytes,g_cpu_fifo.size());')
        # These are successful span redirects, not all FIFO writes or all
        # WPAR byte-fallback writes. Export names state the measured subset.
        s=edit(s,'    const uint64_t be=__builtin_bswap64(value<<(8*(8-bytes)));',
            f'    native_perf_census::GuestAdd(native_perf_census::GatherCalls); /* {MARKER} */\n'
            '    native_perf_census::GuestAdd(native_perf_census::GatherBytes,bytes);\n'
            '    const uint64_t be=__builtin_bswap64(value<<(8*(8-bytes)));')
        return include(s)
    change('src/hle/gx.cpp',gx)
    def texture(s):
        s=edit(s,'    native_hud::work_texture(false);',
            '    native_hud::work_texture(false);\n'
            f'    native_perf_census::RenderAdd(native_perf_census::TextureLookup); /* {MARKER} */\n'
            '    native_perf_census::RenderAdd(static_cast<native_perf_census::Counter>(native_perf_census::Format0+(stage.format&15u)));')
        s=edit(s,'    native_hud::work_texture(true);',
            '    native_hud::work_texture(true);\n'
            f'    native_perf_census::RenderAdd(native_perf_census::TextureMiss); /* {MARKER} */')
        return include(s)
    change('src/hle/texture_cache_gl.cpp',texture)
    def quant(s):
        anchor='    if (st_type >= 4) {'
        injected='''    if (st_type >= 4) {
      // NATIVE_PERF_CENSUS_QUANT_V1: count only integer two-element stores.
      if (!W && native_perf_census::Enabled()) {
        native_perf_census::GuestAdd(static_cast<native_perf_census::Counter>(native_perf_census::QuantU8+st_type-4));
        const uint32_t cp=addr&0x3fffffffu;
        const unsigned cb=(st_type==4 || st_type==6)?2:4;
        const bool ram=mmu.page_bias[cp>>16]>1 && (cp&65535u)<=65536u-cb && !native_watch_span_may_hit(cp,cb);
        native_perf_census::GuestAdd(ram?native_perf_census::QuantRam:native_perf_census::QuantFallback);
      }'''
        return include(edit(s,anchor,injected))
    change('include/runtime/cpu_context.h',quant)
    def packed(s):
        s=edit(s,
        'inline float* NativePackedNewWords(size_t count){return new float[count];}',
        'inline float* NativePackedNewWords(size_t count){\n'
        f' native_perf_census::SharedAdd(native_perf_census::PackedAlloc); /* {MARKER} */\n'
        ' native_perf_census::SharedAdd(native_perf_census::PackedWords,count);\n'
        ' return new float[count];}')
        s=edit(s,' if(!data)data=NativePackedNewWords(count);',
            ' if(!data)data=NativePackedNewWords(count);\n'
            f' else native_perf_census::SharedAdd(native_perf_census::PackedReuse); /* {MARKER} */')
        s=edit(s,'__attribute__((always_inline)) inline std::shared_ptr<float[]> NativePackedAllocateWords(size_t count){',
            '__attribute__((always_inline)) inline std::shared_ptr<float[]> NativePackedAllocateWords(size_t count){\n'
            f' native_perf_census::SharedAdd(native_perf_census::PackedRequests); /* {MARKER} */')
        return include(s)
    change('include/runtime/native_packed_buffer.h',packed)
    total=0
    for tree in ('crash-generated','crash-hot','crash-split'):
        for path in sorted((port/'build'/tree).glob('*.cpp')):
            source=path.read_text(errors='surrogateescape')
            new,n=patch_cpp(source,path.name=='main_output.cpp')
            total+=n
            if new!=source:path.write_text(new,errors='surrogateescape')
    print(f'Opt-in work census staged; {total} simple entry switches instrumented this pass.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--restore',action='store_true')
    args=parser.parse_args();port=Path(__file__).resolve().parents[1]
    (restore if args.restore else apply)(port)

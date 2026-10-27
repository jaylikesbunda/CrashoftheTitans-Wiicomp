"""Reproducible V12/V13 guest ABI, fixed RAM placement and bounded handoff spins.

Applied after all source generators. --restore removes these edits before a
staging replay. Unsupported compilers retain their ordinary function ABI.
"""
from pathlib import Path
import argparse, base64, os, re
from fix_protected_fastmem import hot_source, restore as restore_fastmem
import v13_codegen
from v13_headers import cpu_source
from guest_write_sets import analyse,load_bounds

MARK='NATIVE_V13_RUNTIME_V1'
INCLUDE='#include "runtime/native_guest_abi.h" // '+MARK+'\n'
HANDOFF='#include "runtime/native_handoff.h" // '+MARK+'\n'
GUEST_FAST='#include "runtime/native_guest_fast.h" // '+MARK+'\n'
FAST_DEFINE='#define NWII_V13_INLINE_INDIRECT 1 // '+MARK+'\n'
LAUNCH_INCLUDE='#include "runtime/native_launch_flags.h" // NATIVE_V13_HEADERS_V1\n'
PTR=re.compile(r'void\s*\(\*\s*(\w*)\)\(CPUContext\s*&\)')
PTR_BACK=re.compile(r'/\* '+MARK+r' PTR ([A-Za-z0-9+/=]+) \*/NativeGuestFnPtr(?: \w+)?')
DECL=re.compile(r'^(void func_[0-9A-F]{8}\w*\((?:NWII_CTX_PARAM|CPUContext&|nwii::runtime::CPUContext&) ctx\))',re.M)
STATIC=re.compile(r'^(static )(__attribute__\(\([^\n]*?\)\) void native_guest_(?:primary|resume|continuation)_[0-9A-F]{8}\w*\()',re.M)
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n(?:(?!/\* '+MARK+r' [A-Za-z0-9+/=]+ \*/\n).)*?/\* END_'+MARK+r' \*/',re.S)
POLL='if (nwii::runtime::native_poll_due()) process_pending_callbacks(ctx); /* '+MARK+' INDIRECT_CREDIT */'
INDIRECT_EXIT=re.compile(r'(nwii::runtime::native_call_indirect_cached\(ctx, \d+\); if \(ctx\.pc != 0x[0-9A-F]+\) return;) process_pending_callbacks\(ctx\);')

def indirect_polls(text,main=False):
    """Share the existing loop-poll credit; attention and delay credit=1 still poll immediately."""
    if main and 'void native_call_indirect_cached(' in text:
        old='    if (!ctx.is_running || ctx.pc == 0) return;\n    process_pending_callbacks(ctx);'
        start=text.index('void native_call_indirect_cached(')
        end=text.index('\n}\n}',start)+2
        body=text[start:end]
        assert body.count(old)==1,'Indirect callback entry anchor changed'
        return text[:start]+body.replace(old,old.replace('process_pending_callbacks(ctx);',POLL))+text[end:]
    return INDIRECT_EXIT.sub(lambda m:m[1]+' '+POLL,text)

def restore(text):
    while BLOCK.search(text):text=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),text)
    text=v13_codegen.restore(text)
    if GUEST_FAST in text:text=text.replace('nwii::runtime::native_call_indirect_fast(', 'nwii::runtime::native_call_indirect_cached(')
    text=text.replace(POLL,'process_pending_callbacks(ctx);')
    text=restore_fastmem(text)
    text=PTR_BACK.sub(lambda m:base64.b64decode(m[1]).decode(),text)
    return text.replace(INCLUDE,'').replace(HANDOFF,'').replace(GUEST_FAST,'').replace(FAST_DEFINE,'').replace(LAUNCH_INCLUDE,'').replace('NWII_GUEST_ABI ','')

def guest_source(text,main=False):
    text=restore(text)
    text=DECL.sub(r'NWII_GUEST_ABI \1',text)
    text=STATIC.sub(r'\1NWII_GUEST_ABI \2',text)
    if main:
        text=PTR.sub(lambda m:'/* '+MARK+' PTR '+base64.b64encode(m[0].encode()).decode()+' */NativeGuestFnPtr'+(' '+m[1] if m[1] else ''),text)
    return text

def replace(text,old,new):
    assert text.count(old)==1,('V13 anchor moved',old[:80],text.count(old))
    return text.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */',1)

def runtime_source(text,host):
    text=restore(text)
    if host:
        old='      std::unique_lock<std::mutex> drain_lock(nwii::runtime::g_gx_drain_mutex);'
        new='''      { // Bounded spin before taking the drain mutex; the existing park/recheck handles late publications.
        static const unsigned spin_us=native_handoff::budget("NWII_HANDOFF_SPIN_US",250);
        native_trace::Scope scope("host.handoff_spin");
        if(native_handoff::spin(field_deadline,spin_us,[] {
            return nwii::runtime::g_gx_drain_request.load(std::memory_order_acquire);
        })) continue;
      }
'''+old
    else:
        old='        native_idle::wait(ticks, ctx.tb_freq, pending);'
        new='''        if(native_poll_launch_idle_drain) {
          if(*g_native_gx_staged_size)native_gx_publish();
          if(g_gx_pending_bytes.load(std::memory_order_relaxed))NativeRequestGxDrain();
        }
        static const unsigned spin_us=native_handoff::budget("NWII_GUEST_SPIN_US",60);
        const auto deadline=native_handoff::Clock::now()+std::chrono::nanoseconds(ticks*1000000000ull/ctx.tb_freq);
        if(!native_handoff::spin(deadline,spin_us,pending) && !pending()) {
          // Time spent spinning consumes the original idle budget, rather than extending it.
          const uint64_t remaining=native_idle::budget_ticks(ctx.read_timebase(),EventScheduler::get().next_due(),
              ctx.dec_irq_pending,ctx.dec_written_tb,ctx.dec_value,ctx.tb_freq);
          native_idle::wait(remaining,ctx.tb_freq,pending);
        }'''
    return HANDOFF+replace(text,old,new)

def files(port):
    result=[]
    for name in ('crash-generated','crash-hot','crash-split'):
        result += list((port/'build'/name).glob('*.cpp'))
        result += list((port/'build'/name).glob('*.h'))
    return sorted(result)

def apply(port,undo=False):
    rt=port/'build/nwii-full-source/nWiiRuntime'
    # The optional IR slice has already been compiled using the ordinary ABI and
    # dynamic MMU layout. Keep both for that backend; its handoff can still spin.
    cpp_backend=os.environ.get('NWII_BUILD_IR_BACKEND','cpp')=='cpp'
    guest_abi=cpp_backend and os.environ.get('NWII_BUILD_V13_ABI','1')!='0'
    fastmem=(not undo and cpp_backend and os.name=='nt' and os.environ.get('NWII_BUILD_V13_STATIC_RAM','1')!='0'
             and os.environ.get('NWII_BUILD_V13_FASTMEM','1')!='0')
    indirect_credit=not undo and cpp_backend and os.environ.get('NWII_BUILD_V13_INDIRECT_POLL','1')!='0'
    batch=not undo and cpp_backend and os.environ.get('NWII_BUILD_V13_CODEGEN','1')!='0'
    indirect_fast=batch and indirect_credit and guest_abi and os.environ.get('NWII_BUILD_V13_INDIRECT_FAST','1')!='0'
    main_path=port/'build/crash-generated/main_output.cpp'
    main_original=restore(main_path.read_text(encoding='utf-8',errors='surrogateescape')) if main_path.exists() else ''
    bounds=load_bounds(main_original) if batch else []
    summaries={};starts={a for a,b in bounds};overrides=set();wpad=None
    if bounds:
        from port_paths import default_dol
        fallback=port.parent/'orig/main.dol'
        dol=Path(os.environ.get('NWII_INPUT_DOL') or os.environ.get('NWII_DOL') or (str(fallback) if fallback.exists() else str(default_dol())))
        _,summaries,_,_,_,_=analyse(dol,main_original)
        overrides={int(a,16) for a in re.findall(r'\{\s*0x([0-9A-F]{8}),\s*[\w:]+,\s*(?:true|false)\s*\}',main_original)}
        match=re.search(r'target-0x([0-9A-F]+)u<=0x([0-9A-F]+)u-0x[0-9A-F]+u',main_original)
        assert match,'Region-specific WPAD dispatch range missing'
        wpad=(int(match[1],16),int(match[2],16))
    totals=dict(reloads=0,fp_guards=0,tails=0,nested=0,stack=0)
    edits=[]
    for path in files(port):
        old=path.read_text(encoding='utf-8',errors='surrogateescape')
        new=restore(old) if undo or not guest_abi else guest_source(old,path.name=='main_output.cpp')
        if indirect_credit and path.suffix=='.cpp':
            new=indirect_polls(new,path.name=='main_output.cpp')
        if batch and path.suffix=='.cpp':
            if path.name=='main_output.cpp' and bounds:new=v13_codegen.dispatch_source(new,indirect_fast,POLL,replace)
            elif path.name!='main_output.cpp':
                new,stats=v13_codegen.transform(new,summaries,starts,overrides,wpad,path.parent.name=='crash-hot',fastmem)
                for key,value in stats.items():totals[key]+=value
                if indirect_fast:new=new.replace('nwii::runtime::native_call_indirect_cached(', 'nwii::runtime::native_call_indirect_fast(')
            new=(FAST_DEFINE if indirect_fast else '')+GUEST_FAST+new
        if fastmem and path.suffix=='.cpp' and path.name!='main_output.cpp':new=hot_source(new)
        edits.append((path,old,new))
    for relative,host in [('src/core/main.cpp',True),('src/hle/ios.cpp',False)]:
        path=rt/relative;old=path.read_text(encoding='utf-8')
        edits.append((path,old,restore(old) if undo else runtime_source(old,host)))
    path=rt/'include/runtime/cpu_context.h';old=path.read_text(encoding='utf-8')
    header=restore(old)
    if batch:header=cpu_source(header,replace)
    edits.append((path,old,header if undo else INCLUDE+header))
    cm=rt.parent/'CMakeLists.txt';old=cm.read_text(encoding='utf-8');new=restore(old)
    if not undo:
        # CMake comments, not C++ restoration blocks.
        new=re.sub(r'\n# '+MARK+r' RAM\n.*?# END_'+MARK+r' RAM\n','',new,flags=re.S)
        if cpp_backend and os.environ.get('NWII_BUILD_V13_STATIC_RAM','1')!='0':
            new+='\n# '+MARK+' RAM\nif(MINGW AND CMAKE_SIZEOF_VOID_P EQUAL 8)\n  target_compile_definitions(nwiiruntime PUBLIC NWII_STATIC_RAM_BASE)\nendif()\n# END_'+MARK+' RAM\n'
    else:new=re.sub(r'\n# '+MARK+r' RAM\n.*?# END_'+MARK+r' RAM\n','',new,flags=re.S)
    edits.append((cm,old,new))
    if not undo:
        for name in ('native_guest_abi.h','native_handoff.h','native_guest_fast.h','native_launch_flags.h'):
            target=rt/'include/runtime'/name;data=(port/'runtime-overlays'/name).read_bytes()
            if not target.exists() or target.read_bytes()!=data:target.write_bytes(data)
    for name in ('native_fastmem.h','native_fastmem_allocator.h'):
        target=rt/'include/runtime'/name
        if fastmem:
            data=(port/'runtime-overlays'/name).read_bytes()
            if not target.exists() or target.read_bytes()!=data:target.write_bytes(data)
        elif target.exists():target.unlink()
    changed=0
    for path,old,new in edits:
        if old!=new:path.write_text(new,encoding='utf-8',errors='surrogateescape');changed+=1
    print(('V13 runtime restored' if undo else 'V13 ABI, static RAM and bounded handoff staged')+f': {changed} files')
    if batch:print('V13 codegen:',totals)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--restore',action='store_true');args=parser.parse_args()
    apply(args.port,args.restore)

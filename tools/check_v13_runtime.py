"""Check ABI consistency, reversible staging and bounded handoff behavior without a game."""
from pathlib import Path
import os, shutil, subprocess, tempfile
from fix_v13_runtime import apply, guest_source, restore, runtime_source
from port_paths import winlibs_bin
PORT=Path(__file__).resolve().parents[1]

FIXTURE=r'''
#include "runtime/native_guest_abi.h"
#include "runtime/native_handoff.h"
#include <cassert>
#include <type_traits>
namespace nwii::runtime { struct CPUContext { unsigned value=0; }; }
using nwii::runtime::CPUContext;
NWII_GUEST_ABI void translated(CPUContext& ctx) {ctx.value+=7;}
static NWII_GUEST_ABI __attribute__((noinline)) void resume(CPUContext& ctx) {translated(ctx);}
int main() {
    static_assert(std::is_same_v<decltype(&translated),NativeGuestFnPtr>);
    CPUContext ctx;
    NativeGuestFnPtr table[]={translated,resume};
    translated(ctx);for(auto call:table)call(ctx);assert(ctx.value==21);
    unsigned calls=0;
    auto ready=[&]{++calls;return true;};
    assert(!native_handoff::spin(native_handoff::Clock::now()+std::chrono::seconds(1),0,ready));assert(calls==0);
    assert(native_handoff::spin(native_handoff::Clock::now()+std::chrono::seconds(1),250,ready));assert(calls==1);
    assert(!native_handoff::spin(native_handoff::Clock::now(),250,[]{return false;}));
    return 0;
}
'''

def main():
    original='''void func_12345678(nwii::runtime::CPUContext& ctx);
void func_12345678(NWII_CTX_PARAM ctx) {}
static __attribute__((noinline,cold,optimize("O2"))) void native_guest_resume_12345678(NWII_CTX_PARAM ctx) {}
struct Bound { void(*func)(CPUContext&); };
static_assert(sizeof(void(*)(CPUContext&))==8);
'''
    changed=guest_source(original,True)
    assert changed.count('NWII_GUEST_ABI')==3 and changed.count('NativeGuestFnPtr')==2
    assert guest_source(changed,True)==changed and restore(changed)==original
    with tempfile.TemporaryDirectory(prefix='v13-check-') as directory:
        root=Path(directory);rt=root/'build/nwii-full-source/nWiiRuntime'
        for name in ('src/core','src/hle','include/runtime'):(rt/name).mkdir(parents=True)
        for name in ('crash-generated','crash-hot','crash-split'):(root/'build'/name).mkdir()
        (root/'runtime-overlays').mkdir()
        for name in ('native_guest_abi.h','native_handoff.h','native_fastmem.h','native_fastmem_allocator.h','native_guest_fast.h','native_launch_flags.h'):
            (root/'runtime-overlays'/name).write_bytes((PORT/'runtime-overlays'/name).read_bytes())
        host='void test() {\n      std::unique_lock<std::mutex> drain_lock(nwii::runtime::g_gx_drain_mutex);\n}\n'
        guest='void test() {\n        native_idle::wait(ticks, ctx.tb_freq, pending);\n}\n'
        paths={rt/'src/core/main.cpp':host,rt/'src/hle/ios.cpp':guest,rt/'include/runtime/cpu_context.h':'#pragma once\n',rt.parent/'CMakeLists.txt':'project(test)\n',root/'build/crash-generated/main_output.cpp':original}
        for path,text in paths.items():path.write_text(text,encoding='utf-8')
        apply(root);first={p:p.read_bytes() for p in paths};apply(root)
        assert all(p.read_bytes()==text for p,text in first.items())
        previous=os.environ.get('NWII_BUILD_IR_BACKEND')
        try:
            os.environ['NWII_BUILD_IR_BACKEND']='hybrid';apply(root)
            assert (root/'build/crash-generated/main_output.cpp').read_text(encoding='utf-8')==original
            assert 'NWII_STATIC_RAM_BASE' not in (rt.parent/'CMakeLists.txt').read_text(encoding='utf-8')
        finally:
            if previous is None:os.environ.pop('NWII_BUILD_IR_BACKEND',None)
            else:os.environ['NWII_BUILD_IR_BACKEND']=previous
        apply(root,True);assert all(p.read_text(encoding='utf-8')==text for p,text in paths.items())
        # Compile the same attributes and helper that ship in the real runtime.
        code=root/'check.cpp';code.write_text(FIXTURE,encoding='utf-8');exe=root/'check.exe'
        compiler=shutil.which('g++') or str(winlibs_bin()/'g++.exe')
        subprocess.run([compiler,'-std=c++20','-O2','-Wall','-Wextra','-I',str(rt/'include'),str(code),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print('V13 runtime checks passed: guest ABI calls, reversible/idempotent staging and bounded handoff.')

if __name__=='__main__':main()

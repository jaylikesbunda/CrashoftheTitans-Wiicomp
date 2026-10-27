"""Compare protected loads with the production MMU, including faulting reads."""
from pathlib import Path
import argparse, ast, os, shutil, subprocess, tempfile
from fix_protected_fastmem import hot_source, restore
from port_paths import winlibs_bin
PORT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
#include "runtime/native_fastmem.h"
#include "runtime/native_guest_fast.h"
#include <cassert>
#include <memory>
#include <random>
#include <cstdio>
#include <windows.h>
namespace nwii::runtime { MMU* g_mmu=nullptr; }
extern "C" void run_game(nwii::runtime::CPUContext&) {}
using namespace nwii::runtime;
int main() {
  auto context=std::make_unique<CPUContext>();MMU& m=context->mmu;g_mmu=&m;
  std::mt19937 rng(20261018);
  for(size_t n=0;n<m.mem1.size();++n)m.mem1[n]=uint8_t(rng());
  for(size_t n=0;n<m.mem2.size();++n)m.mem2[n]=uint8_t(rng());
  const uint32_t edges[]={0,8,12,0xffff,0x17ffff8,0x1800000,0x1800004,0x1800ffc,
    0x0c000000,0x0c006c00,0x0d000000,0x0ffffffc,0x10000000,0x13fffff8,0x14000000,
    0x1400004,0x14000ffc,0x3ffffff9,0x3fffffff};
  unsigned checks=0;
  auto check=[&](uint32_t address) {
    assert(m.read8(address)==native_fastmem::read8(m,address));
    assert(m.read16(address)==native_fastmem::read16(m,address));
    assert(m.read32(address)==native_fastmem::read32(m,address));
    assert(m.read64(address)==native_fastmem::read64(m,address));
    float af=m.read_f32(address),bf=native_fastmem::read_f32(m,address);
    double ad=m.read_f64(address),bd=native_fastmem::read_f64(m,address);
    assert(std::memcmp(&af,&bf,4)==0 && std::memcmp(&ad,&bd,8)==0);checks+=6;
  };
  for(uint32_t edge:edges)for(unsigned offset=0;offset<9;++offset)
    for(uint32_t mirror:{0u,0x80000000u,0xc0000000u})check((edge+offset)|mirror);
  for(unsigned n=0;n<12000;++n)check((rng()%2?0x10000000u:0u)+(rng()%0x1800000u));
  for(unsigned n=0;n<300;++n)check(rng()&0x3fffffffu);
  g_observations=0;const auto io=m.read32(0x0c006c00u);const auto expected=g_observations;
  g_observations=0;assert(native_fastmem::read32(m,0x0c006c00u)==io && g_observations==expected && expected==1);
  // Both banks share one reservation; per-bank dirty tracking must remain independent.
  assert(ResetWriteWatch(m.mem1.data(),m.mem1.size())==0);
  assert(ResetWriteWatch(m.mem2.data(),m.mem2.size())==0);
  m.mem1[0x12340]=1;m.mem2[0x23450]=2;
  void* pages[16];ULONG_PTR count=16;DWORD granularity=0;
  assert(GetWriteWatch(WRITE_WATCH_FLAG_RESET,m.mem1.data(),m.mem1.size(),pages,&count,&granularity)==0);
  assert(count==1 && pages[0]==m.mem1.data()+0x12000 && granularity==4096);
  count=16;
  assert(GetWriteWatch(WRITE_WATCH_FLAG_RESET,m.mem2.data(),m.mem2.size(),pages,&count,&granularity)==0);
  assert(count==1 && pages[0]==m.mem2.data()+0x23000);
  // Compare the new table and stack stores with the unchanged checked methods.
  // Include dynamically changed watches and page/bank boundaries.
  for(uint32_t watch:{0u,0x80010000u,0x90012340u}) {
    g_watch_addr=watch;
    for(uint32_t base:{0xcu,0xfffdu,0x10000u,0x17ffffdu,0x10012340u,0x13fffffdu,0x0c008000u})
      for(unsigned offset=0;offset<8;++offset)for(uint32_t mirror:{0u,0x80000000u,0xc0000000u}) {
        uint32_t a=(base+offset)|mirror,v=rng();
        g_observations=0;m.native_v13_checked_write32(a,v);auto observations=g_observations;
        const auto expected=m.read32(a);
        g_observations=0;native_guest_fast::write32(m,a,v);assert(g_observations==observations);
        assert(m.read32(a)==expected);
        g_observations=0;m.write32(a,v);assert(g_observations==observations);
        assert(m.read32(a)==expected);checks+=4;
      }
  }
  std::printf("Protected fastmem parity passed: %u scalar/float checks, RAM, mirrors, sentinel, padding, MMIO and holes.\n",checks);
}
'''

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--include',type=Path,required=True);args=parser.parse_args()
    text='void f(){auto a=ctx.mmu.read32(x);ctx.mmu.write32(x,a);}\n'
    changed=hot_source(text);assert changed==hot_source(changed) and restore(changed)==text
    assert 'ctx.mmu.write32' in changed
    # Reuse only the stub declarations from the existing production-MMU fixture.
    tree=ast.parse((PORT/'tools/check_fastmem_static_base.py').read_text(encoding='utf-8'))
    stubs=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='STUBS' for t in node.targets))
    stubs=stubs.replace('uint32_t HW_Reg_Read32(uint32_t){return 0;}','uint32_t HW_Reg_Read32(uint32_t a){++g_observations;return a^0xa5c39921u;}')
    with tempfile.TemporaryDirectory(prefix='protected-fastmem-') as directory:
        work=Path(directory);inc=work/'runtime';inc.mkdir()
        for name in ('native_fastmem.h','native_fastmem_allocator.h'):
            shutil.copy2(PORT/'runtime-overlays'/name,inc/name)
        code=work/'check.cpp';code.write_text('#include "runtime/cpu_context.h"\n'+stubs+FIXTURE,encoding='utf-8')
        exe=work/'check.exe';compiler=shutil.which('g++') or str(winlibs_bin()/'g++.exe')
        subprocess.run([compiler,'-std=c++20','-O2','-march=x86-64-v3','-fno-strict-aliasing','-DNWII_STATIC_RAM_BASE',
          '-DNWII_WATCH_PAGE_MARKING','-I',str(work),'-I',str(args.include),str(code),str(PORT/'runtime-overlays/static_ram_base.cpp'),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True,timeout=90)

if __name__=='__main__':main()

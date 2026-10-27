"""Bounded read-only captures at guest texture conversion entry/post-conversion."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/core/os_hle_stubs.cpp';s=p.read_text()
begin='  // NATIVE_TEXTURE_SOURCE_PROBE_BEGIN\n';end='  // NATIVE_TEXTURE_SOURCE_PROBE_END\n'
if begin in s:
    a=s.index(begin);b=s.index(end,a)+len(end);s=s[:a]+s[b:]
probe=r'''  // NATIVE_TEXTURE_SOURCE_PROBE_BEGIN
  if(func_addr==0x802e0f98 || func_addr==0x802e0a64) {
    static const char* dir=std::getenv("NWII_TEXTURE_SOURCE_PROBE");
    static unsigned count=0;
    static std::set<uint64_t> seen;
    if(dir && count<100) {
      uint32_t address=0,width=0,height=0,format=0,object=ctx.gpr[3];
      if(func_addr==0x802e0f98) {
        address=ctx.gpr[4];width=ctx.gpr[5];height=ctx.gpr[6];format=6;
      } else if(ctx.mmu.get_ptr(object)) {
        width=ctx.mmu.read32(object+0x3c);height=ctx.mmu.read32(object+0x40);
        format=ctx.mmu.read32(object+0x54);address=ctx.mmu.read32(object+0x64);
      }
      const uint64_t n=uint64_t(width)*height*4;
      uint32_t phys=address&0x3fffffff;
      const bool valid=(phys<0x01800000 && phys+n<=0x01800000) ||
                       (phys>=0x10000000 && uint64_t(phys)+n<=0x14000000);
      uint64_t key=(uint64_t(func_addr)<<32)|address;
      if(format==6 && width && height && n<=1048576 && valid && seen.insert(key).second) {
        char file[2048];std::snprintf(file,sizeof(file),"%s/texture_%03u_%08x_%08x_%ux%u.bin",dir,++count,func_addr,address,width,height);
        if(FILE* f=std::fopen(file,"wb")){std::fwrite(ctx.mmu.get_ptr(address),1,size_t(n),f);std::fclose(f);}
        std::cout<<"[TEXTURE_SOURCE] call="<<std::hex<<func_addr<<" object="<<object<<" address="<<address<<std::dec<<" width="<<width<<" height="<<height<<" format="<<format<<" path="<<file<<std::endl;
      }
    }
  }
  // NATIVE_TEXTURE_SOURCE_PROBE_END
'''
needle='  // PNG_PROBE_V1: bounded observer; no guest state mutations.\n'
if needle not in s:raise SystemExit('Trace source-probe insertion changed')
s=s.replace(needle,probe+needle,1)
if '#include <cstdio>' not in s:s='#include <cstdio>\n'+s
if p.read_text()!=s:p.write_text(s)
print('Opt-in source/conversion texture capture staged; requires filtered call tracing.')

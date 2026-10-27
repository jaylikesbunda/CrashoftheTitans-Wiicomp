"""Preserve Wii MEM2 addresses in GX list reads and snapshot identities."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
base=PORT/'build/nwii-full-source/nWiiRuntime/src/hle'
p=base/'gx/fifo_parser.cpp';s=p.read_text()
old='''    uint32_t phys = addr & 0x1FFFFFFF;
    if (!nwii::runtime::g_mmu || size == 0 || size > 0x400000 ||
        phys + size > 0x01800000 || depth >= 4)
        return;'''
new='''    // NATIVE_DISPLAY_LIST_BANKS_V1: Wii lists may reside in either RAM bank.
    const uint32_t phys = addr & 0x3fffffffu;
    const bool mem1 = phys < 0x01800000u && size <= 0x01800000u-phys;
    const bool mem2 = phys >= 0x10000000u && phys < 0x14000000u && size <= 0x14000000u-phys;
    if (!nwii::runtime::g_mmu || size == 0 || size > 0x400000 ||
        (!mem1 && !mem2) || depth >= 4)
        return;
    static const bool bank_probe=std::getenv("NWII_DL_BANK_PROBE")!=nullptr;
    if(bank_probe) {
        static std::map<uint32_t,unsigned> seen;
        static const char *gate=std::getenv("NWII_EFB_PROBE_ARM_FILE");
        FILE *f=gate?std::fopen(gate,"rb"):nullptr;
        if((!gate || f) && seen.size()<100 && !seen.count(phys)) {
            seen[phys]=size;
            printf("[DLBANK] addr=%08x phys=%08x size=%u bank=%u depth=%d\\n",addr,phys,size,mem2?2u:1u,depth);
        }
        if(f)std::fclose(f);
    }'''
if 'NATIVE_DISPLAY_LIST_BANKS_V1' not in s:
    if s.count(old)!=1:raise SystemExit('Display-list range context changed')
    s=s.replace(old,new)
    s=s.replace('get_ptr(0x80000000u | phys)','get_ptr(phys)')
    p.write_text(s)
p=base/'gx.cpp';s=p.read_text()
if 'NATIVE_DISPLAY_LIST_SNAPSHOT_BANKS_V1' not in s:
    old='''    if (!nwii::runtime::g_mmu || size == 0 || size > (8u << 20)) return;'''
    new='''    // NATIVE_DISPLAY_LIST_SNAPSHOT_BANKS_V1
    const uint32_t phys=addr&0x3fffffffu;
    const bool valid=(phys<0x01800000u && size<=0x01800000u-phys) ||
        (phys>=0x10000000u && phys<0x14000000u && size<=0x14000000u-phys);
    if (!nwii::runtime::g_mmu || !valid || size == 0 || size > (8u << 20)) return;'''
    if s.count(old)!=1:raise SystemExit('Snapshot range context changed')
    s=s.replace(old,new)
    s=s.replace('get_ptr(0x80000000u | (addr & 0x03FFFFFF))','get_ptr(phys)')
    s=s.replace('(addr & 0x03FFFFFF)','(addr & 0x3fffffff)')
    p.write_text(s)
print('GX display lists and snapshot keys preserve both Wii RAM banks.')

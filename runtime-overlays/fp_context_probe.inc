// CPU-thread-only, opt-in, bounded observer. Never writes guest state.
static void native_fp_context_probe(CPUContext &ctx, const char *event, uint32_t c=0) {
  static const bool enabled = std::getenv("NWII_FP_CONTEXT_PROBE") != nullptr;
  if (!enabled) return;
  static std::vector<std::string> recent;
  static unsigned good=0, bad=0, targeted=0;
  const bool search=ctx.pc>=0x8029D6A8 && ctx.pc<=0x8029D8C8;
  const bool loop=ctx.pc==0x8029D8B0 || ctx.pc==0x8029D7BC;
  uint64_t bits; std::memcpy(&bits,&ctx.fpr[2],8);
  // While FP is disabled, physical registers may legitimately belong to another
  // thread; the next guarded FP instruction takes the ownership exception.
  const bool wrong=loop && (ctx.msr & 0x2000u) && bits!=0x4330000000000000ULL;
  if (event[0]=='p' && (!search || (wrong ? bad>=8 : good>=16))) return;
  auto ram=[](uint32_t a){return (a>=0x80000000u && a<0x81800000u) || (a>=0x90000000u && a<0x94000000u);};
  if (!c) c=ctx.mmu.read32(0x800000D4);
  uint32_t bias=ctx.gpr[2]-19816;
  std::ostringstream out;
  out<<"[FPContext] "<<event<<std::hex<<" pc="<<ctx.pc<<" lr="<<ctx.lr<<" r2="<<ctx.gpr[2]
     <<" bias="<<bias<<" fixed="<<ctx.mmu.read64(0x8059B058)<<" addressed="<<(ram(bias)?ctx.mmu.read64(bias):0)
     <<" f2="<<bits<<" msr="<<ctx.msr<<" context="<<c<<" thread="<<ctx.mmu.read32(0x800000E4)
     <<" owner="<<ctx.mmu.read32(0x800000D8);
  if(ram(c) && ram(c+0x2c8)) out<<" saved_f2="<<ctx.mmu.read64(c+0xa0)<<" state="<<ctx.mmu.read16(c+0x1a2)<<" saved_pc="<<ctx.mmu.read32(c+0x198);
  out<<'\n';
  if(event[0]!='p') {
    if(recent.size()==32)recent.erase(recent.begin());
    recent.push_back(out.str());
    if(search && targeted++<32)std::cout<<out.str()<<std::flush;
  } else {
    if(wrong && bad++==0) {std::cout<<"[FPContext] recent context transitions:\n";for(const auto &line:recent)std::cout<<line;}
    if(!wrong)++good;
    std::cout<<out.str()<<std::flush;
  }
}

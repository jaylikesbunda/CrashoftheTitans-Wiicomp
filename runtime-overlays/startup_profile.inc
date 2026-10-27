  // Samples execute on the CPU thread. These are callback-point observations,
  // not a host profiler or an instruction-accurate time distribution.
  static const bool startup_profile = std::getenv("NWII_STARTUP_PROFILE") != nullptr;
  if (startup_profile) {
    static const double font_after = [](){const char *e=std::getenv("NWII_FONT_PROBE_AFTER");return e?std::strtod(e,nullptr):-1.0;}();
    static const auto font_start = std::chrono::steady_clock::now();
    static unsigned font_samples = 0;
    if (font_after>=0 && font_samples<20 && (ctx.pc==0x80326F80 || ctx.pc==0x803276D0) &&
        std::chrono::duration<double>(std::chrono::steady_clock::now()-font_start).count()>=font_after) {
      const uint32_t object = ctx.pc==0x80326F80?ctx.gpr[3]:ctx.gpr[18];
      auto ram = [](uint32_t p){return (p>=0x80000000u && p<0x81800000u) || (p>=0x90000000u && p<0x94000000u);};
      if(ram(object) && ram(object+0x50)) {
        std::ostringstream out;
        out<<"[FontProbe] n="<<++font_samples<<" pc=0x"<<std::hex<<ctx.pc<<" object=0x"<<object
           <<" pages="<<std::dec<<ctx.mmu.read32(object+0x3C)<<" clear_index="<<ctx.gpr[8]
           <<" draw_index="<<ctx.gpr[23]<<" text=0x"<<std::hex<<ctx.gpr[20]<<" table=0x"<<ctx.gpr[27]<<" text16=";
        if(ram(ctx.gpr[20]) && ram(ctx.gpr[20]+32))
          for(int i=0;i<16;++i)out<<std::hex<<ctx.mmu.read16(ctx.gpr[20]+i*2)<<',';
        out<<'\n';std::cout<<out.str()<<std::flush;
      }
    }
    {
      // Opt-in: dump callee-saved registers and the object behind r30 while a named PC spins.
      static const uint32_t spin_pc = [](){const char *e=std::getenv("NWII_SPIN_PROBE_PC");return e?uint32_t(std::strtoul(e,nullptr,16)):0u;}();
      static const double spin_after = [](){const char *e=std::getenv("NWII_SPIN_PROBE_AFTER");return e?std::strtod(e,nullptr):0.0;}();
      static unsigned spin_samples = 0;
      static uint64_t spin_hits = 0;
      { static uint32_t pl_ctr=0, pl_pc=0, pl_lr=0, pl_r3=0; static unsigned pl_n=0; static uint32_t pl_depth=0; if (spin_pc && ctx.pc==spin_pc) { if (pl_pc==spin_pc && pl_ctr>1 && ctx.ctr!=pl_ctr-1 && ctx.ctr!=pl_ctr && pl_n<8) { ++pl_n; std::cout<<"[CtrJump] prev_ctr=0x"<<std::hex<<pl_ctr<<" now_ctr=0x"<<ctx.ctr<<" r3_prev=0x"<<pl_r3<<" r3=0x"<<ctx.gpr[3]<<" lr=0x"<<ctx.lr<<" in_cb="<<ctx.in_callback<<" depth="<<std::dec<<ctx.callback_depth<<" saved_ctx=0x"<<std::hex<<ctx.dispatch_saved_ctx<<'\n'<<std::flush; } pl_ctr=ctx.ctr; pl_r3=ctx.gpr[3]; } pl_pc=ctx.pc; (void)pl_lr; (void)pl_depth; } { struct RE{uint32_t pc,lr,ctr,r1;}; static RE ring[48]; static unsigned rn=0; static bool dumped=false; ring[rn%48]={ctx.pc,ctx.lr,ctx.ctr,ctx.gpr[1]}; ++rn; if (!dumped && (ctx.ctr>>16)==0xFFFFu) { dumped=true; std::cout<<"[CtrRing] corrupt ctr=0x"<<std::hex<<ctx.ctr<<" pc=0x"<<ctx.pc<<'\n';for(unsigned k=0;k<48;++k){const RE&e=ring[(rn+k)%48]; std::cout<<"  pc=0x"<<e.pc<<" lr=0x"<<e.lr<<" ctr=0x"<<e.ctr<<" r1=0x"<<e.r1<<'\n';} std::cout<<std::flush; } } static uint64_t spin_consec=0; if (spin_pc) { if (ctx.pc==spin_pc) ++spin_consec; else spin_consec=0; } if (spin_pc && ctx.pc==spin_pc && spin_samples<4 && ctx.ctr>0x400000u && (++spin_hits & 0xFFFF)==0) {
        auto ram = [](uint32_t p){return (p>=0x80000000u && p<0x81800000u) || (p>=0x90000000u && p<0x94000000u);};
        std::ostringstream out;
        out<<"[SpinProbe] n="<<++spin_samples<<std::hex<<" pc=0x"<<ctx.pc<<" lr=0x"<<ctx.lr<<" ctr=0x"<<ctx.ctr
           <<" r3=0x"<<ctx.gpr[3]<<" r4=0x"<<ctx.gpr[4]<<" r28=0x"<<ctx.gpr[28]<<" r29=0x"<<ctx.gpr[29]
           <<" r30=0x"<<ctx.gpr[30]<<" r31=0x"<<ctx.gpr[31]<<" r6..r10=" <<ctx.gpr[6]<<","<<ctx.gpr[7]<<","<<ctx.gpr[8]<<","<<ctx.gpr[9]<<","<<ctx.gpr[10]<<std::dec<<" f0="<<ctx.fpr[0]<<" f1="<<ctx.fpr[1]<<" f2="<<ctx.fpr[2]<<std::hex<<" cr0="<<ctx.cr[0].lt<<ctx.cr[0].gt<<ctx.cr[0].eq<<ctx.cr[0].so<<"\n";
        if (ram(ctx.gpr[30]) && ram(ctx.gpr[30]+0x80)) {
          out<<"[SpinProbe] obj:";
          for(int i=0;i<32;++i)out<<' '<<std::hex<<ctx.mmu.read32(ctx.gpr[30]+i*4);
          out<<'\n';
        }
        if (spin_pc==0x80371C10) {
          for(int r: {29,31}) {
            uint32_t a=ctx.gpr[r];
            if(ram(a) && ram(a+0x80)) {
              out<<"[SpinProbe] r"<<std::dec<<r<<" words:";
              for(int i=0;i<32;++i)out<<' '<<std::hex<<ctx.mmu.read32(a+i*4);
              out<<'\n';
            }
          }
        }
        std::cout<<out.str()<<std::flush;
      }
    }
    static uint64_t calls = 0, previous_inst = 0;
    static auto sampled = std::chrono::steady_clock::now();
    static auto reported = sampled;
    static std::map<uint64_t, uint64_t> counts;
    if ((++calls & 4095) == 0) {
      const auto now = std::chrono::steady_clock::now();
      if (now - sampled >= std::chrono::milliseconds(1)) {
        sampled = now;
        ++counts[(uint64_t(ctx.pc) << 32) | ctx.lr];
      }
      if (now - reported >= std::chrono::seconds(5)) {
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now-reported).count();
        std::vector<std::pair<uint64_t,uint64_t>> ranked(counts.begin(),counts.end());
        std::sort(ranked.begin(), ranked.end(), [](const auto &a,const auto &b){return a.second>b.second;});
        std::ostringstream out;
        out << "[StartupProfile] ms=" << elapsed << " inst_delta=" << ctx.inst_count-previous_inst
            << " pc=0x" << std::hex << ctx.pc << " lr=0x" << ctx.lr
            << " r3=0x" << ctx.gpr[3] << " r4=0x" << ctx.gpr[4] << " r5=0x" << ctx.gpr[5]
            << " current=0x" << ctx.mmu.read32(0x800000E4) << " top_pc/lr/hits=";
        for (size_t i=0;i<ranked.size() && i<8;++i)
          out << std::hex << (ranked[i].first>>32) << '/' << uint32_t(ranked[i].first)
              << '/' << std::dec << ranked[i].second << ',';
        out << '\n';
        auto ram = [](uint32_t p){return (p>=0x80000000u && p<0x81800000u) || (p>=0x90000000u && p<0x94000000u);};
        uint32_t th=ctx.mmu.read32(0x800000DC);
        for(int i=0;i<16 && ram(th) && ram(th+0x300);++i) {
          const bool current=th==ctx.mmu.read32(0x800000E4);
          uint32_t sp=current?ctx.gpr[1]:ctx.mmu.read32(th+4);
          out << "[StartupThread] thread=0x" << std::hex << th << " current=" << current
              << " state=" << std::dec << ctx.mmu.read16(th+0x2C8)
              << " suspend=" << ctx.mmu.read32(th+0x2CC)
              << " prio=" << ctx.mmu.read32(th+0x2D0) << " waitq=0x" << std::hex << ctx.mmu.read32(th+0x2DC)
              << " srr0=0x" << ctx.mmu.read32(th+0x198) << " stack=";
          for(int f=0;f<8 && ram(sp) && ram(sp+8);++f) {
            out << std::hex << sp << ':' << ctx.mmu.read32(sp+4) << ',';
            const uint32_t next=ctx.mmu.read32(sp);
            if(next<=sp)break;
            sp=next;
          }
          out << '\n';
          th=ctx.mmu.read32(th+0x2FC);
        }
        // Original CGame singleton, identified by FUN_8021ADC8. Read-only
        // state/desired state and module words; interpretations stay in reports.
        out << "[LoadingState] game=805c95a0 words:";
        for(int i=0;i<40;++i)out<<' '<<std::hex<<ctx.mmu.read32(0x805C95A0+i*4);
        out<<'\n';
        uint32_t sound=ctx.mmu.read32(ctx.gpr[13]-0x3734);
        uint32_t bus=ctx.mmu.read32(0x800000F8), divisor=bus/4000;
        out<<"[SoundWait] manager="<<std::hex<<sound<<" barrier="<<ctx.mmu.read32(0x805C74D8)
           <<" deadline_ms="<<ctx.mmu.read32(0x805C74DC)<<" expected_now_ms="<<(divisor?uint32_t(ctx.read_timebase()/divisor):0);
        if(ram(sound) && ram(sound+0xff0))out<<" counter="<<ctx.mmu.read32(sound+0xfe8)<<" lock="<<ctx.mmu.read32(sound+0xfe4)<<" pending="<<ctx.mmu.read32(sound+0x20);
        out<<'\n';
        if(ram(sound)&&ram(sound+0x40)){{uint32_t sm=ctx.mmu.read32(sound+0x1c);if(ram(sm)&&ram(sm+0x10)){out<<"[SoundSem] sem=0x"<<std::hex<<sm<<" count="<<std::dec<<int(ctx.mmu.read32(sm))<<std::hex<<" q=" <<ctx.mmu.read32(sm+4)<<","<<ctx.mmu.read32(sm+8);uint32_t th=ctx.mmu.read32(sm+4);if(ram(th)&&ram(th+0x2e0))out<<" head_th=0x"<<th<<" state="<<std::dec<<ctx.mmu.read16(th+0x2C8)<<" susp="<<ctx.mmu.read32(th+0x2CC);out<<'\n';}}for(uint32_t sm2: {0x91d73c58u,0x91d8a400u}){if(ram(sm2)&&ram(sm2+0x10)){out<<"[SemDump] sem=0x"<<std::hex<<sm2<<" count="<<std::dec<<int(ctx.mmu.read32(sm2))<<std::hex<<" q="<<ctx.mmu.read32(sm2+4)<<","<<ctx.mmu.read32(sm2+8);uint32_t th=ctx.mmu.read32(sm2+4);if(ram(th)&&ram(th+0x2e0))out<<" head_th=0x"<<th<<" state="<<std::dec<<ctx.mmu.read16(th+0x2C8);out<<'\n';}}out<<"[SoundMgr] +18:";for(int w=6;w<18;++w)out<<' '<<std::hex<<ctx.mmu.read32(sound+w*4);uint32_t pend=ctx.mmu.read32(sound+0x20);if(ram(pend)&&ram(pend+0x60)){out<<" pend:";for(int w=0;w<24;++w)out<<' '<<std::hex<<ctx.mmu.read32(pend+w*4);}out<<'\n';}
        for(int i=0;i<20;++i) {
          uint32_t a=ctx.mmu.read32(0x805C95A0+0x1c+i*4);
          if(ram(a) && ram(a+0x80)) {
            out<<"[LoadingModule] slot="<<std::dec<<i<<" object="<<std::hex<<a<<" words:";
            for(int j=0;j<32;++j)out<<' '<<std::hex<<ctx.mmu.read32(a+j*4);
            out<<'\n';
          }
        }
        out << "[WaitHW] vi_events=" << std::dec << hw::native_vi_events.load()
            << " pe_tokens=" << hw::native_pe_tokens.load()
            << " pe_finishes=" << hw::native_pe_finishes.load()
            << " pe_sr=0x" << std::hex << hw::g_pe_sr.load()
            << " pi_sr=0x" << hw::pi_intsr.load() << " pi_mr=0x" << hw::pi_intmr.load()
            << " di_delay=" << std::dec << hw::g_di_interrupt_delay << '\n';
        std::cout << out.str() << std::flush;
        hw::native_wait_ipc_snapshot();
        previous_inst=ctx.inst_count;reported=now;counts.clear();
      }
    }
  }

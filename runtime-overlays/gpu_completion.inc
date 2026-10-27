// Called only after preceding draws/copies have executed in FIFO order.
// EFB copies do not themselves request a PE finish interrupt.
static void NativeGpuCompletion(uint8_t reg,uint32_t value,bool have_gl) {
  if(reg==0x45 && (value&0xffu)==2u) {
    if(have_gl)glFinish();
    nwii::runtime::hw::pe_signal_finish();
  } else if(reg==0x47 || reg==0x48) {
    if(have_gl)glFinish();
    nwii::runtime::hw::pe_signal_token(value&0xffffu,reg==0x48);
  }
}

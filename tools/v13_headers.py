"""V13 table-store and launch-predicate changes, preserving checked fallbacks."""
import re

def cpu_source(text,replace):
    if 'struct MMU {' not in text:return text
    text=replace(text,'  intptr_t page_bias[16384];','  intptr_t page_bias[16384];\n  uint32_t native_v13_watch_snapshot=0;')
    text=replace(text,'  void native_init_page_bias() {','  void native_init_page_bias() {\n    native_v13_watch_snapshot=g_watch_addr;')
    entries=[('write8','uint8_t',1,'uint8_t bits=value;'),
             ('write16','uint16_t',2,'uint16_t bits=__builtin_bswap16(value);'),
             ('write32','uint32_t',4,'uint32_t bits=__builtin_bswap32(value);'),
             ('write_f32','float',4,'uint32_t bits;std::memcpy(&bits,&value,4);bits=__builtin_bswap32(bits);')]
    for name,ty,width,bits in entries:
        pattern=re.compile(r'  __attribute__\(\(always_inline\)\) inline void '+name+r'\(uint32_t addr, '+ty+r' value\) \{\n.*?\n  \}\n',re.S)
        match=pattern.search(text);assert match,('V13 store anchor',name)
        checked=match[0].replace('inline void '+name+'(','inline void native_v13_checked_'+name+'(',1)
        checked=checked.replace('__attribute__((always_inline)) inline void', '__attribute__((noinline,cold)) void',1)
        fast='''  __attribute__((always_inline)) inline void NAME(uint32_t addr, TYPE value) {
    const uint32_t paddr=addr&0x3fffffffu;
    if(__builtin_expect(g_watch_addr==native_v13_watch_snapshot,1)) {
      const intptr_t bias=page_bias[paddr>>16];
      if(__builtin_expect(bias>1
#ifndef NWII_WATCH_PAGE_MARKING
          && NWII_WATCH_CLEAR(paddr,WIDTH)
#endif
          ,1)) { BITS std::memcpy(reinterpret_cast<void*>(bias+paddr),&bits,WIDTH);return; }
    }
    native_v13_checked_NAME(addr,value);
  }
'''.replace('NAME',name).replace('TYPE',ty).replace('WIDTH',str(width)).replace('BITS',bits)
        text=replace(text,match[0],checked+fast)
    # A changed debug watch retains the original predicate; normal launches use
    # the cached write-gather reachability test.
    anchor='// Strict Dolphin-accurate MMU'
    helper='''__attribute__((noinline,cold)) inline bool native_v13_wg_watch(uint32_t address,unsigned bytes) {
  return native_watch_span_may_hit(address,bytes);
}
'''
    assert text.count(anchor)==1
    text=replace(text,anchor,helper+anchor)
    pattern=re.compile(r'!native_watch_span_may_hit\(paddr,(\d)\)')
    for line in text.splitlines(keepends=True):
        if pattern.search(line):
            new=pattern.sub(lambda m:'!((native_launch_flags::wg_watch_possible || g_watch_addr!=native_launch_flags::watch) && native_v13_wg_watch(paddr,'+m[1]+'))',line)
            text=replace(text,line,new)
    return '#include "runtime/native_launch_flags.h" // NATIVE_V13_HEADERS_V1\n'+text

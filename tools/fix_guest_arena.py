"""NATIVE_GUEST_ARENA_V1: Dolphin-style fastmem for guest loads and stores (on by default; NWII_BUILD_GUEST_ARENA=0 opts out).

Every guest access becomes one host instruction, `movbe reg,[base + guest_address]` or `movbe [base + guest_address],reg`:
no bank test, no range check, no page-table load, no watch test. MEM1 and MEM2 live in pagefile sections that are mapped at
every 0x40000000 mirror of a 4 GiB arena (and, for MMU::mem1/mem2, at 0x20000000 / 0x30000000, so every page_bias based helper
is unchanged and sees the same bytes). MMIO, the write-gather pipe, the EFB and every hole are unmapped; the access faults and
a vectored handler (runtime-overlays/native_guest_arena.cpp) emulates it with the ordinary MMU slow paths and resumes.

What this stage changes in the staged tree (all reversible; `--restore` or NWII_BUILD_GUEST_ARENA=0 removes every edit):
  runtime/native_guest_arena.h, native_guest_arena_fifo.h   new headers (accessors, write-gather helpers)
  runtime/native_fastmem.h                                  readN / read_f32 / read_f64 -> arena loads
  runtime/native_guest_fast.h                               writeN / write_f32 / write_f64 -> arena stores (StackSpan stays)
  runtime/native_fastmem_allocator.h                        VirtualAlloc allocator + load-only handler -> stub
  runtime/cpu_context.h                                     psq_load/psq_load_pair/psq_store/psq_store_val/batch helpers
  CMakeLists.txt                                            native_guest_arena.cpp, compile definitions, no ram_write_watch.cpp
  src/hle/virtual_disc.cpp, src/hle/native_savestate_ios.inc  HostWriteScope around kernel reads into guest RAM
  crash-generated / crash-split / crash-hot                 `ctx.mmu.writeN(` -> arena stores (not in the hardware-touching
                                                            functions), statically recognised write-gather sites -> direct pipe
Layouts (NWII_BUILD_GUEST_ARENA_MODE): dynamic (default: 4 GiB arena at an OS-chosen base above 4 GiB held in one register; the
layout is compiled into the generated code, so the default is the one that cannot fail to place on a PC where a driver or DLL
occupies a fixed range), fixed (4 GiB arena at 0x7FFF0000, the only 64 KiB aligned address that is both a disp32 and clear of the
shared user data page, so `movbe eax,[rax+0x7fff0000]`; startup aborts if that range is taken), mask (the 1 GiB window at 0x20000000 with `addr & 0x3fffffff`). A pagefile section view cannot
be MEM_WRITE_WATCH, so the mirrored layouts replace ram_write_watch.cpp by a protection based tracker (on by default; NWII_ARENA_TRACK=0
makes the texture / geometry caches compare bytes); the mask layout keeps GetWriteWatch.
Requires the NWII_STATIC_RAM_BASE build (tools/fix_v13_runtime.py) and runs after every other stage (after the camera-glide
stage; it reads the final text of the hot units). Idempotent. Environment:
  NWII_BUILD_GUEST_ARENA=0                 disable (restores the exact pre-arena text); unset or 1 enables. Unset also stays off,
                                           without failing, when the staged tree has no NWII_STATIC_RAM_BASE build
  NWII_BUILD_GUEST_ARENA_MODE=dynamic|fixed|mask
  NWII_BUILD_GUEST_ARENA_STORES=all|hot    arena stores in every generated unit (default) or only in the promoted crash-hot units
  NWII_ARENA_SLOW_FUNCTIONS=<file>         extra functions (hex addresses, one per line) that keep the checked accessors; default
                                           tools/guest_arena_slow_functions.txt when present (see --map-sites)
Runtime switches: NWII_ARENA_PAGE0=0 (do not trap stores to physical page 0), NWII_ARENA_TRACK=0 (turn off the software write tracker for the
texture / geometry caches in the mirrored layouts; on by default), NWII_ARENA_SITES=<file> (write the fault-site report at exit),
NWII_ARENA_DEBUG=1.
Usage: fix_guest_arena.py [--restore] [--port <native-port dir>] [--map-sites report.txt exe [--out file]]
"""
from pathlib import Path
import argparse, base64, os, re, subprocess, sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fix_protected_fastmem import IO_FUNCTIONS, FUNCTION  # noqa: E402  (the same hardware-touching function list)

MARK = 'NATIVE_GUEST_ARENA_V1'
NL = '\n'
BLOCK = re.compile(r'/\* ' + MARK + r' ([A-Za-z0-9+/=]*) \*/\r?\n(?:(?!/\* ' + MARK + r' [A-Za-z0-9+/=]* \*/\r?\n).)*?/\* END_' + MARK + r' \*/(\r?\n?)', re.S)
INCLUDE_CTX = '#include "runtime/native_guest_arena.h" // ' + MARK + NL


def read_text(path):
    """Bytes in, bytes out: the staged runtime and generated sources are CRLF, the overlays LF; edits must leave both alone."""
    return Path(path).read_bytes().decode('utf-8', errors='surrogateescape')


def write_text(path, text):
    Path(path).write_bytes(text.encode('utf-8', errors='surrogateescape'))


def strip_include(text, header):
    """Remove the include line this stage added, whatever line ending another stage's text-mode rewrite gave it."""
    return re.sub(r'#include "runtime/' + re.escape(header) + r'" // ' + MARK + r'\r?\n', '', text)


def b64(text):
    return base64.b64encode(text.encode('utf-8', errors='surrogateescape')).decode()


def unb64(text):
    return base64.b64decode(text).decode('utf-8', errors='surrogateescape')


def wrap(old, new):
    """Replacement text that carries the original so --restore is exact (the newline after the end marker belongs to the block
    only when the original ended with one)."""
    tail = NL if old.endswith(NL) else ''
    return '/* ' + MARK + ' ' + b64(old) + ' */' + NL + new.rstrip(NL) + NL + '/* END_' + MARK + ' */' + tail


def _unwrap(m):
    old = unb64(m[1])
    return old if old.endswith(NL) else old + m[2]


def restore_blocks(text):
    while BLOCK.search(text):
        text = BLOCK.sub(_unwrap, text)
    return text


def replace_regex(text, pattern, new, count, name, flags=re.S):
    matches = list(pattern.finditer(text)) if hasattr(pattern, 'finditer') else list(re.finditer(pattern, text, flags))
    assert len(matches) == count, 'arena anchor moved: %s (found %d, want %d)' % (name, len(matches), count)
    out, last = [], 0
    for m in matches:
        out.append(text[last:m.start()])
        out.append(wrap(m[0], new))
        last = m.end()
    out.append(text[last:])
    return ''.join(out)


def replace_literal(text, old, new, count, name):
    assert text.count(old) == count, 'arena anchor moved: %s (found %d, want %d)' % (name, text.count(old), count)
    return text.replace(old, wrap(old, new))


# ---------------------------------------------------------------------------------------------------------------------------
# cpu_context.h
# ---------------------------------------------------------------------------------------------------------------------------
F32_PAIR_LOAD = (r'      // NATIVE_PSQ_F32_PAIR_V1: one guard \+ one 8-byte load for a W==0 MEM1 pair\..*?'
                 r'      result\.p = W \? 1\. : double\(mmu\.read_f32\(addr \+ 4\)\);\r?\n')
F32_PAIR_LOAD_NEW = ('      // Two arena loads (element by element, exactly like the checked path); MMIO and holes fault into the MMU slow path.\n'
                     '      result.f = native_arena::ldf32(addr);\n'
                     '      result.p = W ? 1. : double(native_arena::ldf32(addr + 4));\n')
INT_PAIR_LOAD = (r'      // NATIVE_GUEST_PSQ_PAIRS_V1: ordinary contiguous integer pair, one guard\.\r?\n.*?'
                 r'          return \{int16_t\(bits>>16\)\*scale,int16_t\(bits\)\*scale\};\r?\n        \}\r?\n      \}\r?\n')
F32_PAIR_STORE = (r'      // NATIVE_GUEST_PSQ_PAIRS_V1: no observer can run between these RAM writes\.\r?\n.*?'
                  r'      if \(!W\) mmu\.write_f32\(addr \+ 4, float\(pval\)\);\r?\n')
F32_PAIR_STORE_NEW = ('      // Two arena stores, element by element; recognised write-gather sites use psq_store_fifo instead.\n'
                      '      native_arena::stf32(addr, float(fval));\n'
                      '      if (!W) native_arena::stf32(addr + 4, float(pval));\n')
QUANT_PAIR_STORE = (r'      // NATIVE_QUANT_PAIR_STORES_V1: no observer between these ordinary RAM stores\.\r?\n.*?'
                    r'            std::memcpy\(destination, &packet, 4\);\r?\n          \}\r?\n          return;\r?\n        \}\r?\n      \}\r?\n')
PSQ_FIFO_MEMBER = '''  // NATIVE_GUEST_ARENA_V1: psq store at a statically recognised write-gather site (displacement -32768). The ordinary-FIFO pair
  // goes straight to the pipe as one 8-byte packet (as the checked path did); everything else is an ordinary arena store.
  __attribute__((always_inline)) inline void psq_store_fifo(double fval, double pval, uint32_t addr, uint32_t W, uint32_t I) {
    if(__builtin_expect((gqr[I]&7u)==0,1)){
      const uint32_t physical=addr&0x3fffffffu;
      if((physical&0xfffff000u)==0x0c008000u && (physical&4095u)<=(W?4092u:4088u) && !native_watch_span_may_hit(physical,W?4:8)){
        const float a=float(fval);
        if(W){NWII_CTX_BARRIER();GX_WGPIPE_WriteF32(a);return;}
        const float b=float(pval);
        if(g_native_fifo_ok){uint32_t av,bv;std::memcpy(&av,&a,4);std::memcpy(&bv,&b,4);
          const uint64_t bits=(uint64_t(av)<<32)|bv;double packet;std::memcpy(&packet,&bits,8);NWII_CTX_BARRIER();GX_WGPIPE_WriteF64(packet);return;}
        NWII_CTX_BARRIER();GX_WGPIPE_WriteF32(a);GX_WGPIPE_WriteF32(b);return;
      }
    } else if((gqr[I]&7u)>=4u){
      // Quantised pair to the pipe: the same conversions as psq_store_val, written through the pipe instead of faulting.
      const uint32_t physical=addr&0x3fffffffu,st_type=gqr[I]&7u,size=(st_type==4u||st_type==6u)?1u:2u;
      if((physical&0xfffff000u)==0x0c008000u && (physical&4095u)<=4096u-(W?size:2u*size) && !native_watch_span_may_hit(physical,W?size:2u*size)){
        const uint32_t st_scale=(gqr[I]>>8)&0x3Fu;const int signed_scale=(st_scale&32u)?int(st_scale)-64:int(st_scale);
        const uint32_t scale_bits=uint32_t(127+signed_scale)<<23;float scale;std::memcpy(&scale,&scale_bits,sizeof(scale));
        auto emit=[&](double val){switch(st_type){
          case 4: GX_WGPIPE_Write8((uint8_t)(val*scale)); break;
          case 5: GX_WGPIPE_Write16((uint16_t)(val*scale)); break;
          case 6: GX_WGPIPE_Write8((uint8_t)(int8_t)(val*scale)); break;
          default: GX_WGPIPE_Write16((uint16_t)(int16_t)(val*scale)); break;}};
        NWII_CTX_BARRIER();emit(fval);if(!W)emit(pval);return;
      }
    }
    psq_store_val(fval,pval,addr,W,I);
  }
  __attribute__((always_inline)) inline void psq_store_fifo_reg(uint32_t frS, uint32_t addr, uint32_t W, uint32_t I) {
    psq_store_fifo(fpr[frS],ps1[frS],addr,W,I);
  }
  __attribute__((always_inline)) inline void native_batch_psq_store_fifo(double fval, double pval, uint32_t addr, uint32_t W, uint32_t I) {
    psq_store_fifo(fval,pval,addr,W,I);
  }
'''


def patch_cpu_context(text):
    text = strip_include(restore_blocks(text), 'native_guest_arena.h')
    text = INCLUDE_CTX + text
    # psq_load
    text = replace_literal(text, 'fpr[frD]=mmu.read_f32(addr);ps1[frD]=W?1.:double(mmu.read_f32(addr+4));',
                           'fpr[frD]=native_arena::ldf32(addr);ps1[frD]=W?1.:double(native_arena::ldf32(addr+4));', 1, 'psq_load f32')
    # psq_load_pair / native_batch_psq_load_zero: f32 pair
    text = replace_regex(text, F32_PAIR_LOAD, F32_PAIR_LOAD_NEW, 2, 'f32 pair load')
    # psq_load_pair: the integer pair fast path goes; the element lambda below it is the (exact) arena path
    text = replace_regex(text, INT_PAIR_LOAD, '      // Integer pairs load element by element through the arena (the lambda below).\n', 1, 'integer pair load')
    for case, old, new in (
            ('case 4', 'case 4: return mmu.read8(a) * scale;', 'case 4: return native_arena::ld8(a) * scale;'),
            ('case 5', 'case 5: return mmu.read16(a) * scale;', 'case 5: return native_arena::ld16(a) * scale;'),
            ('case 6', 'case 6: return (int8_t)mmu.read8(a) * scale;', 'case 6: return (int8_t)native_arena::ld8(a) * scale;'),
            ('default', 'default: return (int16_t)mmu.read16(a) * scale;', 'default: return (int16_t)native_arena::ld16(a) * scale;')):
        text = replace_literal(text, old, new, 1, 'psq_load_pair lambda ' + case)
    # psq_store
    text = replace_literal(text, 'mmu.write_f32(addr,float(fpr[frS]));return;}', 'native_arena::stf32(addr,float(fpr[frS]));return;}', 1, 'psq_store W=1')
    # psq_store_val / native_batch_psq_store_zero: f32 pair
    text = replace_regex(text, F32_PAIR_STORE, F32_PAIR_STORE_NEW, 2, 'f32 pair store')
    # psq_store_val: quantised pair fast path goes; the element lambda is the (exact) arena path
    text = replace_regex(text, QUANT_PAIR_STORE, '      // Quantised pairs store element by element through the arena (the lambda below).\n', 1, 'quant pair store')
    for old, new in (('case 4: mmu.write8(a, (uint8_t)(val * scale)); break;', 'case 4: native_arena::st8(a, (uint8_t)(val * scale)); break;'),
                     ('case 5: mmu.write16(a, (uint16_t)(val * scale)); break;', 'case 5: native_arena::st16(a, (uint16_t)(val * scale)); break;'),
                     ('case 6: mmu.write8(a, (int8_t)(val * scale)); break;', 'case 6: native_arena::st8(a, (uint8_t)(int8_t)(val * scale)); break;'),
                     ('default: mmu.write16(a, (int16_t)(val * scale)); break;', 'default: native_arena::st16(a, (uint16_t)(int16_t)(val * scale)); break;')):
        text = replace_literal(text, old, new, 1, old[:24])
    # write-gather psq store member
    anchor = '  inline void ps_add(uint32_t D, uint32_t A, uint32_t B) {'
    text = replace_literal(text, anchor, PSQ_FIFO_MEMBER + anchor, 1, 'ps_add anchor')
    return text


# ---------------------------------------------------------------------------------------------------------------------------
# whole-file replacements (original carried in the first line)
# ---------------------------------------------------------------------------------------------------------------------------
FASTMEM_NEW = '''#pragma once
// NATIVE_GUEST_ARENA_V1: guest loads are single movbe instructions on the arena (runtime/native_guest_arena.h). The previous
// bank tests are gone: MMIO and unmapped addresses fault and the handler returns what MMU::read* returned.
#include "runtime/native_guest_arena_fifo.h"
#include <cstdint>
#include <cstring>
namespace native_fastmem {
using nwii::runtime::MMU;
inline constexpr uintptr_t base=0x20000000u;
__attribute__((always_inline)) inline bool ram(uint32_t physical) {
  return physical<MMU::kMem1Limit || physical-0x10000000u<MMU::kMem2Limit;
}
__attribute__((always_inline)) inline uint8_t read8(MMU&,uint32_t address) {return native_arena::ld8(address);}
__attribute__((always_inline)) inline uint16_t read16(MMU&,uint32_t address) {return native_arena::ld16(address);}
__attribute__((always_inline)) inline uint32_t read32(MMU&,uint32_t address) {return native_arena::ld32(address);}
__attribute__((always_inline)) inline uint64_t read64(MMU&,uint32_t address) {return native_arena::ld64(address);}
__attribute__((always_inline)) inline float read_f32(MMU&,uint32_t address) {return native_arena::ldf32(address);}
__attribute__((always_inline)) inline double read_f64(MMU&,uint32_t address) {return native_arena::ldf64(address);}
}
'''
ALLOCATOR_NEW = '''// NATIVE_GUEST_ARENA_V1: native_static_ram_commit and the fault handler now live in runtime-overlays/native_guest_arena.cpp.
'''
FILE_MARK = re.compile(r'^// ' + MARK + r' FILE ([A-Za-z0-9+/=]*)\n')


def replace_file(path, new):
    old = read_text(path)
    m = FILE_MARK.match(old)
    original = unb64(m[1]) if m else old
    write_text(path, '// ' + MARK + ' FILE ' + b64(original) + NL + new)


def restore_file(path):
    if not path.exists():
        return
    old = read_text(path)
    m = FILE_MARK.match(old)
    if m:
        write_text(path, unb64(m[1]))


GUEST_FAST_WRITES = re.compile(r'__attribute__\(\(always_inline\)\) inline void write8\(MMU& mmu,uint32_t address,uint8_t value\) \{.*?'
                               r'  mmu\.write_f64\(address,value\);\r?\n\}\r?\n', re.S)
GUEST_FAST_NEW = '''// Guest stores are single movbe instructions on the arena. The stack-span test, the watch test and the checked
// MMU::write* fallback are gone: a store the arena cannot take (MMIO, pipe, hole, watched or page-0 word) faults and is
// emulated by the handler with the ordinary MMU slow path.
__attribute__((always_inline)) inline void write8(MMU&,uint32_t address,uint8_t value) {native_arena::st8(address,value);}
__attribute__((always_inline)) inline void write16(MMU&,uint32_t address,uint16_t value) {native_arena::st16(address,value);}
__attribute__((always_inline)) inline void write32(MMU&,uint32_t address,uint32_t value) {native_arena::st32(address,value);}
__attribute__((always_inline)) inline void write64(MMU&,uint32_t address,uint64_t value) {native_arena::st64(address,value);}
__attribute__((always_inline)) inline void write_f32(MMU&,uint32_t address,float value) {native_arena::stf32(address,value);}
__attribute__((always_inline)) inline void write_f64(MMU&,uint32_t address,double value) {native_arena::stf64(address,value);}
'''


def patch_guest_fast(text):
    text = strip_include(restore_blocks(text), 'native_guest_arena_fifo.h')
    text = text.replace('#include "runtime/cpu_context.h"\n', '#include "runtime/cpu_context.h"\n#include "runtime/native_guest_arena_fifo.h" // ' + MARK + NL, 1)
    assert 'native_guest_arena_fifo.h' in text, 'native_guest_fast.h include anchor moved'
    return replace_regex(text, GUEST_FAST_WRITES, GUEST_FAST_NEW, 1, 'native_guest_fast writes')


# ---------------------------------------------------------------------------------------------------------------------------
# CMake
# ---------------------------------------------------------------------------------------------------------------------------
CMAKE_BLOCK = re.compile(r'\n# ' + MARK + r'\n.*?# END_' + MARK + r'\n', re.S)
RAMWATCH_LINE = re.compile(r'(  target_sources\(nwiiruntime PRIVATE "[^"\n]*ram_write_watch\.cpp"\))')
RAMWATCH_OFF = re.compile(r'  # ' + MARK + r' OFF (target_sources\(nwiiruntime PRIVATE "[^"\n]*ram_write_watch\.cpp"\))')


def patch_cmake(text, port, mode):
    text = CMAKE_BLOCK.sub('', text)
    text = RAMWATCH_OFF.sub(lambda m: '  ' + m[1], text)
    assert 'NWII_STATIC_RAM_BASE' in text, 'the arena needs the NWII_STATIC_RAM_BASE build (tools/fix_v13_runtime.py)'
    definitions = {'fixed': '', 'dynamic': ' NWII_ARENA_DYNAMIC_BASE=1', 'mask': ' NWII_ARENA_MASK=1'}[mode]
    if mode != 'mask':
        # MapViewOfFile sections cannot be MEM_WRITE_WATCH: ram_write_watch.cpp is replaced by the arena's own tracker.
        assert RAMWATCH_LINE.search(text), 'ram_write_watch.cpp CMake line moved'
        text = RAMWATCH_LINE.sub(lambda m: '  # ' + MARK + ' OFF ' + m[1].strip(), text)
    source = (port / 'runtime-overlays/native_guest_arena.cpp').as_posix()
    text += ('\n# ' + MARK + '\nif(MINGW AND CMAKE_SIZEOF_VOID_P EQUAL 8)\n  target_sources(nwiiruntime PRIVATE "' + source + '")\n'
             '  target_compile_definitions(nwiiruntime PUBLIC NWII_GUEST_ARENA=1' + definitions + ')\nendif()\n# END_' + MARK + '\n')
    return text


# ---------------------------------------------------------------------------------------------------------------------------
# runtime hooks (kernel reads into guest RAM must not meet a store trap)
# ---------------------------------------------------------------------------------------------------------------------------
def patch_virtual_disc(text):
    text = restore_blocks(text)
    text = strip_include(text, 'native_guest_arena.h')
    old = '        size_t bytes_read = fread(dst + (lo - offset), 1, hi - lo, f);'
    new = ('native_arena::HostWriteScope arena_scope(dst + (lo - offset), hi - lo); // ' + MARK + '\n        ' + old.lstrip())
    text = replace_literal(text, old, new, 1, 'virtual_disc fread')
    first = text.index('#include')
    return text[:first] + '#include "runtime/native_guest_arena.h" // ' + MARK + NL + text[first:]


def patch_savestate(text):
    text = restore_blocks(text)
    old = 'static void native_ss_ram(native_ss::Stream& s, uint8_t* base, size_t bytes) {'
    new = old + '\n  native_arena::HostWriteScope arena_scope(base, s.loading ? bytes : 0); // ' + MARK + ': stream reads reach the kernel'
    return replace_literal(text, old, new, 1, 'native_ss_ram')


# ---------------------------------------------------------------------------------------------------------------------------
# generated code
# ---------------------------------------------------------------------------------------------------------------------------
STORE_NAMES = {'write8': 'st8', 'write16': 'st16', 'write32': 'st32', 'write64': 'st64', 'write_f32': 'stf32', 'write_f64': 'stf64'}
FIFO_NAMES = {'write8': 'fifo8', 'write16': 'fifo16', 'write32': 'fifo32', 'write_f32': 'fifof32', 'write_f64': 'fifof64'}
BACK_NAMES = {v: k for k, v in list(STORE_NAMES.items()) + list(FIFO_NAMES.items())}
MMU_STORE = re.compile(r'ctx\.mmu\.(write8|write16|write32|write64|write_f32|write_f64)\(')
ARENA_BACK = re.compile(r'native_arena::(st8|st16|st32|st64|stf32|stf64|fifo8|fifo16|fifo32|fifof32|fifof64)\(ctx\.mmu, ')
# displacement -32768 from a plain register: the signature of the write-gather pipe (base 0xCC010000)
FIFO_DISP = re.compile(r'(?=(?:g\d+|ctx\.gpr\[\d+\]) \+ -32768,)')
PIPE_CONST = re.compile(r'^\s*((?:g\d+|ctx\.gpr\[\d+\])) = (-872382464|-872349696|0xCC008000u?|0xCC010000u?);')
ASSIGN = re.compile(r'^\s*((?:g\d+|ctx\.gpr\[\d+\])) = ')
PSQ_STORE = re.compile(r'ctx\.(psq_store_val|native_batch_psq_store_zero)\((?=[^;]*?, (?:g\d+|ctx\.gpr\[\d+\]) \+ -32768, )')
# lis rN,0xCC00 ; ori rN,rN,0x8000 builds the pipe address in two steps; psq stores through it are write-gather stores too
PIPE_HIGH = re.compile(r'^\s*((?:g\d+|ctx\.gpr\[\d+\])) = -872415232;')
PIPE_ORI = re.compile(r'^\s*((?:g\d+|ctx\.gpr\[\d+\])) = ((?:g\d+|ctx\.gpr\[\d+\])) \| 32768;')
PSQ_PIPE_STORE = re.compile(r'ctx\.(psq_store_val|native_batch_psq_store_zero)\((?=[^;]*?, ((?:g\d+|ctx\.gpr\[\d+\])) \+ -?\d+, )')
# addi rN, rM, K from a pipe register (e.g. r5 = 0xCC010000 - 32768): the fifo helpers re-check the page at run time
# lis rN,0x8000: stores at a small displacement from it hit physical page 0 (the OS globals), which traps in the arena
LOW_BASE = re.compile(r'^\s*((?:g\d+|ctx\.gpr\[\d+\])) = -2147483648;')
ANY_ASSIGN = re.compile(r'^\s*((?:g\d+|ctx\.gpr\[\d+\])) = ')
LOW_DISP = re.compile(r'((?:g\d+|ctx\.gpr\[\d+\])) \+ (\d+),')
PIPE_ADD = re.compile(r'^\s*((?:g\d+|ctx\.gpr\[\d+\])) = ((?:g\d+|ctx\.gpr\[\d+\])) \+ -?\d+;')
PSQ_REG_STORE = re.compile(r'ctx\.psq_store\((\d+), ((?:g\d+|ctx\.gpr\[\d+\])) \+ -?\d+, ')
PSQ_BACK = re.compile(r'ctx\.(psq_store_fifo_reg|psq_store_fifo|native_batch_psq_store_fifo)\(')
PSQ_FORWARD = {'psq_store_val': 'psq_store_fifo', 'native_batch_psq_store_zero': 'native_batch_psq_store_fifo'}
PSQ_REVERSE = {'psq_store_fifo': 'psq_store_val', 'native_batch_psq_store_fifo': 'native_batch_psq_store_zero', 'psq_store_fifo_reg': 'psq_store'}


def restore_generated(text):
    text = ARENA_BACK.sub(lambda m: 'ctx.mmu.' + BACK_NAMES[m[1]] + '(', text)
    return PSQ_BACK.sub(lambda m: 'ctx.' + PSQ_REVERSE[m[1]] + '(', text)


def slow_functions(port):
    result = set(IO_FUNCTIONS)
    names = []
    if os.environ.get('NWII_ARENA_SLOW_FUNCTIONS'):
        names.append(Path(os.environ['NWII_ARENA_SLOW_FUNCTIONS']))
    names.append(HERE / 'guest_arena_slow_functions.txt')
    for path in names:
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.split('#')[0].strip()
                if line:
                    result.add(int(line, 16))
    return frozenset(result)


def arena_source(text, slow):
    """Rewrite checked guest stores into arena stores, function by function. Hardware-touching functions keep the checked path."""
    text = restore_generated(text)
    out, io, pipe, high_base, low_base = [], False, set(), set(), set()
    changed = False
    for line in text.splitlines(keepends=True):
        match = FUNCTION.match(line)
        if match:
            io = int(match[1], 16) in slow
            pipe = set()
            high_base = set()
            low_base = set()
        if io:
            out.append(line)
            continue
        any_assign = ANY_ASSIGN.match(line)
        if any_assign:
            low_base.discard(any_assign[1])
            if LOW_BASE.match(line):
                low_base.add(any_assign[1])
        constant = PIPE_CONST.match(line)
        high = PIPE_HIGH.match(line)
        ori = PIPE_ORI.match(line)
        if constant:
            pipe.add(constant[1])
        elif ori and ori[1] == ori[2] and ori[1] in high_base:
            pipe.add(ori[1])
        elif PIPE_ADD.match(line) and PIPE_ADD.match(line)[2] in pipe:
            pipe.add(PIPE_ADD.match(line)[1])
            high_base.discard(PIPE_ADD.match(line)[1])
        else:
            assigned = ASSIGN.match(line)
            if assigned:
                pipe.discard(assigned[1])
                high_base.discard(assigned[1])
                if high:
                    high_base.add(high[1])
        if 'ctx.mmu.write' not in line and 'ctx.psq_store_val' not in line and 'ctx.native_batch_psq_store_zero' not in line and 'ctx.psq_store(' not in line:
            out.append(line)
            continue

        def store(m):
            after = line[m.end():]
            low = LOW_DISP.match(after)
            if low and low[1] in low_base and int(low[2]) < 4096:
                return 'ctx.mmu.' + m[1] + '('   # physical page 0: the checked path applies its rules in software, no trap
            fifo = FIFO_DISP.match(after) is not None
            if not fifo and pipe and m[1] in FIFO_NAMES:
                base = re.match(r'((?:g\d+|ctx\.gpr\[\d+\]))(?: \+ 0)?[,)]', after)
                fifo = bool(base and base[1] in pipe)
            return 'native_arena::' + (FIFO_NAMES[m[1]] if fifo else STORE_NAMES[m[1]]) + '(ctx.mmu, '
        new = MMU_STORE.sub(store, line)
        new = PSQ_STORE.sub(lambda m: 'ctx.' + PSQ_FORWARD[m[1]] + '(', new)
        new = PSQ_REG_STORE.sub(lambda m: 'ctx.psq_store_fifo_reg(' + m[1] + ', ' + m[0][m[0].index(m[2]):] if m[2] in pipe else m[0], new)
        new = PSQ_PIPE_STORE.sub(lambda m: 'ctx.' + PSQ_FORWARD[m[1]] + '(' if m[2] in pipe else m[0], new)
        changed |= new != line
        out.append(new)
    return ''.join(out) if changed else text


def generated_files(port, hot_only=False):
    result = []
    for name in (('crash-hot',) if hot_only else ('crash-generated', 'crash-hot', 'crash-split')):
        directory = port / 'build' / name
        if directory.exists():
            result += [p for p in directory.glob('*.cpp') if p.name != 'main_output.cpp']
    return sorted(result)


# ---------------------------------------------------------------------------------------------------------------------------
def apply(port, restore, mode):
    rt = port / 'build/nwii-full-source/nWiiRuntime'
    inc = rt / 'include/runtime'
    cm = rt.parent / 'CMakeLists.txt'
    edits = []

    def edit(path, fn):
        if not path.exists():
            return
        old = read_text(path)
        new = fn(old)
        if new != old:
            edits.append((path, new))

    def cpu(text):
        return strip_include(restore_blocks(text), 'native_guest_arena.h') if restore else patch_cpu_context(text)

    def guest_fast(text):
        if restore:
            return strip_include(restore_blocks(text), 'native_guest_arena_fifo.h')
        return patch_guest_fast(text)

    def cmake(text):
        if restore:
            text = CMAKE_BLOCK.sub('', text)
            return RAMWATCH_OFF.sub(lambda m: '  ' + m[1], text)
        return patch_cmake(text, port, mode)

    def disc(text):
        if restore:
            return strip_include(restore_blocks(text), 'native_guest_arena.h')
        return patch_virtual_disc(text)

    def savestate(text):
        return restore_blocks(text) if restore else patch_savestate(text)

    edit(inc / 'cpu_context.h', cpu)
    edit(inc / 'native_guest_fast.h', guest_fast)
    edit(cm, cmake)
    edit(rt / 'src/hle/virtual_disc.cpp', disc)
    edit(rt / 'src/hle/native_savestate_ios.inc', savestate)
    slow = frozenset() if restore else slow_functions(port)
    counts = dict(files=0)
    # NWII_BUILD_GUEST_ARENA_STORES=hot: only the promoted hot units get arena stores (their MMIO stores already run with a stale
    # ctx by design); the cold chunks keep the checked, out-of-line MMU stores, which flush ctx before a hardware side effect.
    hot_only = os.environ.get('NWII_BUILD_GUEST_ARENA_STORES', 'all') == 'hot'
    hot_files = set(generated_files(port, True))
    for path in generated_files(port):
        old = read_text(path)
        new = restore_generated(old) if restore or (hot_only and path not in hot_files) else arena_source(old, slow)
        if new != old:
            edits.append((path, new))
            counts['files'] += 1
    for path, new in edits:
        write_text(path, new)
    for name in ('native_fastmem.h', 'native_fastmem_allocator.h'):
        path = inc / name
        if path.exists():
            if restore:
                restore_file(path)
            else:
                replace_file(path, FASTMEM_NEW if name == 'native_fastmem.h' else ALLOCATOR_NEW)
    for name in ('native_guest_arena.h', 'native_guest_arena_fifo.h'):
        target = inc / name
        if restore:
            if target.exists():
                target.unlink()
        else:
            data = (port / 'runtime-overlays' / name).read_bytes()
            if not target.exists() or target.read_bytes() != data:
                target.write_bytes(data)
    return counts


def map_sites(report, exe, out):
    """Map a NWII_ARENA_SITES report (host RVAs of faulting accesses) to guest functions through the executable's symbols and write a
    tools/guest_arena_slow_functions.txt candidate: functions that fault often keep the checked accessors on the next build."""
    nm = os.environ.get('NM') or 'nm'
    symbols = []
    for line in subprocess.run([nm, '-n', '--defined-only', str(exe)], capture_output=True, text=True, check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            m = re.search(r'(?:func|native_guest_(?:primary|resume|continuation))_([0-9A-F]{8})', parts[2])
            if m:
                symbols.append((int(parts[0], 16), int(m[1], 16)))
    image = int(subprocess.run(['objdump', '-p', str(exe)], capture_output=True, text=True, check=True).stdout.split('ImageBase')[1].split()[0], 16)
    import bisect
    starts = [s[0] for s in symbols]
    counts = {}
    for line in Path(report).read_text().splitlines():
        if line.startswith('#') or not line.strip():
            continue
        fields = line.split()
        rva, kind, width, address, wg, count = fields[:6]
        index = bisect.bisect_right(starts, image + int(rva, 16)) - 1
        # The host symbol containing the access can be a caller that inlined the guest function whose text does the store, so the
        # guest function the context was in (ctx_pc) is slow too.
        functions = {symbols[index][1]} if index >= 0 else set()
        if len(fields) > 6 and int(fields[6], 16):
            functions.add(int(fields[6], 16))
        for function in functions:
            counts.setdefault(function, [0, 0])
            counts[function][0] += int(count)
            counts[function][1] += int(wg)
    lines = ['# functions whose guest accesses fault (NWII_ARENA_SITES report); they keep the checked accessors', '# address count write_gather_sites']
    for function, (count, wg) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
        lines.append('%08x  # %d faults, %d write-gather' % (function, count, wg))
    Path(out).write_text('\n'.join(lines) + '\n')
    print('mapped %d faulting functions to %s' % (len(counts), out))


def stage_enabled(port):
    """Whether this stage will apply to the staged tree at `port` (the same decision main() makes). tools/fix_guest_mem_proofs.py
    asks it too: with the arena on, the promoted hot units keep their plain accessors so they compile to arena accesses."""
    requested = os.environ.get('NWII_BUILD_GUEST_ARENA')
    if requested == '0' or os.name != 'nt' or os.environ.get('NWII_BUILD_IR_BACKEND', 'cpp') != 'cpp':
        return False
    cmake = port / 'build/nwii-full-source/CMakeLists.txt'
    # on by default only where the fixed-RAM build exists (NWII_BUILD_V13_STATIC_RAM=0 turns that off)
    return requested == '1' or not cmake.exists() or 'NWII_STATIC_RAM_BASE' in read_text(cmake)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--port', type=Path, default=HERE.parent)
    ap.add_argument('--mode', choices=('fixed', 'dynamic', 'mask'), default=os.environ.get('NWII_BUILD_GUEST_ARENA_MODE', 'dynamic'))
    ap.add_argument('--map-sites', nargs=2, metavar=('REPORT', 'EXE'))
    ap.add_argument('--out', type=Path, default=HERE / 'guest_arena_slow_functions.txt')
    args = ap.parse_args()
    if args.map_sites:
        map_sites(args.map_sites[0], args.map_sites[1], args.out)
        return
    enabled = stage_enabled(args.port)
    if not enabled and os.environ.get('NWII_BUILD_GUEST_ARENA') != '0' and os.name == 'nt' and os.environ.get('NWII_BUILD_IR_BACKEND', 'cpp') == 'cpp':
        print('Guest arena needs the NWII_STATIC_RAM_BASE build; staying off.')
    restore = args.restore or not enabled
    # Every edit starts from the restored text of its own file (blocks / markers), so a replay or a layout change heals itself.
    counts = apply(args.port, restore, args.mode)
    print('Guest arena %s (%s)%s.' % ('removed' if restore else 'staged', args.mode, ': %d generated files' % counts['files'] if not restore else ''))


if __name__ == '__main__':
    main()

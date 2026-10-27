// Protected flat guest RAM. Only explicit fixed-width hot loads enter the fault path.
// Other accesses retain the ordinary MMU, including watch and store behavior.
#include "runtime/cpu_context.h"
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
namespace nwii::runtime { extern MMU* g_mmu; namespace {
constexpr uintptr_t kFlat=0x20000000u;
constexpr size_t kFlatBytes=0x40010000u;
bool g_reserved=false;
uint64_t* reg_ptr(CONTEXT* c, unsigned r) {
  switch (r) {
    case 0: return &c->Rax; case 1: return &c->Rcx; case 2: return &c->Rdx; case 3: return &c->Rbx;
    case 4: return &c->Rsp; case 5: return &c->Rbp; case 6: return &c->Rsi; case 7: return &c->Rdi;
    case 8: return &c->R8; case 9: return &c->R9; case 10: return &c->R10; case 11: return &c->R11;
    case 12: return &c->R12; case 13: return &c->R13; case 14: return &c->R14; default: return &c->R15;
  }
}

enum class Kind { None, Load, Store, Other };
struct Access {
  Kind kind = Kind::None;
  unsigned length = 0, reg = 0, width = 0, dest_bits = 0;
  bool movbe = false, sign = false, zero = false, rex = false, has_imm = false;
  uint64_t imm = 0;
  uintptr_t ea = 0;
};

// Byte-sized forms among one-byte opcodes that take a ModRM memory operand.
bool byte_form(uint8_t op) {
  if (op < 0x40 && (op & 7) < 4) return (op & 1) == 0;
  return op == 0x80 || op == 0x82 || op == 0x84 || op == 0x86 || op == 0x88 || op == 0x8A || op == 0xC0 ||
         op == 0xD0 || op == 0xD2 || op == 0xF6 || op == 0xFE;
}
bool one_byte_modrm(uint8_t op) {
  if (op < 0x40 && (op & 7) < 4) return true;
  return op == 0x63 || op == 0x69 || op == 0x6B || (op >= 0x80 && op <= 0x8B) || op == 0xC0 || op == 0xC1 ||
         (op >= 0xD0 && op <= 0xD3) || op == 0xF6 || op == 0xF7 || op == 0xFE || op == 0xFF;
}

bool decode(const uint8_t* ip, CONTEXT* c, Access& out) {
  const uint8_t* p = ip;
  bool opsize = false, addr32 = false; unsigned rex = 0;
  for (int i = 0; i < 6; ++i) {
    if (*p == 0x66) { opsize = true; ++p; continue; }
    if (*p == 0x67) { addr32 = true; ++p; continue; }
    if (*p == 0x2E || *p == 0x3E || *p == 0x26 || *p == 0x36 || *p == 0xF2 || *p == 0xF3) { ++p; continue; }
    break;
  }
  if ((*p & 0xF0) == 0x40) { rex = *p; out.rex = true; ++p; }
  const bool w = rex & 8, r = rex & 4, x = rex & 2, b = rex & 1;
  const unsigned osize = w ? 64 : opsize ? 16 : 32;
  out.dest_bits = osize; out.width = osize / 8;
  if (p[0] == 0x0F && p[1] == 0x38 && p[2] == 0xF0) { out.kind = Kind::Load; out.movbe = true; p += 3; }
  else if (p[0] == 0x0F && (p[1] == 0xB6 || p[1] == 0xB7 || p[1] == 0xBE || p[1] == 0xBF)) {
    out.kind = Kind::Load; out.width = (p[1] & 1) ? 2 : 1; out.sign = p[1] >= 0xBE; out.zero = !out.sign; p += 2;
  } else if (p[0] == 0x0F && (p[1] == 0xAF || (p[1] >= 0x40 && p[1] <= 0x4F))) { out.kind = Kind::Other; p += 2; }
  else if (p[0] == 0x0F && p[1] == 0x38 && p[2] == 0xF1) { out.kind = Kind::Store; out.movbe = true; p += 3; }
  else if (p[0] == 0x89) { out.kind = Kind::Store; p += 1; }
  else if (p[0] == 0x88) { out.kind = Kind::Store; out.width = 1; out.dest_bits = 8; p += 1; }
  else if (p[0] == 0xC7) { out.kind = Kind::Store; out.has_imm = true; p += 1; }
  else if (p[0] == 0xC6) { out.kind = Kind::Store; out.has_imm = true; out.width = 1; out.dest_bits = 8; p += 1; }
  else if (p[0] == 0x8B) { out.kind = Kind::Load; p += 1; }
  else if (p[0] == 0x8A) { out.kind = Kind::Load; out.width = 1; out.dest_bits = 8; p += 1; }
  else if (p[0] == 0x63 && w) { out.kind = Kind::Load; out.width = 4; out.sign = true; p += 1; }
  else if (one_byte_modrm(p[0])) { out.kind = Kind::Other; if (byte_form(p[0])) out.width = 1; p += 1; }
  else return false;
  const uint8_t modrm = *p++;
  const unsigned mod = modrm >> 6, rm = modrm & 7;
  out.reg = ((modrm >> 3) & 7) | (r ? 8 : 0);
  if (mod == 3) return false;
  auto regv = [&](unsigned n) -> uint64_t { const uint64_t v = *reg_ptr(c, n); return addr32 ? (v & 0xFFFFFFFFu) : v; };
  uint64_t ea = 0;
  if (rm == 4) {
    const uint8_t sib = *p++;
    const unsigned scale = 1u << (sib >> 6), index = ((sib >> 3) & 7) | (x ? 8 : 0), base = (sib & 7) | (b ? 8 : 0);
    if (index != 4) ea += regv(index) * scale;
    if ((sib & 7) == 5 && mod == 0) { int32_t d; std::memcpy(&d, p, 4); p += 4; ea += uint64_t(int64_t(d)); }
    else ea += regv(base);
  } else if (rm == 5 && mod == 0) {
    return false;  // RIP-relative: never a guest window access
  } else {
    ea += regv(rm | (b ? 8 : 0));
  }
  if (mod == 1) { ea += uint64_t(int64_t(int8_t(*p))); p += 1; }
  else if (mod == 2) { int32_t d; std::memcpy(&d, p, 4); p += 4; ea += uint64_t(int64_t(d)); }
  if (addr32) ea &= 0xFFFFFFFFu;
  out.ea = uintptr_t(ea);
  if (out.has_imm) {  // mov m, imm: imm8 / imm16 / imm32 (sign-extended for REX.W)
    if (out.width == 1) { out.imm = *p; p += 1; }
    else if (out.width == 2) { uint16_t v; std::memcpy(&v, p, 2); out.imm = v; p += 2; }
    else { int32_t v; std::memcpy(&v, p, 4); out.imm = uint64_t(int64_t(v)); p += 4; }
  }
  out.length = unsigned(p - ip);  // exact only for Kind::Load (no immediates follow those encodings)
  return true;
}

uint64_t slow_value(uint32_t phys, unsigned width) {  // big-endian numeric value, original slow-path semantics
  MMU& m = *g_mmu;
  switch (width) {
    case 1: return m.native_read8_slow(phys);
    case 2: return m.native_read16_slow(phys);
    case 4: return m.native_read32_slow(phys);
    default: return m.native_read64_slow(phys);
  }
}

void write_dest(CONTEXT* c, const Access& l, uint64_t v) {
  if (l.dest_bits == 8) {
    if (!l.rex && l.reg >= 4 && l.reg < 8) { uint64_t* d = reg_ptr(c, l.reg - 4); *d = (*d & ~uint64_t(0xFF00)) | ((v & 0xFF) << 8); }
    else { uint64_t* d = reg_ptr(c, l.reg); *d = (*d & ~uint64_t(0xFF)) | (v & 0xFF); }
  } else if (l.dest_bits == 16) {
    uint64_t* d = reg_ptr(c, l.reg); *d = (*d & ~uint64_t(0xFFFF)) | (v & 0xFFFF);
  } else if (l.dest_bits == 32) {
    *reg_ptr(c, l.reg) = v & 0xFFFFFFFFu;
  } else {
    *reg_ptr(c, l.reg) = v;
  }
}

LONG CALLBACK fastmem_handler(EXCEPTION_POINTERS* ep) {
  const auto* error=ep->ExceptionRecord;
  auto* context=ep->ContextRecord;
  if(error->ExceptionCode!=EXCEPTION_ACCESS_VIOLATION || error->NumberParameters<2 || error->ExceptionInformation[0]!=0 || !g_mmu)
    return EXCEPTION_CONTINUE_SEARCH;
  const uintptr_t fault=error->ExceptionInformation[1];
  if(fault<kFlat || fault>=kFlat+kFlatBytes) return EXCEPTION_CONTINUE_SEARCH;
  Access access;
  if(!decode(reinterpret_cast<const uint8_t*>(context->Rip),context,access) || access.kind!=Kind::Load ||
      access.ea<kFlat || access.ea+access.width>kFlat+kFlatBytes ||
      (access.width!=1 && access.width!=2 && access.width!=4 && access.width!=8)) return EXCEPTION_CONTINUE_SEARCH;
  uint64_t value=slow_value(uint32_t(access.ea-kFlat),access.width);
  if(!access.movbe) {
    if(access.width==2)value=__builtin_bswap16(uint16_t(value));
    else if(access.width==4)value=__builtin_bswap32(uint32_t(value));
    else if(access.width==8)value=__builtin_bswap64(value);
  }
  if(access.sign)value=access.width==1?uint64_t(int64_t(int8_t(value))):access.width==2?uint64_t(int64_t(int16_t(value))):uint64_t(int64_t(int32_t(value)));
  else if(access.zero)value&=access.width==1?0xffu:0xffffu;
  write_dest(context,access,value);
  context->Rip+=access.length;
  return EXCEPTION_CONTINUE_EXECUTION;
}
} // namespace
void native_static_ram_commit(uintptr_t address,size_t bytes) {
  if(!g_reserved) {
    void* window=VirtualAlloc(reinterpret_cast<void*>(kFlat),kFlatBytes,MEM_RESERVE|MEM_WRITE_WATCH,PAGE_NOACCESS);
    if(window!=reinterpret_cast<void*>(kFlat) || !AddVectoredExceptionHandler(1,fastmem_handler)) {
      std::fprintf(stderr,"[Fastmem] cannot reserve protected guest RAM (error %lu).\n",static_cast<unsigned long>(GetLastError()));
      std::abort();
    }
    g_reserved=true;
  }
  if(address<kFlat || bytes>kFlat+kFlatBytes-address || !VirtualAlloc(reinterpret_cast<void*>(address),bytes,MEM_COMMIT,PAGE_READWRITE)) {
    std::fprintf(stderr,"[Fastmem] cannot commit guest RAM (error %lu).\n",static_cast<unsigned long>(GetLastError()));
    std::abort();
  }
}
} // namespace nwii::runtime

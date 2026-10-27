#pragma once
#include "runtime/cpu_context.h"
template<class F>struct NativeLeafCommit {F& flush;~NativeLeafCommit(){flush();}};
template<class F>struct NativeLeafMemory {
 nwii::runtime::MMU& mmu;F& flush;
 inline void observe(uint32_t a){if(__builtin_expect((a&0x3fffffffu)>=mmu.mem1.size()-8 || nwii::runtime::g_watch_addr!=0,0))flush();}
 inline uint8_t read8(uint32_t a){observe(a);return mmu.read8(a);}
 inline uint16_t read16(uint32_t a){observe(a);return mmu.read16(a);}
 inline uint32_t read32(uint32_t a){observe(a);return mmu.read32(a);}
 inline uint64_t read64(uint32_t a){observe(a);return mmu.read64(a);}
 inline float read_f32(uint32_t a){observe(a);return mmu.read_f32(a);}
 inline double read_f64(uint32_t a){observe(a);return mmu.read_f64(a);}
 inline void write8(uint32_t a,uint8_t value){observe(a);mmu.write8(a,value);}
 inline void write16(uint32_t a,uint16_t value){observe(a);mmu.write16(a,value);}
 inline void write32(uint32_t a,uint32_t value){observe(a);mmu.write32(a,value);}
 inline void write64(uint32_t a,uint64_t value){observe(a);mmu.write64(a,value);}
 inline void write_f32(uint32_t a,float value){observe(a);mmu.write_f32(a,value);}
 inline void write_f64(uint32_t a,double value){observe(a);mmu.write_f64(a,value);}
};
struct NativePlainMemory {
 uint8_t* __restrict__ ram;
 inline uint32_t read32(uint32_t a){uint32_t v;memcpy(&v,ram+(a&0x3fffffffu),4);return __builtin_bswap32(v);}
 inline uint64_t read64(uint32_t a){uint64_t v;memcpy(&v,ram+(a&0x3fffffffu),8);return __builtin_bswap64(v);}
 inline float read_f32(uint32_t a){uint32_t v=read32(a);float f;memcpy(&f,&v,4);return f;}
 inline double read_f64(uint32_t a){uint64_t v=read64(a);double f;memcpy(&f,&v,8);return f;}
 inline void write32(uint32_t a,uint32_t v){v=__builtin_bswap32(v);memcpy(ram+(a&0x3fffffffu),&v,4);}
 inline void write64(uint32_t a,uint64_t v){v=__builtin_bswap64(v);memcpy(ram+(a&0x3fffffffu),&v,8);}
 inline void write_f32(uint32_t a,float f){uint32_t v;memcpy(&v,&f,4);write32(a,v);}
 inline void write_f64(uint32_t a,double f){uint64_t v;memcpy(&v,&f,8);write64(a,v);}
};
static inline bool NativeLeafDisabled(){static const bool disabled=std::getenv("NWII_NO_LEAF_KERNELS")!=nullptr;return disabled;}
static inline bool NativeLeafFiniteValue(double x){uint64_t bits;memcpy(&bits,&x,8);return (bits&0x7ff0000000000000ull)!=0x7ff0000000000000ull;}

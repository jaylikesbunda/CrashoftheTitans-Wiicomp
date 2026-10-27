#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace native_prompts {
// Pure3D texture names survive in guest objects, including legacy save states.
// Identify the Wii action by that name, independently of any image or old binding.
class FontSlots {
public:
    struct Bank { const unsigned char* data; size_t size; };
private:
    struct Slot { uint32_t object; unsigned glyph; };
    std::vector<Slot> slots;
    std::array<Bank,2> banks{};
    size_t cursor=0;
    uint64_t retry=0;
    static constexpr const char* names[16]={
        "Titans_v3_48_texture_4","Titans_v3_48_texture_5","Titans_v3_48_texture_6","Titans_v3_48_texture_7",
        "Titans_v3_48_texture_8","Titans_v3_48_texture_9","Titans_v3_48_texture_10","Titans_v3_48_texture_11",
        "Titans_v3_32_texture_2","Titans_v3_32_texture_3","Titans_v3_32_texture_4","Titans_v3_32_texture_5",
        "Titans_v3_32_texture_6","Titans_v3_32_texture_7","Titans_v3_32_texture_8","Titans_v3_32_texture_9"};
    static constexpr uint64_t uid(const char* name) {
        uint64_t value=0;while(*name)value=(value*65599)^static_cast<unsigned char>(*name++);return value;
    }
    static uint32_t be32(const unsigned char* p) {
        return uint32_t(p[0])<<24|uint32_t(p[1])<<16|uint32_t(p[2])<<8|p[3];
    }
    const unsigned char* ptr(uint32_t address,size_t bytes) const {
        const uint32_t physical=address&0x3fffffffu;
        const unsigned bank=physical>=0x10000000u;
        const size_t off=physical-(bank?0x10000000u:0u);
        return off<=banks[bank].size && bytes<=banks[bank].size-off?banks[bank].data+off:nullptr;
    }
    bool object(const Slot& slot,uint32_t& address,uint32_t& format) const {
        const auto* p=ptr(slot.object,0x2c);if(!p)return false;
        const uint64_t id=uint64_t(be32(p+8))<<32|be32(p+12);
        if(id!=uid(names[slot.glyph]))return false;
        const auto* vt=ptr(be32(p),12);if(!vt)return false;
        const uint32_t method=be32(vt+8);
        if(method<0x80004000u || method>=0x80480000u)return false;
        const auto* texture=ptr(be32(p+0x18),0x8c);if(!texture)return false;
        if(be32(texture+0x3c)!=32 || be32(texture+0x40)!=32)return false;
        format=be32(texture+0x54);address=be32(texture+0x64)&0x3fffffffu;
        const unsigned bytes=format==8?512:format==9?1024:format==10?2048:format==6?4096:0;
        return bytes && ptr(address,bytes);
    }
    void scan(uint64_t now) {
        const size_t total=banks[0].size+banks[1].size;
        if(cursor>=total) {
            if(now<retry)return;
            cursor=0;slots.clear();
        }
        // Bound discovery work per lookup instead of scanning all guest RAM in one frame.
        const size_t end=cursor+4*1024*1024<total?cursor+4*1024*1024:total;
        while(cursor<end) {
            const unsigned bank=cursor>=banks[0].size;
            const size_t off=cursor-(bank?banks[0].size:0);cursor+=4;
            if(off+0x2c>banks[bank].size)continue;
            const auto* p=banks[bank].data+off;
            const uint32_t high=be32(p+8);
            if(high!=uint32_t(uid(names[0])>>32) && high!=uint32_t(uid(names[6])>>32) && high!=uint32_t(uid(names[8])>>32))continue;
            const uint64_t id=uint64_t(high)<<32|be32(p+12);
            for(unsigned i=0;i<16;++i)if(id==uid(names[i])) {
                Slot slot{uint32_t((bank?0x90000000u:0x80000000u)+off),i};uint32_t address,format;
                if(slots.size()<128 && object(slot,address,format))slots.push_back(slot);
            }
        }
        if(cursor>=total)retry=now+1000;
    }
public:
    void clear(){slots.clear();cursor=0;retry=0;banks={};}
    int lookup(Bank mem1,Bank mem2,uint32_t address,unsigned format,uint64_t now) {
        if(banks[0].data!=mem1.data || banks[1].data!=mem2.data || banks[0].size!=mem1.size || banks[1].size!=mem2.size)clear();
        banks={mem1,mem2};
        unsigned seen=0;bool invalid=false;
        for(const auto& slot:slots){uint32_t source,encoding;
            if(!object(slot,source,encoding)){invalid=true;continue;}
            seen|=1u<<slot.glyph;
            if(source==(address&0x3fffffffu) && encoding==format)return int(slot.glyph+16);
        }
        if(invalid){clear();banks={mem1,mem2};}
        if(seen!=0xffffu)scan(now);
        for(const auto& slot:slots){uint32_t source,encoding;
            if(object(slot,source,encoding) && source==(address&0x3fffffffu) && encoding==format)return int(slot.glyph+16);
        }
        return -1;
    }
};
}

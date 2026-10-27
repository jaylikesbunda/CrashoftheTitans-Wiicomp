#include "native_prompt_slots.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
using Bytes=std::vector<unsigned char>;
static void put(Bytes& b,unsigned p,uint32_t v){for(int i=3;i>=0;--i){b[p+i]=v;v>>=8;}}
static uint64_t uid(const std::string& s){uint64_t v=0;for(unsigned char c:s)v=(v*65599)^c;return v;}
int main(int argc,char** argv){
    Bytes m1(0x10000),m2(0x10000);native_prompts::FontSlots slots;
    put(m1,0x108,0x80008000); // Valid texture class virtual method.
    for(unsigned i=0;i<16;++i){
        const unsigned o=0x1000+i*0x100,t=o+0x30;
        const auto name=std::string(i<8?"Titans_v3_48_texture_":"Titans_v3_32_texture_")+std::to_string(i<8?i+4:i-6);
        const auto id=uid(name);put(m2,o,0x80000100);put(m2,o+8,uint32_t(id>>32));put(m2,o+12,uint32_t(id));put(m2,o+0x18,0x90000000+t);
        put(m2,t+0x3c,32);put(m2,t+0x40,32);put(m2,t+0x54,9);put(m2,t+0x64,0x90008000+i*1024);
    }
    auto lookup=[&](unsigned address,unsigned format=9){return slots.lookup({m1.data(),m1.size()},{m2.data(),m2.size()},address,format,1000);};
    for(unsigned i=0;i<16;++i)assert(lookup(0x10008000+i*1024)==int(i+16));
    // Pixels, palettes, old controller family and old bindings cannot affect identity.
    for(unsigned i=0x8000;i<m2.size();++i)m2[i]=static_cast<unsigned char>(i*37);
    for(unsigned i=0;i<16;++i)assert(lookup(0x90008000+i*1024)==int(i+16));
    put(m2,0x1294,0x80004000); // A relocated B texture follows its object's live pointer.
    assert(lookup(0x80004000)==18);assert(lookup(0x10008800)==-1);
    assert(lookup(0x80004000,6)==-1);
    put(m2,0x1208,0);assert(lookup(0x80004000)==-1); // Reused/freed object cannot retain a stale identity.
    slots.clear();put(m2,0x1030+0x3c,64);assert(lookup(0x10008000)==-1);
    std::cout<<"All 16 slots: arbitrary pixels, relocation, bank aliases, stale objects and invalid shapes passed\n";
    if(argc==3){
        auto load=[](const char* p){std::ifstream f(p,std::ios::binary);assert(f);return Bytes(std::istreambuf_iterator<char>(f),{});};
        m1=load(argv[1]);m2=load(argv[2]);slots.clear();int glyph=-1;
        for(unsigned n=0;n<64 && glyph<0;++n)glyph=lookup(0x10d03160);
        assert(glyph==26); // Captured legacy save: named small-font B slot, regardless of saved mouse image.
        std::cout<<"Captured legacy save resolves the actual mouse texture by font identity\n";
    }
}

#pragma once
#include "runtime/native_prompt_device.h"
#include "runtime/native_gl_texture_shadow.h"
#include <array>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <filesystem>
#include <chrono>
#include <iostream>
#include <unordered_map>
#include <vector>

namespace native_prompts {
struct Glyph {
    std::array<unsigned char,4096> original{}; // decoded RGBA, independent of guest texture encoding
    std::array<std::array<unsigned char,4096>,3> pixels{};
    std::array<std::array<GLuint,3>,2> textures{};
    bool observed=false;
};
class Textures {
    std::vector<Glyph> glyphs;
    std::filesystem::file_time_type pack_time{};
    std::chrono::steady_clock::time_point next_check{};
    struct Candidate {
        unsigned format=~0u, palette_format=~0u;
        std::vector<unsigned char> source,palette;
        int glyph=-2;
    };
    std::unordered_map<unsigned,Candidate> candidates;
    static bool font_pixels_match(const unsigned char* original,const unsigned char* rgba,unsigned opaque_alpha) {
        // The game flips PNG rows and quantizes its C8 font palette to RGB5A3.
        // Accept exact decoded values of either RGB5A3 opaque-alpha convention.
        for(unsigned y=0;y<32;++y)for(unsigned x=0;x<32;++x) {
            const unsigned p=(y*32+x)*4,q=((31-y)*32+x)*4;
            const bool opaque=original[q+3]>=opaque_alpha;
            unsigned alpha=255;
            if(!opaque){unsigned v=original[q+3]>>5;alpha=(v<<5)|(v<<2)|(v>>1);}
            if(rgba[p+3]!=alpha)return false;
            for(unsigned c=0;c<3;++c){
                const unsigned v=original[q+c]>>(opaque?3:4);
                const unsigned value=opaque?((v<<3)|(v>>2)):((v<<4)|v);
                if(rgba[p+c]!=value)return false;
            }
        }
        return true;
    }
    static std::vector<Glyph> read_pack() {
        const char* path = std::getenv("NWII_PROMPT_PACK");
        if (!path || !*path) return {};
        std::ifstream file(path,std::ios::binary);
        char magic[8]{};
        file.read(magic,8);
        if (!file || std::memcmp(magic,"WCPRMT01",8)) return {};
        // Fixed-size format: exactly sixteen originals and three replacement families each.
        std::vector<Glyph> loaded(16);
        for (auto& g : loaded) {
            file.read(reinterpret_cast<char*>(g.original.data()),4096);
            for (auto& p : g.pixels) file.read(reinterpret_cast<char*>(p.data()),4096);
            // The pack stores the original in a canonical tiled RGBA8 layout.
            // Compare decoded pixels so IA8, paletted and other guest encodings work too.
            const auto tiled=g.original;
            for(unsigned y=0;y<32;++y)for(unsigned x=0;x<32;++x) {
                const unsigned p=(y*32+x)*4,t=((y/4)*8+x/4)*64+((y%4)*4+x%4)*2;
                g.original[p]=tiled[t+1];g.original[p+1]=tiled[t+32];g.original[p+2]=tiled[t+33];g.original[p+3]=tiled[t];
            }
        }
        if (!file || file.peek()!=std::char_traits<char>::eof()) return {};
        return loaded;
    }
    void refresh() {
        const auto now=std::chrono::steady_clock::now();if(now<next_check)return;
        next_check=now+std::chrono::milliseconds(250);
        const char* path=std::getenv("NWII_PROMPT_PACK");if(!path || !*path)return;
        std::error_code error;const auto modified=std::filesystem::last_write_time(path,error);
        if(error || modified==pack_time)return;
        auto loaded=read_pack();if(loaded.empty())return; // Retain the last complete pack on a failed update.
        clear();glyphs=std::move(loaded);pack_time=modified;
    }
public:
    bool enabled() const { return !glyphs.empty(); }
    Textures() {
        refresh();
        if(!glyphs.empty())std::cout << "[Prompts] live pack loaded: 16 original glyphs" << std::endl;
    }
    void clear() {
        for (auto& g : glyphs) for(auto& orientation:g.textures) for (auto& t : orientation) {
            if(t) NativeTextureDelete(1,&t);
            t=0;
        }
        candidates.clear();
    }
    // -2 requests one decode, -1 is an ordinary texture, >=0 identifies a glyph.
    // Hits and misses are cached. Reused RAM and changed palettes require a fresh decode.
    int probe(unsigned address,unsigned format,unsigned palette_format,const unsigned char* source,size_t bytes,
              const unsigned char* palette,size_t palette_bytes) {
        if(glyphs.empty() || !source) return -1;
        if(candidates.size()>512) candidates.clear();
        auto& c=candidates[address];
        if(c.format==format && c.palette_format==palette_format && c.source.size()==bytes && c.palette.size()==palette_bytes &&
            !std::memcmp(c.source.data(),source,bytes) && (!palette_bytes || !std::memcmp(c.palette.data(),palette,palette_bytes))) return c.glyph;
        c.format=format;c.palette_format=palette_format;c.source.assign(source,source+bytes);
        c.palette.clear();if(palette_bytes)c.palette.assign(palette,palette+palette_bytes);
        c.glyph=-2;
        return -2;
    }
    int record(unsigned address,const unsigned char* rgba) {
        auto& c=candidates.at(address);c.glyph=-1;
        if(!rgba)return -1;
        for(size_t i=0;i<glyphs.size();++i) {
            const bool direct=!std::memcmp(rgba,glyphs[i].original.data(),4096);
            const bool paletted=(c.format==8 || c.format==9 || c.format==10) && c.palette_format==2;
            const bool flipped=paletted && (font_pixels_match(glyphs[i].original.data(),rgba,224) || font_pixels_match(glyphs[i].original.data(),rgba,255));
            if(direct || flipped) {
                c.glyph=static_cast<int>(i+(direct?0:glyphs.size()));
                if(!glyphs[i].observed) {glyphs[i].observed=true;std::cout<<"[Prompts] matched original glyph "<<i<<std::endl;}
                break;
            }
        }
        return c.glyph;
    }
    GLuint texture(int glyph) {
        refresh();
        if(glyph<0 || static_cast<size_t>(glyph)>=glyphs.size()*2)return 0;
        const unsigned flip=static_cast<size_t>(glyph)>=glyphs.size()?1:0;
        auto& g=glyphs[glyph%glyphs.size()];
        const unsigned selected=device.load(std::memory_order_relaxed);
        if(selected==3)return 0; // Explicit Wii prompts use the original guest texture.
        const unsigned family=selected<3?selected:2;
        auto& tex=g.textures[flip][family];
        if (!tex) {
            NativeTextureGenerate(1,&tex);
            NativeTextureBind(GL_TEXTURE_2D,tex);
            std::array<unsigned char,4096> flipped{};
            if(flip)for(unsigned y=0;y<32;++y)std::memcpy(flipped.data()+y*128,g.pixels[family].data()+(31-y)*128,128);
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,32,32,0,GL_RGBA,GL_UNSIGNED_BYTE,flip?flipped.data():g.pixels[family].data());
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
            glGenerateMipmap(GL_TEXTURE_2D);
        }
        return tex;
    }
};
inline Textures& textures() { static Textures value; return value; }
}

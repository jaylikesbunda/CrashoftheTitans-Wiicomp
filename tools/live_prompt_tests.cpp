#include "native_prompt_textures.h"
#include <cassert>
#include <iostream>
#include <thread>

int main(int argc,char** argv) {
    native_prompts::Activity activity;
    assert(activity.update(0,1000));
    assert(!activity.update(1,1100));
    assert(activity.update(3,1300)); // delayed solitary input is retained through the debounce
    assert(!activity.update(1,1600));
    assert(activity.select(2,1601)); // Unplug/mode change bypasses input debounce.
    assert(activity.selected==2 && !activity.update(3,1700));
    assert(activity.select(1,1701)); // Reconnect another controller family.
    native_prompts::Textures textures;
    std::array<unsigned char,4096> source{};
    source.fill(1);
    if(argc>1 && !std::strcmp(argv[1],"native")) {
        std::array<unsigned char,4096> rgba{};
        for(unsigned p=0;p<4096;p+=4){rgba[p]=102;rgba[p+1]=153;rgba[p+2]=204;rgba[p+3]=146;}
        assert(textures.probe(1,9,2,source.data(),source.size(),nullptr,0)==-2);
        assert(textures.record(1,rgba.data())==16);
        assert(textures.texture(16));
        assert(uploaded_pixel==151 && uploaded_last_pixel==120); // replacement rows follow the game's flipped UVs
        rgba[4095]=145;
        assert(textures.record(1,rgba.data())==-1); // quantized alpha still must match exactly
        rgba[4095]=146;
        assert(textures.probe(2,9,1,source.data(),source.size(),nullptr,0)==-2);
        assert(textures.record(2,rgba.data())==-1); // another TLUT format must not match RGB5A3
        std::cout<<"RGB5A3 font matching, row orientation and alpha rejection passed\n";
        return 0;
    }
    auto lookup=[&]() {
        int glyph=textures.probe(1,6,0,source.data(),source.size(),nullptr,0);
        if(glyph==-2)glyph=textures.record(1,source.data());
        return textures.texture(glyph);
    };
    const auto first=lookup();
    if(argc==1) { assert(!first); std::cout<<"disabled/malformed pack passed\n"; return 0; }
    assert(first);
    assert(uploaded_pixel==121);
    if(!std::strcmp(argv[1],"reload")) {
        const char* path=std::getenv("NWII_PROMPT_PACK");
        std::ifstream file(path,std::ios::binary);
        std::vector<unsigned char> pack(std::istreambuf_iterator<char>(file),{});file.close();
        pack[8+3*4096]=201;
        {std::ofstream out(path,std::ios::binary);out.write(reinterpret_cast<const char*>(pack.data()),pack.size());}
        std::filesystem::last_write_time(path,std::filesystem::last_write_time(path)+std::chrono::seconds(1));
        std::this_thread::sleep_for(std::chrono::milliseconds(270));
        const auto remapped=textures.texture(0);assert(remapped && remapped!=first && uploaded_pixel==201);
        {std::ofstream out(path,std::ios::binary);out<<"incomplete";}
        std::this_thread::sleep_for(std::chrono::milliseconds(270));
        assert(textures.texture(0)==remapped); // Failed publication retains the complete previous mapping.
        std::cout<<"Live binding pack reload and incomplete-update recovery passed\n";return 0;
    }
    assert(lookup()==first);
    native_prompts::device.store(0);
    const auto xbox=lookup();
    assert(xbox && xbox!=first);
    assert(uploaded_pixel==81);
    native_prompts::device.store(1);
    assert(lookup()!=xbox);
    assert(uploaded_pixel==101);
    for(const auto byte:source) assert(byte==1); // source stays byte-identical
    native_prompts::device.store(2);
    assert(lookup()==first);
    native_prompts::device.store(3);
    assert(!lookup()); // Wii policy keeps original glyphs without a restart.
    native_prompts::device.store(2);
    source[4095]=2;
    assert(!lookup()); // full comparison, including the final byte
    source[4095]=1;
    textures.clear();
    assert(lookup()!=first);
    unsigned char palette[]={1,2};
    assert(textures.probe(2,8,0,source.data(),source.size(),palette,2)==-2);
    assert(textures.record(2,source.data())==0);
    assert(textures.probe(2,8,0,source.data(),source.size(),palette,2)==0);
    palette[1]=3;
    assert(textures.probe(2,8,0,source.data(),source.size(),palette,2)==-2);
    assert(textures.record(2,source.data())==0);
    assert(textures.probe(2,8,1,source.data(),source.size(),palette,2)==-2);
    std::cout<<"family switching, exact match, reuse, cleanup and debounce passed\n";
}

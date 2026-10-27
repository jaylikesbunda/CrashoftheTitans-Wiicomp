#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace native_palette_decode {
template<class Color> inline Color Palette(const uint8_t* data,size_t bytes,unsigned index,unsigned format) {
  const size_t offset=size_t(index)*2;
  if(!data || offset+1>=bytes)return Color{255,0,255,255};
  const unsigned v=(unsigned(data[offset])<<8)|data[offset+1];
  auto c5=[](unsigned n){return uint8_t((n<<3)|(n>>2));};
  auto c4=[](unsigned n){return uint8_t((n<<4)|n);};
  if(format==0)return Color{uint8_t(v),uint8_t(v),uint8_t(v),uint8_t(v>>8)};
  if(format==1)return Color{c5((v>>11)&31),uint8_t((((v>>5)&63)<<2)|(((v>>5)&63)>>4)),c5(v&31),255};
  if(v&0x8000)return Color{c5((v>>10)&31),c5((v>>5)&31),c5(v&31),255};
  const unsigned a=(v>>12)&7;
  return Color{c4((v>>8)&15),c4((v>>4)&15),c4(v&15),uint8_t((a<<5)|(a<<2)|(a>>1))};
}

// Source spans are validated once. Each tile is read in storage order, with
// C4/C8 palette expansion shared by every pixel of this decode.
template<class Color> inline bool Decode(const uint8_t* source,size_t bytes,unsigned width,unsigned height,
    unsigned format,const uint8_t* palette,size_t palette_bytes,unsigned palette_format,Color* output) {
  if(format!=8 && format!=9 && format!=10)return false;
  const unsigned tw=format==10?4:8,th=format==8?8:4;
  const size_t columns=(size_t(width)+tw-1)/tw,rows=(size_t(height)+th-1)/th;
  if(columns && rows>SIZE_MAX/32/columns)return false;
  if(!source || columns*rows*32>bytes || (width && height && !output))return false;
  std::array<Color,256> expanded;
  if(format!=10)for(unsigned i=0;i<(format==8?16u:256u);++i)expanded[i]=Palette<Color>(palette,palette_bytes,i,palette_format);
  for(unsigned y=0;y<height;y+=th)for(unsigned x=0;x<width;x+=tw){
    const uint8_t* tile=source+((size_t(y)/th)*columns+x/tw)*32;
    for(unsigned iy=0;iy<std::min(th,height-y);++iy){
      Color* row=output+size_t(y+iy)*width+x;
      for(unsigned ix=0;ix<std::min(tw,width-x);++ix){
        unsigned index;
        if(format==8){const unsigned v=tile[iy*4+ix/2];index=ix&1?v&15:v>>4;}
        else if(format==9)index=tile[iy*8+ix];
        else {const unsigned offset=iy*8+ix*2;index=((unsigned(tile[offset])<<8)|tile[offset+1])&0x3fff;}
        row[ix]=format==10?Palette<Color>(palette,palette_bytes,index,palette_format):expanded[index];
      }
    }
  }
  return true;
}
}

"""Retain secondary vertex inputs and evaluate independent regular ST/STQ texgens."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
stage=PORT/'build/nwii-full-source/nWiiRuntime'
def patch(relative,old,new):
    p=stage/relative;s=p.read_text()
    if new in s:return
    assert s.count(old)==1,(relative,old[:80]);p.write_text(s.replace(old,new,1))
if '    float color1[4]' not in (stage/'include/runtime/gx_command.h').read_text():
    patch('include/runtime/gx_command.h','    float color[4];','    float color[4];\n    float color1[4] = {1,1,1,1};\n    float object_pos[3]{}, object_norm[3]{};')
patch('include/runtime/gx_command.h','    bool has_color = false;','    bool has_color = false;\n    bool has_color1 = false;')
for declaration in ('float pos[3];','float norm[3];','float color[4];','float tex[8][2];'):
    patch('include/runtime/gx_command.h',declaration,declaration[:-1]+'{};')
patch('include/runtime/gx_state.h','  float normalMatrices[96];','  float normalMatrices[96];\n  float postMatrices[256]{};')
p=stage/'src/hle/gx/fifo_parser.cpp';s=p.read_text()
if '// NATIVE_SECONDARY_INPUTS' not in s:
    a=s.index('        off += AttrBytes(r.vat.clrMask[1]')
    b=s.index('        if (off > buf_size)',a)
    s=s[:a]+'''        // NATIVE_SECONDARY_INPUTS
        if (r.vat.clrMask[1] != VtxAttrMask::None) {
            if (!ReadColorAttr(buf,off,buf_size,r.vat.clrMask[1],r.vat.clrType[1],r.arrayBase[3],r.arrayStride[3],vtx.color1)) return;
            vtx.has_color1 = true;
        }
        for (unsigned t=0;t<8;++t) if(r.vat.texMask[t]!=VtxAttrMask::None) {
            if (!ReadVectorAttr(buf,off,buf_size,r.vat.texMask[t],r.vat.texType[t],r.vat.texShift[t],r.arrayBase[4+t],r.arrayStride[4+t],r.vat.texElements[t]?2:1,vtx.tex[t])) return;
            vtx.has_tex[t]=true;
        }
        for(unsigned j=0;j<3;++j) {vtx.object_pos[j]=vtx.pos[j];vtx.object_norm[j]=vtx.has_norm?vtx.norm[j]:0;}
'''+s[b:];p.write_text(s)
patch('src/hle/gx/renderer_gl.cpp','#include <vector>','#include <vector>\n#include <cmath>')
patch('src/hle/gx/renderer_gl.cpp','  float nx, ny, nz;','  float nx, ny, nz;\n  float color1[4];\n  float texgen[8][3];')
p=stage/'src/hle/gx/renderer_gl.cpp';s=p.read_text()
if '// NATIVE_TEXGEN_V1' in s:
    a=s.index('// NATIVE_TEXGEN_V1')
    b=min(s.index(anchor,a) for anchor in ('static void NativeWriteConvertedVertex(', 'static BatchVertex ConvertVertex(', 'static void EmitVertex(') if anchor in s[a:])
    s=s[:a]+(PORT/'runtime-overlays/texgen.inc').read_text()+'\n'+s[b:]
    p.write_text(s)
if '// NATIVE_TEXGEN_V1' not in s:
    needle='static void EmitVertex(const VertexData &vtx) {'
    s=s.replace(needle,(PORT/'runtime-overlays/texgen.inc').read_text()+'\n'+needle,1)
    a=s.index('  if (vtx.has_tex[0]) {',s.index(needle));b=s.index('\n  float x = vtx.pos[0]',a)
    s=s[:a]+'''  std::memcpy(bv.color1,vtx.color1,sizeof(bv.color1));
  NativeTexGen(g_state,vtx,bv.texgen);
  const float q=bv.texgen[0][2];
  bv.u=bv.texgen[0][0]/(q==0?1:q);bv.v=bv.texgen[0][1]/(q==0?1:q);
'''+s[b:]
    needle='                          (void *)offsetof(BatchVertex, nx));'
    assert s.count(needle)==1
    s=s.replace(needle,needle+'''
    glVertexAttribPointer(4,4,GL_FLOAT,GL_FALSE,sizeof(BatchVertex),(void*)offsetof(BatchVertex,color1));
    glEnableVertexAttribArray(4);
    for(unsigned t=0;t<8;++t) {
      glEnableVertexAttribArray(5+t);
      glVertexAttribPointer(5+t,3,GL_FLOAT,GL_FALSE,sizeof(BatchVertex),(void*)(offsetof(BatchVertex,texgen)+t*12));
    }''',1)
    s=s.replace('word(0x44524131); // DRA1, little endian host format','word(0x44524132); // DRA2, 40-float vertices; DRA1 archives remain readable')
    s=s.replace('static_assert(sizeof(BatchVertex)==48','static_assert(sizeof(BatchVertex)==160')
    needle='          } else if (current_addr >= 0x0600 && current_addr <= 0x067F) {'
    assert s.count(needle)==1
    s=s.replace(needle,'          } else if (current_addr >= 0x0500 && current_addr < 0x0600) {\n            g_state.postMatrices[current_addr-0x0500]=cmd.payload[i];\n'+needle,1)
    p.write_text(s)
p=stage/'src/hle/gx/tev_shader_gen.cpp';s=p.read_text()
if '// NATIVE_GENERATED_TEXCOORD_INPUTS' not in s:
    needle='    vs << "layout(location = 3) in vec3 vertexNormal;\\n";'
    assert s.count(needle)==1
    s=s.replace(needle,needle+'''
    // NATIVE_GENERATED_TEXCOORD_INPUTS
    vs << "layout(location = 4) in vec4 vertexColor1;\\n";
    for(unsigned t=0;t<8;++t) vs << "layout(location = " << 5+t << ") in vec3 generatedTex" << t << ";\\n";
''')
    s=s.replace('in vec2 vTex','in vec3 vTex').replace('out vec2 vTex','out vec3 vTex')
    s=s.replace('dolphin_chan(1, vertexPosition, vertexNormal, vertexColor)','dolphin_chan(1, vertexPosition, vertexNormal, vertexColor1)')
    a=s.index('    for (int i = 0; i < 8; ++i)\n        vs << "    vTex"')
    b=s.index('    vs << "}\\n";',a)
    s=s[:a]+'''    for (unsigned i=0;i<8;++i) {
        const unsigned type=(state.xf[0x40+i]>>4)&7;
        if(type==2 || type==3) vs << "    vTex" << i << " = vec3(vColor" << type-2 << ".rg,1.0);\\n";
        else vs << "    vTex" << i << " = generatedTex" << i << ";\\n";
    }
'''+s[b:]
    s=s.replace('<< (int)stage.texCoord << ");\\n";','<< (int)stage.texCoord << ".xy / vTex" << (int)stage.texCoord << ".z);\\n";')
    p.write_text(s)
print('Secondary colour/eight UVs, independent regular matrices, post matrices and STQ interpolation staged.')

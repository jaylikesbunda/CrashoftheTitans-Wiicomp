"""Check live prompt staging and production pack/cache logic without a GPU or game files."""
from pathlib import Path
import os, shutil, subprocess, tempfile
from fix_live_prompts import patch, ANCHOR
from fix_input_mapping import apply_edits, EDITS

PORT=Path(__file__).resolve().parents[1]

def main():
    original={key:''.join(anchor for k,anchor,_,_ in EDITS if k==key) for key in ('header','wpad')}
    mapped,count=apply_edits(original)
    assert count==len(EDITS)
    assert apply_edits(mapped)==(mapped,0)
    coop=dict(mapped)
    coop['wpad']=coop['wpad'].replace(EDITS[2][2],EDITS[2][2].replace('s.','c.'))+'\n// NATIVE_WPAD_COOP_V1\n'
    coop['wpad']=coop['wpad'].replace('reported by 803EC364','reported by 00000000')
    assert apply_edits(coop)==(coop,0), 'co-op/region staging must still refresh SDL mappings'
    source='void TextureCache::clear() {\n}\n'+ANCHOR+'\n'
    assert patch(patch(source))==patch(source)
    try: patch('moved anchor')
    except AssertionError: pass
    else: raise AssertionError('missing anchor accepted')
    compiler=shutil.which('g++')
    if not compiler:
        from port_paths import winlibs_bin
        compiler=str(winlibs_bin()/'g++.exe')
    with tempfile.TemporaryDirectory(prefix='live-prompts-') as directory:
        root=Path(directory); (root/'runtime').mkdir()
        shutil.copy2(PORT/'runtime-overlays/native_prompt_device.h',root/'runtime/native_prompt_device.h')
        (root/'runtime/native_gl_texture_shadow.h').write_text('''#pragma once
using GLuint=unsigned;
constexpr unsigned GL_TEXTURE_2D=1,GL_RGBA=2,GL_UNSIGNED_BYTE=3,GL_TEXTURE_MIN_FILTER=4,GL_TEXTURE_MAG_FILTER=5,GL_NEAREST=6;
inline unsigned next_texture=0;
inline unsigned uploaded_pixel=0;
inline unsigned uploaded_last_pixel=0;
inline void NativeTextureGenerate(int,GLuint* t){*t=++next_texture;}
inline void NativeTextureDelete(int,GLuint*){}
inline void NativeTextureBind(unsigned,GLuint){}
inline void glTexImage2D(unsigned,int,unsigned,int,int,int,unsigned,unsigned,const void* p){uploaded_pixel=*static_cast<const unsigned char*>(p);uploaded_last_pixel=static_cast<const unsigned char*>(p)[4096-128];}
inline void glTexParameteri(unsigned,unsigned,unsigned){}
inline void glGenerateMipmap(unsigned){}
''',encoding='utf-8')
        exe=root/'check.exe'
        subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-I',str(root),'-I',str(PORT/'runtime-overlays'),str(PORT/'tools/live_prompt_tests.cpp'),'-o',str(exe)],check=True)
        env=os.environ.copy();env.pop('NWII_PROMPT_PACK',None)
        subprocess.run([str(exe)],env=env,check=True)
        pack=root/'pack.bin'; env['NWII_PROMPT_PACK']=str(pack)
        tiled=(bytes([128,100])*16+bytes([150,200])*16)*64
        pixels=b''.join(b''.join(bytes([80+20*f+y,0,0,255])*32 for y in range(32)) for f in range(3))
        pack.write_bytes(b'WCPRMT01'+(tiled+pixels)*16)
        subprocess.run([str(exe),'native'],env=env,check=True)
        pack.write_bytes(b'WCPRMT01'+b''.join(bytes([i])*4096+b''.join(bytes([80+20*f+i])*4096 for f in range(3)) for i in range(1,17)))
        subprocess.run([str(exe),'valid'],env=env,check=True)
        subprocess.run([str(exe),'reload'],env=env,check=True)
        pack.write_bytes(pack.read_bytes()[:-1])
        subprocess.run([str(exe)],env=env,check=True)
        pack.write_bytes(b'WCPRMT01'+bytes(16*16384)+b'extra')
        subprocess.run([str(exe)],env=env,check=True)
    print('Live prompt checks passed.')

if __name__=='__main__': main()

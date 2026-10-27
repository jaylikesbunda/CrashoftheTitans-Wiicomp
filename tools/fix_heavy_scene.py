"""Preserve draw ordering while avoiding parser-only records and repeated work."""
from pathlib import Path
import argparse,base64,re,shutil
MARK='NATIVE_HEAVY_SCENE_V1'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)

def restore(text):
    return BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),text)

def replace(text,old,new):
    assert text.count(old)==1,('Heavy-scene anchor',old[:100],text.count(old))
    return text.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */',1)

def parser(text):
    text=restore(text)
    old='static inline void EmitCp(CommandStream& commands,uint8_t reg,uint32_t val,native_gx_work::ConstructionBatch& stats){\n if(stats.active)++stats.values[1];'
    text=replace(text,old,old.replace('& stats){','& stats,bool retain){')+'\n if(!retain)return; // CP was applied by ParseCP; the renderer does not consume it.')
    for value in ('val','merged'):
        old='EmitCp(commands, reg, '+value+',native_constructed);'
        text=replace(text,old,old.replace('native_constructed);','native_constructed,depth!=0);'))
    old='            for (const StreamEntry& e0 : native_cached.entries) {'
    text=replace(text,old,old+'''\n                if(depth==0 && e0.kind==uint8_t(GXCommandType::CPRegister)){
                    ParseCP(uint8_t(e0.reg),e0.val);continue;
                }''')
    old='        s_dl_cache[key] = {sh, ch, local};'
    text=replace(text,old,old+'''\n        // Cache the complete replay first; only the final render output loses CP.
        if(depth==0)local.entries.erase(std::remove_if(local.entries.begin(),local.entries.end(),
            [](const StreamEntry& e){return e.kind==uint8_t(GXCommandType::CPRegister);}),local.entries.end());''')
    return text

def renderer(text):
    text=restore(text)
    anchor='#include "runtime/native_gl_texture_shadow.h"'
    text=replace(text,anchor,'#include "runtime/native_batch_boundaries.h"\n'+anchor)
    old=' if(kind!=uint8_t(GXCommandType::BPRegister))return false;'
    text=replace(text,old,old+'\n if(native_batch_boundaries::Deferred(reg))return true;')
    old='    for (const auto &native_entry : commands.entries) {'
    text=replace(text,old,'    bool native_applied_bp[256]{}; // Fresh stream/load: raw zero does not prove derived state was initialized.\n'+old)
    old='        const uint32_t bp_value = NativeBpRegisterValue(native_reg, e.val);'
    text=replace(text,old,'''        const bool duplicate=native_applied_bp[native_reg] && !native_batch_boundaries::Deferred(native_reg) &&
            NativeRedundantBp(native_kind,native_reg,e.val);
'''+old+'''\n        native_applied_bp[native_reg]=true;
        if(duplicate)continue; // NativeBpRegisterValue still consumes the one-write mask.''')
    return text

def texture(text):
    text=restore(text)
    anchor='#include "runtime/native_prompt_textures.h"'
    text=replace(text,anchor,'#include "runtime/native_palette_decode.h"\n'+anchor)
    text=replace(text,'void TextureCache::decode_paletted(CPUContext& ctx, const gx::TexStage& stage, Color* out) {',
        'void TextureCache::decode_paletted(CPUContext& ctx, const gx::TexStage& stage, Color* out) {'+'''
    const uint32_t p=stage.base_addr&0x3fffffffu;
    const size_t bytes=texture_data_size(stage.width,stage.height,stage.format);
    const bool span=(p<MMU::kMem1Limit && bytes<=MMU::kMem1Limit-p) ||
        (p>=0x10000000u && p-0x10000000u<MMU::kMem2Limit && bytes<=MMU::kMem2Limit-(p-0x10000000u));
    if(span){
        const size_t offset=std::min(size_t(stage.tlut_offset),sizeof(g_state.tlutMem));
        if(native_palette_decode::Decode(ctx.mmu.get_ptr(stage.base_addr),bytes,stage.width,stage.height,stage.format,
            g_state.tlutMem+offset,sizeof(g_state.tlutMem)-offset,stage.tlut_format,out))return;
    }
''')
    text=replace(text,'       if(write_watch){','       if(write_watch && size>=65536){ // Smaller exact comparisons avoid a kernel dirty-page query.')
    return text

def apply(port,undo=False):
    rt=port/'build/nwii-full-source/nWiiRuntime'
    changes=[]
    for rel,fn in [('src/hle/gx/fifo_parser.cpp',parser),('src/hle/gx/renderer_gl.cpp',renderer),('src/hle/texture_cache_gl.cpp',texture)]:
        path=rt/rel;old=path.read_text();new=restore(old) if undo else fn(old);changes.append((path,old,new))
    if not undo:
        for name in ('native_palette_decode.h','native_batch_boundaries.h'):
            shutil.copy2(port/'runtime-overlays'/name,rt/'include/runtime'/name)
    for path,old,new in changes:
        if old!=new:path.write_text(new)
    print('Heavy-scene changes '+('restored.' if undo else 'staged: parser CP, draw boundaries, exact texture checks and palette decoding.'))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');a=ap.parse_args();apply(a.port,a.restore)

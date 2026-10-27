"""Exact EFB snapshot reuse, uniform construction epochs and shared viewport state."""
from pathlib import Path
import argparse,base64,re,shutil
ROOT=Path(__file__).resolve().parents[1]
MARK='NATIVE_DRAW_FOLLOWUPS_V1'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
def restore(s):return BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s)
def edit(s,old,new):
    assert s.count(old)==1,('draw anchor',old[:110],s.count(old))
    return s.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */',1)
def viewport(s,uniform=False):
    s=restore(s)
    include='#include "runtime/native_viewport_shadow.h"\n'
    if uniform:include+='#include "runtime/native_uniform_epoch.h"\n'
    s=edit(s,s.splitlines(keepends=True)[0],include+s.splitlines(keepends=True)[0])
    # Every owner is routed, including HUD save/restore and the main EFB/window.
    # Keep each replacement outside previous markers so restoration is exact.
    pattern=re.compile(r'\b(glViewport|glDepthRange)\s*\([^;]+;')
    matches=list(pattern.finditer(s))
    for match in reversed(matches):
        old=match.group();new=old.replace('glViewport','NativeViewport').replace('glDepthRange','NativeDepthRange')
        s=s[:match.start()]+'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */'+s[match.end():]
    return s
def renderer(s):
    s=viewport(s,True)
    # Stream VAO/VBO are needed only when immutable GPU geometry declines.
    old='  native_gl_bind_shadow::BindVertexArray(s_vao);\n  glBindBuffer(GL_ARRAY_BUFFER, s_vbo);\n  NativeUploadBatch();'
    s=edit(s,old,'  NativeUploadBatch();')
    old='  if(NativeRetainedGpuBind(stride,generators))return; // NATIVE_RETAINED_GPU_BIND_V1'
    s=edit(s,old,old+'\n  native_gl_bind_shadow::BindVertexArray(s_vao);glBindBuffer(GL_ARRAY_BUFFER,s_vbo);')
    old='static void NativeRestoreBpSnapshot() {'
    s=edit(s,old,old+'\n  native_uniform_epoch::Invalidate();')
    old='static void NativeTevRegisterWrite(uint8_t reg, uint32_t val) {'
    s=edit(s,old,old+'\n  native_uniform_epoch::DirtyTev(reg);')
    old='        int addr = cmd.reg;'
    s=edit(s,old,old+'\n        native_uniform_epoch::DirtyXf(uint32_t(cmd.reg),cmd.payload.size());')
    old='''    float mat[8], amb[8];
    unpack(g_state.xf[0x00C], mat);
    unpack(g_state.xf[0x00D], mat + 4);
    unpack(g_state.xf[0x00A], amb);
    unpack(g_state.xf[0x00B], amb + 4);'''
    new=old.replace('float mat','static float mat').replace('    unpack(g_state.xf[0x00C]', '    if(native_uniform_epoch::Build(native_uniform_epoch::Material)){unpack(g_state.xf[0x00C]')+'\n    }'
    s=edit(s,old,new)
    old='''      GLint cc[4] = {(GLint)g_state.xf[0x00E], (GLint)g_state.xf[0x00F],
                     (GLint)g_state.xf[0x010], (GLint)g_state.xf[0x011]};'''
    new='''      static GLint cc[4];if(native_uniform_epoch::Build(native_uniform_epoch::Channel)){
        cc[0]=GLint(g_state.xf[0x00E]);cc[1]=GLint(g_state.xf[0x00F]);cc[2]=GLint(g_state.xf[0x010]);cc[3]=GLint(g_state.xf[0x011]);}'''
    s=edit(s,old,new)
    a=s.index('    float lcol[32], lpos[32], ldir[32], lcos[32], ldist[32];');b=s.index('    if (s_active_shader.uLightCol >= 0)',a)
    old=s[a:b];new=old.replace('    float lcol','    static float lcol').replace('    for (int i = 0; i < 8; i++) {','    if(native_uniform_epoch::Build(native_uniform_epoch::Lights)){\n    for (int i = 0; i < 8; i++) {')+'    }\n'
    s=edit(s,old,new)
    s=edit(s,'  float kcolor[16] = {0};\n  float color[16] = {0};','  static float kcolor[16] = {0};\n  static float color[16] = {0};')
    s=edit(s,'  NativeTevUniforms(color, kcolor);','  if(native_uniform_epoch::Build(native_uniform_epoch::Tev))NativeTevUniforms(color, kcolor);')
    # Record raw/resolved identity before the write mask is consumed. These
    # counters change no batching decisions and run only during HUD/census.
    old='      const uint8_t native_kind = e.kind;'
    new=old+'''
      if(native_kind==uint8_t(GXCommandType::BPRegister)){
        const unsigned reg=e.reg;const uint32_t resolved=((s_native_bp_shadow[reg]&~s_native_bp_mask)|(e.val&s_native_bp_mask))&0xffffff;
        const bool identical=resolved==s_native_bp_shadow[reg];
        const bool boundary=s_batch_open && !NativeRedundantBp(native_kind,uint8_t(reg),e.val);
        native_flush_census::RecordBp(native_hud::on.load(std::memory_order_relaxed)||native_perf_census::Enabled(),reg,identical,boundary);
      }'''
    return edit(s,old,new)
def texture(s):
    s=restore(s)
    # Keep the prior snapshot until freshness is decided; resetting Entry first
    # would destroy the exact fallback and force a multi-MB allocation/copy.
    old='            auto& entry=efb_guest[addr & 0x01FFFFFF];entry={NativeGuestHash(p,bytes),bytes,{}};'
    s=edit(s,old,'            auto& entry=efb_guest[addr & 0x01FFFFFF];')
    a=s.index('            if(exact && span){');b=s.index('\n/* END_NATIVE_AUDIT_SPEEDUPS_V1 */',a)
    old=s[a:b]
    new='''            bool reused=false;
            if(exact && span){
              const uint8_t* bank=physical<mmu.mem1.size()?mmu.mem1.data():mmu.mem2.data();
              const size_t bank_bytes=physical<mmu.mem1.size()?mmu.mem1.size():mmu.mem2.size();
              const size_t offset=physical<mmu.mem1.size()?physical:physical-0x10000000u;
              static const bool watch=!std::getenv("NWII_NO_WRITE_WATCH") && !std::getenv("NWII_NO_EFB_WATCH");
              const uint64_t newest=watch?native_ram_newest_write(bank,bank_bytes,offset,bytes):UINT64_MAX;
              reused=entry.physical_base==physical && entry.bytes==bytes && entry.source.size()==bytes && entry.verified_gen && newest!=UINT64_MAX && newest<=entry.verified_gen;
              if(!reused){const uint64_t before=newest!=UINT64_MAX?native_ram_generation():0;
                entry.source.assign(p,p+bytes);entry.verified_gen=before;}
            }else{entry.source.clear();entry.verified_gen=0;}
            if(!reused)entry.hash=NativeGuestHash(p,bytes);
            entry.bytes=bytes;entry.physical_base=physical;'''
    s=edit(s,old,new)
    old='auto& entry=it->second;const uint32_t physical=addr&0x3fffffffu;auto& mmu=ctx.mmu;'
    return edit(s,old,old+'\n      if(entry.physical_base!=physical)return false; // MEM1/MEM2 may share the legacy masked map key.')
def texture_header(s):
    s=restore(s)
    old='struct EfbGuest { uint32_t hash; uint32_t bytes; std::vector<uint8_t> source; uint64_t verified_gen=0; };'
    return edit(s,old,old.replace('verified_gen=0;','verified_gen=0; uint32_t physical_base=UINT32_MAX;'))
def census(s):
    s=restore(s)
    old='inline Stats counts{},baseline{};'
    new='''struct BpStats{std::array<uint64_t,256> writes{},identical{},flushes{};};
inline BpStats bp_counts{},bp_baseline{};
inline void RecordBp(bool active,unsigned reg,bool identical,bool flushed){if(!active)return;++bp_counts.writes[reg];bp_counts.identical[reg]+=identical;bp_counts.flushes[reg]+=flushed;}
inline BpStats GetBpStats(){BpStats out;for(unsigned i=0;i<256;++i){out.writes[i]=bp_counts.writes[i]-bp_baseline.writes[i];out.identical[i]=bp_counts.identical[i]-bp_baseline.identical[i];out.flushes[i]=bp_counts.flushes[i]-bp_baseline.flushes[i];}return out;}
'''+old
    s=edit(s,old,new)
    return edit(s,'inline void ResetBaseline(){baseline=counts;}','inline void ResetBaseline(){baseline=counts;bp_baseline=bp_counts;}')
def hud(s):
    s=viewport(s,True)
    old=' return out;\n}\ninline std::string GxArchitectureJson()'
    new=''' out+="Uniform construction builds / skips (since launch):";
 for(unsigned i=0;i<native_uniform_epoch::Count;++i)out+=fmt(" %llu/%llu",native_uniform_epoch::builds[i],native_uniform_epoch::skips[i]);
 out+="\\n";
 const auto bp=native_flush_census::GetBpStats();
 out+="BP registers: writes / identical / flush boundaries (since reset).\\n";
 for(unsigned r=0;r<256;++r)if(bp.writes[r])out+=fmt("  0x%02x: %llu / %llu / %llu\\n",r,bp.writes[r],bp.identical[r],bp.flushes[r]);
 return out;
}
inline std::string GxArchitectureJson()'''
    return edit(s,old,new)
def main_source(s):return viewport(s)
OWNERS=[('src/hle/gx/renderer_gl.cpp',renderer),('src/hle/texture_cache_gl.cpp',texture),('src/core/main.cpp',main_source),
        ('include/runtime/native_hud_gl.h',hud),('include/runtime/native_flush_census.h',census),('include/runtime/texture_cache_gl.h',texture_header)]
def apply(port,undo=False):
    rt=port/'build/nwii-full-source/nWiiRuntime';changes=[]
    for rel,fn in OWNERS:
        path=rt/rel
        if undo and not path.exists():continue  # Fresh trees have no generated overlay headers yet.
        old=path.read_text();new=restore(old) if undo else fn(old);changes.append((path,old,new))
    if not undo:
        for name in ('native_viewport_shadow.h','native_uniform_epoch.h'):shutil.copy2(ROOT/'runtime-overlays'/name,rt/'include/runtime'/name)
    for path,old,new in changes:
        if old!=new:path.write_text(new)
    print('Draw followups '+('restored' if undo else 'staged')+'.')
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=ROOT);ap.add_argument('--restore',action='store_true');a=ap.parse_args();apply(a.port,a.restore)

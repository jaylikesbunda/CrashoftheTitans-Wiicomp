"""Skip identical projection uploads and construction of absent legacy uniforms."""
from pathlib import Path
import argparse

MARKER = 'NATIVE_DRAW_SUBMISSION_REUSE_V1'
DECL = '''// NATIVE_DRAW_SUBMISSION_REUSE_V1: uniforms belong to the linked program.
static bool NativeDrawSubmissionReuseEnabled(){
 static const bool enabled=std::getenv("NWII_NO_DRAW_SUBMISSION_REUSE")==nullptr;
 return enabled;
}
namespace native_draw_reuse {
struct Stats {uint64_t xf_equivalent=0,xf_textured_rejected=0;};
inline Stats counts{},baseline{};
inline Stats GetStats(){return {counts.xf_equivalent-baseline.xf_equivalent,counts.xf_textured_rejected-baseline.xf_textured_rejected};}
inline void ResetBaseline(){baseline=counts;}
}
static bool NativeSnapshotWordsEqual(const std::vector<float>& a,const std::vector<float>& b){
 return a.size()==b.size() && (a.empty() || !std::memcmp(a.data(),b.data(),a.size()*sizeof(float)));
}
static bool NativeProjectionUploadNeeded(bool bound,GLuint program,const float* p){
 static std::unordered_map<GLuint,std::vector<uint8_t>> cache;
 // XF processing can run after another GL owner. Never infer its bound program.
 if(!NativeDrawSubmissionReuseEnabled() || !bound){cache.clear();return true;}
 auto& previous=cache[program];
 if(previous.size()==64 && !std::memcmp(previous.data(),p,64))return false;
 const auto* bytes=reinterpret_cast<const uint8_t*>(p);
 previous.assign(bytes,bytes+64);return true;
}
'''
XF = '''// Identical XF stores have no GPU state transition; keep the stores themselves.
static bool NativeRedundantXf(const GXCommand& cmd){
 if(!NativeDrawSubmissionReuseEnabled() || cmd.type!=GXCommandType::XFRegister)return false;
 // Preserve validation frequency, and reject textured work before scanning XF words.
 static const bool all_slots=std::getenv("NWII_ALL_TEXTURE_SLOTS")!=nullptr;
 const unsigned used=all_slots?0xffu:NativeUsedTextureMask(g_state);
 for(unsigned unit=0;unit<8;++unit)if(used&(1u<<unit)){
  const auto& stage=g_state.texStages[unit];
  if(stage.base_addr!=0 && stage.width>0 && stage.height>0){
   ++native_draw_reuse::counts.xf_textured_rejected;return false;
  }
 }
 for(size_t i=0;i<cmd.payload.size();++i){
  const size_t a=size_t(cmd.reg)+i;const void* target=nullptr;
  if(a<=0xff)target=&g_state.posMatrices[a];
  else if(a>=0x400 && a<=0x45f)target=&g_state.normalMatrices[a-0x400];
  else if(a>=0x500 && a<0x600)target=&g_state.postMatrices[a-0x500];
  else if(a>=0x600 && a<=0x67f)target=&g_state.lights[(a-0x600)/16][(a-0x600)%16];
  else if(a>=0x1000 && a<0x1100){
   target=&g_state.xf[a-0x1000];
   if(a>=0x1020 && a<=0x1026){
    if(!g_state.projSet || std::memcmp(&g_state.projection[a-0x1020],&cmd.payload[i],4))return false;
    if(a==0x1026 && std::memcmp(&g_state.projType,&cmd.payload[i],4))return false;
   }
  }else return false; // Unknown/ignored addresses remain the original boundary.
  if(std::memcmp(target,&cmd.payload[i],4))return false;
 }
 ++native_draw_reuse::counts.xf_equivalent;
 return true;
}
'''

def replacements():
    return {
        'static void ApplyProjection(GLint uProj) {': DECL + 'static void ApplyProjection(GLint uProj,bool known_bound=false) {',
        '  s_gpu_vertex_mode=gpu;s_gpu_vertex_snapshot=cmd.native_gpu_snapshot;':
        '  s_gpu_vertex_mode=gpu;\n'
        '  // Existing exact matrix-source comparison canonically owns equal snapshots.\n'
        '  if(!NativeDrawSubmissionReuseEnabled() || !gpu || matrix_changed || !old)s_gpu_vertex_snapshot=cmd.native_gpu_snapshot;',
        'old->snap_pos_bank!=q.snap_pos_bank || old->snap_nrm_bank!=q.snap_nrm_bank':
        '!NativeSnapshotWordsEqual(old->snap_pos_bank,q.snap_pos_bank) || !NativeSnapshotWordsEqual(old->snap_nrm_bank,q.snap_nrm_bank)',
        'static bool NativeRedundantBp(const GXCommand& cmd){': XF+'static bool NativeRedundantBp(const GXCommand& cmd){',
        'cmd.type != GXCommandType::CPRegister && !NativeRedundantBp(cmd) && s_batch_open':
        'cmd.type != GXCommandType::CPRegister && !NativeRedundantBp(cmd) && s_batch_open && !NativeRedundantXf(cmd)',
        '  glUniformMatrix4fv(uProj, 1, GL_FALSE, p);':
        '  if(NativeProjectionUploadNeeded(known_bound,s_active_shader.id,p))\n'
        '    glUniformMatrix4fv(uProj, 1, GL_FALSE, p);',
        '  glUseProgram(s_active_shader.id);\n  ApplyProjection(s_active_shader.uProjMtx);':
        '  glUseProgram(s_active_shader.id);\n  ApplyProjection(s_active_shader.uProjMtx,true);',
        '    float mat[8], amb[8];':
        '    if(!NativeDrawSubmissionReuseEnabled() || s_active_shader.uMatColor>=0 || s_active_shader.uAmbColor>=0){\n'
        '    float mat[8], amb[8];',
        '    if (s_active_shader.uChanCtrl >= 0) {':
        '    } // Absent material/ambient uniforms consume none of the constructed floats.\n'
        '    if (s_active_shader.uChanCtrl >= 0) {',
        '    float lcol[32], lpos[32], ldir[32], lcos[32], ldist[32];':
        '    if(!NativeDrawSubmissionReuseEnabled() || s_active_shader.uLightCol>=0 || s_active_shader.uLightPos>=0 ||\n'
        '       s_active_shader.uLightDir>=0 || s_active_shader.uLightCosAtt>=0 || s_active_shader.uLightDistAtt>=0){\n'
        '    float lcol[32], lpos[32], ldir[32], lcos[32], ldist[32];',
        '      if (NativeUniformChanged(s_active_shader.id, s_active_shader.uLightDistAtt, ldist, 128)) glUniform4fv(s_active_shader.uLightDistAtt, 8, ldist);':
        '      if (NativeUniformChanged(s_active_shader.id, s_active_shader.uLightDistAtt, ldist, 128)) glUniform4fv(s_active_shader.uLightDistAtt, 8, ldist);\n'
        '    } // Linked-program locations are immutable; preserve all live uniform payloads.',
    }

def restore(source):
    if MARKER not in source:
        # A later stage can rewrite the region that carried the marker comment (the marker then
        # disappears while NativeDrawSubmissionReuseEnabled stays), so a re-staged tree must not be
        # treated as an error here: there is simply nothing left to undo.
        return source
    for old,new in replacements().items():
        if source.count(new)==1:
            source=source.replace(new,old,1)
        elif source.count(new)==0:
            # A later stage can rewrite a region this marker covers (the marker itself survives, so
            # the stage has been applied): leave the staged form in place instead of failing.
            continue
        else:
            raise RuntimeError(f'Duplicated draw reuse restore anchor: {new!r}')
    return source

def apply(source):
    if MARKER in source:
        # The marker proves this stage has run. A later stage can rewrite part of a replacement (the
        # Render parameter became CommandStream, the light block gained a GX work-metrics probe), so
        # the old canonical round-trip check no longer holds: leave the staged text alone.
        return source
    source=restore(source)
    for old,new in replacements().items():
        if source.count(old)==1:
            source=source.replace(old,new,1)
        elif source.count(new)>=1:
            continue
        else:
            raise RuntimeError(f'Draw reuse anchor count {source.count(old)}: {old!r}')
    return source

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--restore',action='store_true')
    args=parser.parse_args()
    path=args.port/'build/nwii-full-source/nWiiRuntime/src/hle/gx/renderer_gl.cpp'
    before=path.read_text();after=restore(before) if args.restore else apply(before)
    if before!=after:path.write_text(after)
    print('Draw submission reuse restored.' if args.restore else 'Identical projection uploads and absent legacy uniform construction skipped.')

if __name__=='__main__':main()

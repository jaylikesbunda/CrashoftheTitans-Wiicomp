"""Elide proven duplicate direct XF payloads, retaining every draw boundary."""
from pathlib import Path
import argparse,base64,re
MARK='NATIVE_XF_STREAM_REUSE_V1'
INCLUDE='#include "runtime/native_xf_stream_shadow.h" // '+MARK+'\n'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
def restore(s):
    s=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s).replace(INCLUDE,'')
    if MARK in s:raise ValueError('Partial XF stream reuse transform')
    return s
def replace(s,old,new):
    if s.count(old)!=1:raise ValueError(('XF reuse anchor',old[:100],s.count(old)))
    return s.replace(old,'/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */',1)
def parser(s):
    s=restore(s)
    anchor='void FifoParser::Parse(std::vector<uint8_t>& fifo, CommandStream& commands) {'
    # Reset once per top-level FIFO drain, never while a recursive list is parsed.
    s=replace(s,anchor,'static native_xf_stream::Shadow s_native_xf_stream;\n'+anchor+'\n    s_native_xf_stream.BeginStream();')
    anchor='            ExpandDisplayList(dl_addr, dl_size, commands, depth);'
    s=replace(s,anchor,'            s_native_xf_stream.Reset();\n'+anchor+'\n            s_native_xf_stream.Reset();')
    anchor='            uint32_t val = Read32(fifo, offset + 1);\n            int array = 12 + (cmd - 0x20) / 8;'
    s=replace(s,anchor,'            s_native_xf_stream.Reset();\n'+anchor)
    anchor='''            GXCommand& c = commands.EmitHeavy(GXCommandType::XFRegister,
                (uint16_t)((fifo[offset + 3] << 8) | fifo[offset + 4]));'''
    s=replace(s,anchor,'''            const unsigned native_xf_address=(fifo[offset+3]<<8)|fifo[offset+4];
            const bool native_xf_reuse=depth==0 && native_xf_stream::Enabled() && s_native_xf_stream.Allowed() && !gx_trace();
            const uint32_t native_xf_reference=native_xf_reuse?s_native_xf_stream.Reference(native_xf_address,unsigned(length)+1):UINT32_MAX;
            if(native_xf_reference<commands.heavy.size() &&
               commands.heavy[native_xf_reference].reg==native_xf_address &&
               commands.heavy[native_xf_reference].payload.size()==unsigned(length)+1 &&
               s_native_xf_stream.Unchanged(native_xf_address,fifo.data()+offset+5,unsigned(length)+1,
                 commands.heavy[native_xf_reference].payload.data(),native_xf_reference)){
                // Keep the original flush/texture validation boundary. Only
                // allocation, payload copying and repeated stores disappear.
                auto& barrier=commands.EmitRegister(GXCommandType::XFRegister,uint8_t(native_xf_address),0);
                barrier.heavy=native_xf_reference;
                barrier.flags|=kStreamXfNoopBoundary;
                s_native_xf_stream.Hit();
                if(native_constructed.active)++native_constructed.xf_skipped;
                offset+=total_size;continue;
            }
            if(native_xf_reuse){s_native_xf_stream.Remember(native_xf_address,unsigned(length)+1,uint32_t(commands.heavy.size()));s_native_xf_stream.Miss();}
'''+anchor)
    return INCLUDE+s
def renderer(s):
    s=restore(s)
    # Copy the existing exact state comparison, excluding its texture/batching
    # policy. A draw can temporarily change render state; fall back to applying
    # the referenced original payload if the current values no longer match.
    start=s.index(' for(size_t i=0;i<cmd.payload.size();++i){',s.index('static bool NativeRedundantXf('))
    end=s.index(' ++native_draw_reuse::counts.xf_equivalent;',start)
    comparison=s[start:end]
    anchor='static bool NativeRedundantBp(uint8_t kind,uint8_t reg,uint32_t val){'
    s=replace(s,anchor,'static bool NativeXfPayloadUnchanged(const GXCommand& cmd){\n'+comparison+' return true;\n}\n'+anchor)
    anchor='''    for (const auto &e : commands.entries) {
      const uint8_t native_kind = e.kind;'''
    s=replace(s,anchor,'''    for (const auto &native_entry : commands.entries) {
      auto e=native_entry;
      if(e.flags & kStreamXfNoopBoundary){
        const GXCommand& original=commands.heavy[e.heavy];
        if(NativeXfPayloadUnchanged(original)){
          if(s_batch_open && !NativeRedundantXf(original)){FlushBatch(native_flush_census::Xf);s_batch_open=false;}
          continue;
        }
      }
      const uint8_t native_kind = e.kind;''')
    return s
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');a=ap.parse_args()
    rt=a.port/'build/nwii-full-source/nWiiRuntime'
    changes=[]
    for rel,fn in [('src/hle/gx/fifo_parser.cpp',parser),('src/hle/gx/renderer_gl.cpp',renderer)]:
        path=rt/rel;old=path.read_text();new=restore(old) if a.restore else fn(old);changes.append((path,old,new))
    for path,old,new in changes:
        if old!=new:path.write_text(new)
    if not a.restore:
        target=rt/'include/runtime/native_xf_stream_shadow.h';target.write_bytes((a.port/'runtime-overlays/native_xf_stream_shadow.h').read_bytes())
    print('XF stream reuse restored.' if a.restore else 'XF payload reuse staged; original boundaries retained.')
if __name__=='__main__':main()

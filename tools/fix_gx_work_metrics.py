"""Record optimized-path denominators once per command/draw, never per vertex."""
from pathlib import Path
import argparse,base64,re
MARK='NATIVE_GX_WORK_METRICS_V1'
INCLUDE='#include "runtime/native_gx_work_metrics.h" // '+MARK+'\n'
BLOCK=re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/',re.S)
def restore(s):
    s=BLOCK.sub(lambda m:base64.b64decode(m[1]).decode(),s).replace(INCLUDE,'')
    if MARK in s:raise ValueError('Incomplete GX work metrics')
    return s
def replace(s,old,new,count=1):
    if s.count(old)!=count:raise ValueError(('GX metrics anchor',old[:90],s.count(old),count))
    wrapped='/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'\n/* END_'+MARK+' */'
    return s.replace(old,wrapped)
def method(s,signature):
    start=s.index(signature);body=s.index('{',start);end=body+1;depth=1
    while depth:depth+=(s[end]=='{')-(s[end]=='}');end+=1
    return start,end,s[start:end]
def parser_metrics(s):
    s=restore(s)
    signature='template<bool NativeInPlace> static void NativeParseStreamImpl(const std::vector<uint8_t>& fifo, size_t& offset, std::vector<GXCommand>& commands, int depth) {'
    s=replace(s,signature,signature+'\n    native_gx_work::ConstructionBatch native_constructed;')
    for signature,metric,amount in [('static bool NativeObservedDecode(','ObservedVertices','r.count'),
                                 ('static bool NativeObservedRange(','ObservedVertices','count')]:
        a,z,body=method(s,signature);position=body.rfind('return true;');assert position>=0
        old=body[position:position+12]
        body=replace(body,old,'native_gx_work::Add(native_gx_work::'+metric+','+amount+'); '+old)
        s=s[:a]+body+s[z:]
    old='std::vector<VertexData>().swap(c.vertices);'
    s=replace(s,old,'native_gx_work::Add(native_gx_work::PrepackedVertices,c.vertices.size()); '+old)
    old='snapshot->bytes=c.raw->bytes;'
    s=replace(s,old,old+'\n native_gx_work::Add(native_gx_work::BankBytesAvoided,4ull*(c.raw->snap_pos_bank.size()+c.raw->snap_nrm_bank.size()));')
    old='dl=std::move(snap);'
    s=replace(s,old,'{native_gx_work::Add(native_gx_work::OwnedListBytesMoved,snap.size()); '+old+'}',2)
    old='commands.insert(commands.end(), std::make_move_iterator(local.begin()), std::make_move_iterator(local.end()));'
    s=replace(s,old,'{native_gx_work::Add(native_gx_work::CommandsMoved,local.size()); '+old+'}')
    # Only direct construction, never cached replay, is relevant to the new
    # constructor benchmark. Inspect the command's type once at commit.
    old='native_slot.Commit(commands);'
    new='''if(native_constructed.active){
      if(c.type==GXCommandType::BPRegister)++native_constructed.values[0];
      else if(c.type==GXCommandType::CPRegister)++native_constructed.values[1];
      else if(c.type==GXCommandType::XFRegister)++native_constructed.values[2];
      else if(c.type==GXCommandType::DrawPrimitive)++native_constructed.values[3];
    }
    native_slot.Commit(commands);'''
    s=replace(s,old,new,6)
    return INCLUDE+s
def renderer_metrics(s):
    s=restore(s)
    old='if(previous.size()==64 && !std::memcmp(previous.data(),p,64))return false;'
    s=replace(s,old,'if(previous.size()==64 && !std::memcmp(previous.data(),p,64)){native_gx_work::Add(native_gx_work::ProjectionUploadsSkipped);return false;}')
    a,z,body=method(s,'static bool NativeRedundantXf(');position=body.rfind('return true;');assert position>=0
    body=replace(body,'return true;','native_gx_work::Add(native_gx_work::UnchangedXfBoundaries); return true;')
    s=s[:a]+body+s[z:]
    old='} // Absent material/ambient uniforms consume none of the constructed floats.'
    s=replace(s,old,'} else native_gx_work::Add(native_gx_work::MaterialBuildsSkipped); // Absent linked uniform construction skipped.')
    old='} // Linked-program locations are immutable; preserve all live uniform payloads.'
    s=replace(s,old,'} else native_gx_work::Add(native_gx_work::LightBuildsSkipped); // Absent linked light construction skipped.')
    return INCLUDE+s
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');ap.add_argument('--headers-only',action='store_true');args=ap.parse_args()
    rt=args.port/'build/nwii-full-source/nWiiRuntime'
    if not args.restore:
        for name in ('native_gx_work_metrics.h','native_gl_texture_shadow.h','native_geometry_watch.h','native_flush_census.h','native_gx_batching.h'):
            target=rt/'include/runtime'/name;value=(args.port/'runtime-overlays'/name).read_bytes()
            if not target.exists()or target.read_bytes()!=value:target.write_bytes(value)
    if args.headers_only:return
    changes=[]
    for rel,fn in [('src/hle/gx/fifo_parser.cpp',parser_metrics),('src/hle/gx/renderer_gl.cpp',renderer_metrics)]:
        path=rt/rel;old=path.read_text();new=restore(old) if args.restore else fn(old)
        changes.append((path,old,new))
    for path,old,new in changes:
        if old!=new:path.write_text(new)
    if not args.restore:
        target=rt/'include/runtime/native_gx_work_metrics.h';value=(args.port/'runtime-overlays/native_gx_work_metrics.h').read_bytes()
        if not target.exists()or target.read_bytes()!=value:target.write_bytes(value)
    print('GX optimized-path work metrics '+('restored.'if args.restore else'staged.'))
if __name__=='__main__':main()

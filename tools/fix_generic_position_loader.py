"""Hoist generic position reader selection outside the vertex loop.

Uses the existing exact templated readers, not a new numerical conversion or
executable-memory JIT. Unknown formats and the established direct path stay on
their existing implementation. Restore this stage before decode work metrics.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_GENERIC_POSITION_LOADER_V1'
BLOCK = re.compile(r'/\* '+MARK+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARK+r' \*/', re.S)

def restore(s):
    s = BLOCK.sub(lambda m: base64.b64decode(m[1]).decode(), s)
    if MARK in s: raise ValueError('Incomplete generic position metadata')
    return s

def replace(s, old, new):
    if s.count(old) != 1: raise ValueError(('Generic loader anchor', old[:80], s.count(old)))
    return s.replace(old, '/* '+MARK+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'/* END_'+MARK+' */')

def apply(s):
    s = restore(s)
    old = 'template<bool Direct> static bool NativePackedGeneric('
    s = replace(s, old, 'template<bool Direct,int PosMask=-1,int PosElements=3> static bool NativePackedGeneric(')
    old = 'if (!(Direct ? NativeVectorPlan::Read<1,4,3>(pos,buf,off,buf_size,vtx.pos) : pos.read(buf,off,buf_size,vtx.pos)))'
    new = '''if (!([&](){
            if constexpr(Direct)return NativeVectorPlan::Read<1,4,3>(pos,buf,off,buf_size,vtx.pos);
            else if constexpr(PosMask>=0)return NativeVectorPlan::Read<PosMask,4,PosElements>(pos,buf,off,buf_size,vtx.pos);
            else return pos.read(buf,off,buf_size,vtx.pos);
        }()))'''
    start=s.index('template<bool Direct,int PosMask=-1,int PosElements=3> static bool NativePackedGeneric(')
    end=s.index('template<int P,int N,int T> static bool NativePackedIndexed(',start)
    s=s[:start]+replace(s[start:end],old,new)+s[end:]
    anchor = 'template<int P,int N,int T> static bool NativePackedIndexed('
    helper = '''// Select only qualified F32 position readers. The other attributes retain
// their original VAT plans, malformed-read handling and default initialization.
static bool NativePackedGenericPosition(GXCommand& c,NativePackedBuffer& output,std::vector<uint32_t>& indices,unsigned inputs,unsigned words,const float* defaults){
 static const bool enabled=std::getenv("NWII_NO_GENERIC_POSITION_LOADER")==nullptr;
 const auto& v=c.raw->vat;
 // F32 readers won consistently in the paired fixtures for both indexed masks
 // (index8 and index16 both instantiate NativeVectorPlan::Read<Mask,4,N>, which
 // the fast NativeFormatDispatch<3,...> path already exercises). Small draws
 // retain the original reader to avoid paying extra selection overhead.
 if(enabled && c.raw->count>=32 && v.posType==VtxAttrType::F32 && (v.posMask==VtxAttrMask::Index8 || v.posMask==VtxAttrMask::Index16)){
  if(v.posMask==VtxAttrMask::Index16){
   if(v.posElements)return NativePackedGeneric<false,3,3>(c,output,indices,inputs,words,defaults);
   return NativePackedGeneric<false,3,2>(c,output,indices,inputs,words,defaults);
  }
  if(v.posElements)return NativePackedGeneric<false,2,3>(c,output,indices,inputs,words,defaults);
  return NativePackedGeneric<false,2,2>(c,output,indices,inputs,words,defaults);
 }
 return NativePackedGeneric<false>(c,output,indices,inputs,words,defaults);
}
'''
    s = replace(s, anchor, helper+anchor)
    old = 'ok=direct?NativePackedGeneric<true>(c,output,indices,inputs,words,defaults):NativePackedGeneric<false>(c,output,indices,inputs,words,defaults);'
    s = replace(s, old, 'ok=direct?NativePackedGeneric<true>(c,output,indices,inputs,words,defaults):NativePackedGenericPosition(c,output,indices,inputs,words,defaults);')
    return s

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--restore',action='store_true');args=ap.parse_args()
    path=args.port/'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
    old=path.read_text();new=restore(old) if args.restore else apply(old)
    if old!=new:path.write_text(new)
    print('Generic position loader '+('restored' if args.restore else 'staged'))
if __name__=='__main__':main()

"""Avoid overwritten packed defaults and repeated mutable-buffer ownership checks."""
from pathlib import Path
import argparse, base64, re

MARKER = 'NATIVE_DECODE_WORK_REUSE_V1'
BLOCK = re.compile(r'/\* '+MARKER+r' ([A-Za-z0-9+/=]+) \*/\n.*?/\* END_'+MARKER+r' \*/', re.S)
HELPERS = '''// NATIVE_DECODE_WORK_REUSE_HELPERS_BEGIN
template<int Normal,bool Colour> static __attribute__((always_inline)) inline void NativeInitializeObserved(float* out,const float* defaults,unsigned words){
 // Position, present normal/colour, and ST are completely overwritten by the
 // qualified readers before publication. Failed reads discard this buffer.
 if constexpr(!Colour)std::memcpy(out+3,defaults+3,16);
 if constexpr(Normal==0)std::memcpy(out+9,defaults+9,12);
 if(words==19)std::memcpy(out+12,defaults+12,28);
 else if(words==22)std::memcpy(out+12,defaults+12,40);
 else std::memcpy(out+12,defaults+12,size_t(words-12)*4);
}
// NATIVE_DECODE_WORK_REUSE_HELPERS_END
'''

def restore(source):
    source = BLOCK.sub(lambda m: base64.b64decode(m[1]).decode(), source)
    source = re.sub(r'// NATIVE_DECODE_WORK_REUSE_HELPERS_BEGIN\n.*?// NATIVE_DECODE_WORK_REUSE_HELPERS_END\n', '', source, flags=re.S)
    if MARKER in source or 'NATIVE_DECODE_WORK_REUSE_HELPERS' in source:
        raise ValueError('Incomplete decode work metadata')
    return source

def wrap(old, new):
    return '/* '+MARKER+' '+base64.b64encode(old.encode()).decode()+' */\n'+new+'/* END_'+MARKER+' */'

def apply(source):
    source = restore(source)
    old = 'std::memcpy(p,defaults,words*4);'
    if source.count(old) != 2:
        raise ValueError('Expected observed whole-draw and chunk initializers')
    source = source.replace(old, wrap(old, 'NativeInitializeObserved<Normal,Colour>(p,defaults,words);'))
    old = 'for(size_t i=0;i<c.vertices.size();++i){const auto& v=c.vertices[i];float* p=c.native_packed.data()+i*words;'
    new = 'float* packed_storage=c.native_packed.data();\n for(size_t i=0;i<c.vertices.size();++i){const auto& v=c.vertices[i];float* p=packed_storage+i*words;'
    if source.count(old) != 1:
        raise ValueError('Expected one prepack mutable-storage loop')
    source = source.replace(old, wrap(old, new))
    anchor = '// NATIVE_PACKED_DECODE_V1:'
    if source.count(anchor) != 1: raise ValueError('Packed decode anchor missing')
    source = source.replace(anchor, HELPERS+anchor, 1)
    return source

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--restore', action='store_true')
    args = ap.parse_args()
    path = args.port/'build/nwii-full-source/nWiiRuntime/src/hle/gx/fifo_parser.cpp'
    original = path.read_text()
    changed = restore(original) if args.restore else apply(original)
    if original != changed: path.write_text(changed)
    print('Decode work reuse: '+('restored' if args.restore else 'installed'))

if __name__ == '__main__': main()

"""Selected-disc instruction/source access for the bounded region prototype."""
from pathlib import Path
import re, struct

PORT = Path(__file__).resolve().parents[1]

def restore_regions(source):
    pattern=re.compile(r'// BEGIN_NATIVE_GUEST_REGION_([0-9A-F]{8})\n(.*?)// END_NATIVE_GUEST_REGION_\1',re.S)
    def restore(match):
        a=match[2].index('void native_region_original_'+match[1]+'(')
        z=match[2].index('\n}\n',a)+2
        return match[2][a:z].replace('native_region_original_','func_')
    source=pattern.sub(restore,source)
    source=re.sub(r'// BEGIN_NATIVE_GUEST_REGION_DECLS\n.*?// END_NATIVE_GUEST_REGION_DECLS\n','',source,flags=re.S)
    source=re.sub(r'\n// BEGIN_NATIVE_GUEST_REGION_EXTRAS\n.*?// END_NATIVE_GUEST_REGION_EXTRAS\n','',source,flags=re.S)
    return source.replace('#include "runtime/native_guest_regions.h" // NATIVE_GUEST_REGIONS_V1\n','')

def words_from_dol(path, addresses):
    data = Path(path).read_bytes();result={}
    for n in range(7):
        offset=struct.unpack_from('>I',data,4*n)[0]
        address=struct.unpack_from('>I',data,0x48+4*n)[0]
        size=struct.unpack_from('>I',data,0x90+4*n)[0]
        if offset+size>len(data):raise ValueError('Invalid DOL text bounds')
        for pc in addresses:
            if address<=pc and pc+4<=address+size:
                result[pc]=struct.unpack_from('>I',data,offset+pc-address)[0]
    return result

def functions_from_source(path, allowed):
    source=restore_regions(Path(path).read_text())
    heads=list(re.finditer(r'^(?:static )?void func_([0-9A-F]{8})\((?:NWII_CTX_PARAM|CPUContext&) ctx\) \{',source,re.M))
    result={}
    for n,head in enumerate(heads):
        address=int(head[1],16)
        if address not in allowed:continue
        end=source.find('\n}\n',head.end())
        if end<0:raise ValueError('Unterminated generated function')
        result[address]=source[head.start():end+2]
    return result

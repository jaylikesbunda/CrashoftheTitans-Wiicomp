"""Source-bound typed value helpers for small, closed, integer-only guest leaves."""
import re,hashlib
from dataclasses import dataclass
FIELD=re.compile(r'ctx\.(?:gpr\[\d+\]|cr\[\d+\]\.(?:lt|gt|eq|so)|ctr|xer|lr|pc|inst_count)(?!\w)')
HEAD=re.compile(r'^(?:(?:static|NWII_GUEST_ABI)\s+|__attribute__\(\([^\n]*\)\)\s+)*void func_([0-9A-F]{8})\(NWII_CTX_PARAM ctx\) \{',re.M)

@dataclass
class Leaf:
    address:str
    fields:tuple
    written:tuple
    code:str
    source:str

def name(field):return field.removeprefix('ctx.').replace('[','_').replace(']','').replace('.','_')
def typ(field):return 'uint64_t' if field=='ctx.inst_count' else 'bool' if '.cr[' in field else 'uint32_t'

def qualify(source,address):
    import promote_regs as P
    # Qualification uses only the normal entry. Public/resume dispatch remains untouched.
    source=re.sub(r'^NWII_GUEST_ABI void ','void ',source)
    parsed=P.parse(source)
    if not parsed or parsed[0]!='func_'+address:return None
    body='\n'.join(parsed[-1]);code=re.sub(r'//[^\n]*','',body)
    instructions=len(re.findall(r'\+\+ctx\.inst_count;',code))
    if not 1<=instructions<=24:return None
    if re.search(r'\b(?:for|while|switch|do|throw|try|asm|__asm|static)\b|#|&\s*ctx|["\']',code):return None
    if re.search(r'\bctx\b',FIELD.sub('0',code)):return None
    calls=re.findall(r'([\w:.]+)\s*\(',code)
    if any(c not in {'if','uint32_t','int32_t','uint64_t','int64_t','bool','std::rotl'} for c in calls):return None
    labels=set(re.findall(r'^loc_([0-9A-F]{8}): ;',code,re.M))
    if set(re.findall(r'goto loc_([0-9A-F]{8})',code))-labels:return None
    # Closed acyclic leaves only. A bounded instruction budget is not a loop proof.
    positions={m[1]:m.start() for m in re.finditer(r'^loc_([0-9A-F]{8}): ;',code,re.M)}
    if any(positions[m[1]]<=m.start() for m in re.finditer(r'goto loc_([0-9A-F]{8})',code)):return None
    fields=tuple(dict.fromkeys(FIELD.findall(code)))
    if len(fields)>12:return None
    writes=set()
    for m in FIELD.finditer(code):
        if re.match(r'\s*(?:=(?!=)|[-+*/%&|^]=|<<=|>>=|\+\+|--)',code[m.end():]) or re.search(r'(?:\+\+|--)\s*$',code[:m.start()]):writes.add(m[0])
    written=tuple(f for f in fields if f in writes)
    if not written:return None
    used=set(re.findall(r'\b[A-Za-z_]\w*\b',FIELD.sub('0',code)))
    declared=set(re.findall(r'\b(?:uint32_t|int32_t|uint64_t|int64_t|bool)\s+(\w+)',code))
    if declared & {name(f) for f in fields}:return None
    allowed={'uint32_t','int32_t','uint64_t','int64_t','bool','if','else','goto','return','true','false','std','rotl'}|declared
    if any(n not in allowed and not re.fullmatch(r'loc_[0-9A-F]{8}|[xXuUlL][0-9A-Fa-f]*',n) for n in used):return None
    state='native_leaf_result_'+address;helper='native_leaf_value_'+address
    params=', '.join(typ(f)+' '+name(f) for f in fields)
    returns='return {'+', '.join(name(f) for f in written)+'};'
    lifted=FIELD.sub(lambda m:name(m[0]),body)
    lifted=re.sub(r'\breturn;',returns,lifted)
    header='// guest-batch leaf '+address+' sha256 '+hashlib.sha256(source.encode()).hexdigest()+'\n'
    header+='struct '+state+' {'+' '.join(typ(f)+' '+name(f)+';' for f in written)+'};\n'
    header+='static __attribute__((always_inline)) inline '+state+' '+helper+'('+params+') {\n'+lifted+'\n'+returns+'\n}\n'
    return Leaf(address,fields,written,header,source)

def collect(paths,excluded):
    result={}
    for path in paths:
        text=path.read_text(errors='surrogateescape')
        for head in HEAD.finditer(text):
            address=head[1]
            if address in excluded:continue
            end=text.find('\n}',head.end())
            if end<0:continue
            leaf=qualify(text[head.start():end+2],address)
            if leaf:result[address]=leaf
    return result

def apply(text,leaves):
    pattern=re.compile(r'(ctx\.pc = 0x([0-9A-F]{8});\s*)func_\2\(ctx\);')
    used={};count=0
    def replace(m):
        nonlocal count
        leaf=leaves.get(m[2])
        if not leaf:return m[0]
        used[leaf.address]=leaf;count+=1
        code=m[1]+'{ const auto native_result = native_leaf_value_'+leaf.address+'('+', '.join(leaf.fields)+'); '
        code+=' '.join(f+' = native_result.'+name(f)+';' for f in leaf.written)+' }'
        return code
    changed=pattern.sub(replace,text)
    return changed,'\n'.join(leaf.code for leaf in used.values()),count

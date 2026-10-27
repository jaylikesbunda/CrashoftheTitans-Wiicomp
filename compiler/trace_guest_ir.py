"""Integer PPC-word lifting for the execution-region research prototype.

Every memory operation remains an ordered, guarded effect. Unknown operations
end the region. The embedding tool supplies observed successors, so branches
become guards with exact side exits instead of silently assuming a trace.
FP, timing/counter policies and runtime observation points are not lifted here.
"""
from dataclasses import dataclass
from region_ssa import const, expression as E
from guest_leaf_ir import signed, mask


class Unsupported(ValueError):
    pass


@dataclass(frozen=True)
class Instruction:
    pc: int
    word: int
    source: str = ''


FIELDS=tuple('r'+str(i) for i in range(32))+('lr','ctr','xer')+tuple(f'c{f}{b}' for f in range(8) for b in ('lt','gt','eq','so'))


def lower(node, region, successor, admitted):
    """Return 'continue', 'call', or 'exit'; never guess an unsupported effect."""
    w,pc=node.word,node.pc
    op,d,a,b=w>>26,(w>>21)&31,(w>>16)&31,(w>>11)&31
    get=lambda reg:region.get('r'+str(reg))
    put=lambda reg,value:region.set('r'+str(reg),value)
    def compare(field,left,right,signed_compare):
        kind='lts' if signed_compare else 'ltu'
        region.set(f'c{field}lt',E(kind,left,right))
        region.set(f'c{field}gt',E(kind,right,left))
        region.set(f'c{field}eq',E('eq',left,right))
        # Match the qualified source's architectural behavior. Do not silently
        # introduce an unrelated SO correction into this performance experiment.
        if f'ctx.cr[{field}].so' in node.source:
            region.set(f'c{field}so',E('ltu',const(0x7fffffff),region.get('xer')))
    def record(value):
        if w&1:compare(0,value,const(0),True)
    def finish(target):
        region.finish(target);return 'exit'
    if op in (14,15):
        immediate=signed(w&65535,16)*(65536 if op==15 else 1)
        put(d,E('add',get(a) if a else const(0),immediate))
    elif op in (24,25,26,27,28,29):
        kind={24:'or',25:'or',26:'xor',27:'xor',28:'and',29:'and'}[op]
        value=E(kind,get(d),const((w&65535)<<(16 if op&1 else 0)))
        put(a,value)
        if op in (28,29):compare(0,value,const(0),True)
    elif op in (20,21):
        bits=mask((w>>6)&31,(w>>1)&31)
        value=E('and',E('rotl',get(d),b),bits)
        if op==20:value=E('or',E('and',get(a),~bits),value)
        put(a,value);record(value)
    elif op in (10,11):
        if (w>>21)&1:raise Unsupported('64-bit compare')
        compare((w>>23)&7,get(a),const(signed(w&65535,16) if op==11 else w&65535),op==11)
    elif op in (32,33,36,37):
        update=op in (33,37)
        if update and (a==0 or op==33 and a==d):raise Unsupported('Invalid update-form alias')
        address=E('add',get(a) if a else const(0),signed(w&65535,16))
        if op in (32,33):put(d,region.read32(pc,address))
        else:region.write32(pc,address,get(d))
        if update:put(a,address)
    elif op==18:
        target=signed(w&0x03fffffc,26)
        target=(target if w&2 else pc+target)&0xffffffff
        if w&1:region.set('lr',pc+4)
        if target not in admitted or successor!=target:return finish(target)
        return 'call' if w&1 else 'continue'
    elif op==16:
        if not d&4 or w&1:raise Unsupported('CTR/link conditional branch')
        target=signed(w&0xfffc,16)
        target=(target if w&2 else pc+target)&0xffffffff
        if successor not in (target,pc+4):raise Unsupported('Unobserved conditional successor')
        condition=const(1) if d&16 else region.get(f'c{a//4}{("lt","gt","eq","so")[a%4]}')
        take_on_true=bool(d&8) if not d&16 else True
        expected=take_on_true if successor==target else not take_on_true
        region.guard(pc,condition,expected,pc+4 if successor==target else target)
    elif w==0x4e800020:
        target=region.get('lr')
        if successor is None or successor not in admitted:return finish(target)
        region.guard(pc,E('eq',target,successor),True,target)
    elif op==31:
        xo=(w>>1)&1023
        if xo in (0,32):
            if (w>>21)&1:raise Unsupported('64-bit compare')
            compare((w>>23)&7,get(a),get(b),xo==0)
        elif xo in (266,40):
            value=E('add',get(a),get(b)) if xo==266 else E('sub',get(b),get(a))
            put(d,value);record(value)
        elif xo in (444,28,316):
            value=E({444:'or',28:'and',316:'xor'}[xo],get(d),get(b))
            put(a,value);record(value)
        elif xo==26:
            value=E('clz',get(d));put(a,value);record(value)
        elif xo in (339,467):
            spr=((w>>16)&31)|(((w>>11)&31)<<5)
            field={1:'xer',8:'lr',9:'ctr'}.get(spr)
            if field is None:raise Unsupported('Unqualified SPR')
            if xo==339:put(d,region.get(field))
            else:region.set(field,get(d))
        elif xo in (23,151) and not w&1:
            address=E('add',get(a) if a else const(0),get(b))
            if xo==23:put(d,region.read32(pc,address))
            else:region.write32(pc,address,get(d))
        else:raise Unsupported(f'Unsupported extended opcode {xo}')
    else:raise Unsupported(f'Unsupported primary opcode {op}')
    return 'continue'

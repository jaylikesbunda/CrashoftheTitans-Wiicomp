"""First semantic region slice: acyclic integer/read-only callees.

Instruction semantics are lifted from selected-DOL words. Existing generated
counter annotations remain an explicit baseline policy, not a new timing model.
The emitted helper accepts values and MMU, never a CPUContext. Observable/slow
reads decline without committing any guest state, retaining the original call.
"""
from dataclasses import dataclass, asdict
import hashlib, re

SCHEMA = 1

class Unsupported(ValueError):
    pass

@dataclass(frozen=True)
class Op:
    pc: int
    word: int
    kind: str
    operands: tuple
    before: int = 0
    after: int = 0
    set_so: bool = False

def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value

def mask(mb, me):
    result = 0
    while True:
        result |= 1 << (31 - mb)
        if mb == me:
            return result
        mb = (mb + 1) & 31

def decode(pc, word):
    op, d, a, b = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    if op == 32:
        return Op(pc, word, 'load32', (d, a, signed(word & 65535, 16)))
    if op == 36:
        return Op(pc, word, 'store32', (d, a, signed(word & 65535, 16)))
    if op in (14, 15):
        immediate = signed(word & 65535, 16) * (65536 if op == 15 else 1)
        return Op(pc, word, 'addi', (d, a, immediate))
    if op in (24, 25):
        return Op(pc, word, 'ori', (a, d, (word & 65535) << (16 if op == 25 else 0)))
    if op == 21 and not word & 1:
        return Op(pc, word, 'rotate', (a, d, b, mask((word >> 6) & 31, (word >> 1) & 31)))
    if op in (10, 11) and not (word >> 21) & 1:
        return Op(pc, word, 'compare', ((word >> 23) & 7, a, signed(word & 65535, 16) if op == 11 else word & 65535, op == 11))
    if op == 31 and not word & 1 and (word >> 1) & 1023 == 444:
        return Op(pc, word, 'or', (a, d, b))
    if op == 31 and not word & 1 and (word >> 1) & 1023 == 266:
        return Op(pc, word, 'add', (d, a, b))
    if op == 18 and not word & 1:
        target = signed(word & 0x03fffffc, 26)
        return Op(pc, word, 'branch', (target & 0xffffffff if word & 2 else (pc + target) & 0xffffffff,))
    if op == 16 and not word & 1 and d & 4:
        target = signed(word & 0xfffc, 16)
        return Op(pc, word, 'conditional', (d, a, target & 0xffffffff if word & 2 else (pc + target) & 0xffffffff))
    if word == 0x4e800020:
        return Op(pc, word, 'return', ())
    raise Unsupported(f'Unsupported instruction {pc:08X}/{word:08X}')

def count_policy(text):
    return len(re.findall(r'\+\+ctx\.inst_count;', text)) + sum(map(int, re.findall(r'ctx\.inst_count \+= (\d+);', text)))

def lift(source, selected_words, allow_tail=False):
    if any(value in source for value in ('ctx.fpr', 'ctx.ps1', 'ctx.ps_', 'ctx.require_fpu', 'process_pending_callbacks', 'native_call_indirect')):
        raise Unsupported('Observable/FP/callback operations are outside this slice')
    labels = list(re.finditer(r'^loc_([0-9A-F]{8}): ;', source, re.M))
    if not labels or len(labels) > 64:
        raise Unsupported('Unsupported leaf extent')
    entry_count = count_policy(source[:labels[0].start()])
    ops = []
    for i, label in enumerate(labels):
        block = source[label.end():labels[i + 1].start() if i + 1 < len(labels) else len(source)]
        annotation = re.search(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})', block)
        if not annotation:
            raise Unsupported('Missing instruction annotation')
        pc, word = int(annotation[1], 16), int(annotation[2], 16)
        if pc != int(label[1], 16) or selected_words.get(pc) != word:
            raise Unsupported('Selected DOL and source instruction differ')
        node = decode(pc, word)
        so = bool(re.search(r'ctx\.cr\[\d\]\.so\s*=\s*\(ctx\.xer >> 31\) & 1;', block))
        ops.append(Op(pc, word, node.kind, node.operands, count_policy(block[:annotation.start()]), count_policy(block[annotation.end():]), so))
    addresses = {node.pc for node in ops}
    for node in ops:
        if node.kind in ('branch', 'conditional'):
            target = node.operands[-1]
            if target not in addresses or target <= node.pc:
                raise Unsupported('Only acyclic internal branches in first slice')
    if ops[-1].kind != 'return' and not allow_tail:
        raise Unsupported('Leaf must terminate in a return')
    used, dirty, fields, dirty_fields = set(), set(), set(), set()
    for node in ops:
        x = node.operands
        if node.kind in ('load32', 'addi', 'ori', 'rotate', 'or', 'add'):
            dirty.add(x[0]); used.add(x[0])
            if node.kind in ('load32', 'addi'):
                if x[1]: used.add(x[1])
            else:
                used.add(x[1])
                if node.kind in ('or', 'add'): used.add(x[2])
        elif node.kind == 'store32':
            used.add(x[0])
            if x[1]: used.add(x[1])
        elif node.kind == 'compare':
            used.add(x[1]); fields.add(x[0]); dirty_fields.add(x[0])
        elif node.kind == 'conditional' and not x[0] & 16:
            fields.add(x[1] // 4)
    return dict(schema=SCHEMA, entry=ops[0].pc, entry_count=entry_count, used=sorted(used), inputs=sorted(used), dirty=sorted(dirty), fields=sorted(fields), dirty_fields=sorted(dirty_fields),
                source_sha256=hashlib.sha256(source.encode()).hexdigest(), ops=ops)

def lift_chain(sources, selected_words, entry):
    """Collapse only explicitly emitted consecutive save/restore tail entries."""
    pieces=[];at=entry;seen=set();ops=[]
    while True:
        if at in seen or at not in sources:raise Unsupported('Missing/cyclic chain')
        seen.add(at);source=sources[at];tail=re.search(r'ctx.pc = (0x[0-9A-F]{8}); // mapped boundary fall-through\s+func_([0-9A-F]{8})\(ctx\); // NATIVE_FALLTHROUGH_TAIL_V1',source)
        ir=lift(source,selected_words,bool(tail));pieces.append(ir)
        if any(node.kind not in ('load32','store32','return') for node in ir['ops']):raise Unsupported('Only linear register/RAM chains')
        nodes=list(ir['ops'])
        if len(pieces)>1:
            first=nodes[0];nodes[0]=Op(first.pc,first.word,first.kind,first.operands,first.before+ir['entry_count'],first.after,first.set_so)
        ops+=nodes
        if not tail:break
        at=int(tail[1],16)
        if at!=int(tail[2],16) or at!=ops[-1].pc+4:raise Unsupported('Nonconsecutive tail')
    if ops[-1].kind!='return' or len(ops)>64:raise Unsupported('Unsupported chain exit')
    result=pieces[0].copy();result['ops']=ops
    result['used']=sorted({reg for piece in pieces for reg in piece['used']})
    result['dirty']=sorted({reg for piece in pieces for reg in piece['dirty']})
    roots={node.operands[1] for node in ops if node.kind in ('load32','store32')}
    if len(roots)!=1 or not next(iter(roots)) or next(iter(roots)) in result['dirty']:raise Unsupported('Changing/ambiguous span root')
    kinds={node.kind for node in ops if node.kind!='return'}
    if len(kinds)!=1:raise Unsupported('Mixed memory chain requires an additional effect proof')
    offsets=[node.operands[2] for node in ops if node.kind!='return'];low=min(offsets);high=max(offsets)+4
    result['span']=dict(root=next(iter(roots)),low=low,bytes=high-low,write='store32' in kinds)
    result['inputs']=sorted({result['span']['root']} | ({node.operands[0] for node in ops if node.kind=='store32'}))
    result['chain_entries']=[piece['entry'] for piece in pieces]
    result['source_sha256']=hashlib.sha256(''.join(sources[pc] for pc in result['chain_entries']).encode()).hexdigest()
    return result

def metadata(ir):
    return {**{key: value for key, value in ir.items() if key != 'ops'}, 'ops': [asdict(node) for node in ir['ops']]}

def emit(ir):
    tag = f'{ir["entry"]:08X}'
    members = ['bool valid=false;', 'uint64_t count=0;', 'uint32_t pc=0;']
    members += [f'uint32_t r{reg}=0;' for reg in ir['dirty']]
    members += [f'bool c{field}{bit}=false;' for field in ir['dirty_fields'] for bit in ('lt', 'gt', 'eq', 'so')]
    params = ['Memory& memory', 'uint64_t count', 'uint32_t lr', 'uint32_t xer']
    params += [f'uint32_t r{reg}' for reg in ir.get('inputs',ir['used'])]
    params += [f'bool c{field}{bit}' for field in ir['fields'] for bit in ('lt', 'gt', 'eq', 'so')]
    lines = [f'struct Result_{tag}{{' + ''.join(members) + '};', f'template<class Memory> __attribute__((always_inline)) static inline Result_{tag} Try_{tag}(' + ','.join(params) + '){', f'count+={ir["entry_count"]};']
    lines += [f'uint32_t r{reg}=0;' for reg in ir['used'] if reg not in ir.get('inputs',ir['used'])]
    if 'span' in ir:
        span=ir['span'];lines += [f'auto* span=OrdinarySpan(memory,r{span["root"]}+uint32_t({span["low"]}),{span["bytes"]},{str(span["write"]).lower()});', 'if(!span)return {};']
    for node in ir['ops']:
        x = node.operands
        lines += [f'L_{node.pc:08X}: ;', f'count+={node.before};']
        if node.kind == 'load32':
            if 'span' in ir:
                lines += [f'{{uint32_t value;std::memcpy(&value,span+{x[2]-ir["span"]["low"]},4);r{x[0]}=__builtin_bswap32(value);}}']
            else:
                base = f'r{x[1]}' if x[1] else '0u'
                lines += [f'{{uint32_t address={base}+uint32_t({x[2]});uint32_t value;', 'if(!ReadOrdinary32(memory,address,value))return {};', f'r{x[0]}=value;}}']
        elif node.kind == 'store32':
            if 'span' not in ir:raise Unsupported('Stores require complete preflight before any write')
            lines += [f'{{uint32_t value=__builtin_bswap32(r{x[0]});std::memcpy(span+{x[2]-ir["span"]["low"]},&value,4);}}']
        elif node.kind == 'addi':
            lines += [f'r{x[0]}=' + (f'r{x[1]}' if x[1] else '0u') + f'+uint32_t({x[2]});']
        elif node.kind == 'ori': lines += [f'r{x[0]}=r{x[1]}|uint32_t({x[2]});']
        elif node.kind == 'rotate': lines += [f'r{x[0]}=std::rotl(r{x[1]},{x[2]})&uint32_t({x[3]});']
        elif node.kind == 'or': lines += [f'r{x[0]}=r{x[1]}|r{x[2]};']
        elif node.kind == 'add': lines += [f'r{x[0]}=r{x[1]}+r{x[2]};']
        elif node.kind == 'compare':
            left = f'int32_t(r{x[1]})' if x[3] else f'r{x[1]}'
            right = f'int32_t({x[2]})' if x[3] else f'uint32_t({x[2]})'
            lines += [f'c{x[0]}{bit}=({left}{operator}{right});' for bit, operator in [('lt', '<'), ('gt', '>'), ('eq', '==')]]
            if node.set_so: lines += [f'c{x[0]}so=bool(xer>>31);']
        elif node.kind == 'branch': lines += [f'goto L_{x[0]:08X};']
        elif node.kind == 'conditional':
            condition = 'true' if x[0] & 16 else ('' if x[0] & 8 else '!') + f'c{x[1]//4}{("lt","gt","eq","so")[x[1]%4]}'
            lines += [f'if({condition})goto L_{x[2]:08X};']
        elif node.kind == 'return':
            lines += [f'{{Result_{tag} result;result.valid=true;result.count=count;result.pc=lr;']
            lines += [f'result.r{reg}=r{reg};' for reg in ir['dirty']]
            lines += [f'result.c{field}{bit}=c{field}{bit};' for field in ir['dirty_fields'] for bit in ('lt', 'gt', 'eq', 'so')]
            lines += ['return result;}']
        lines += [f'count+={node.after};']
    lines += ['return {};', '}']
    return '\n'.join(lines)

def inline_call(ir, original):
    tag = f'{ir["entry"]:08X}'
    args = ['ctx.mmu', 'ctx.inst_count', 'ctx.lr', 'ctx.xer']
    args += [f'ctx.gpr[{reg}]' for reg in ir.get('inputs',ir['used'])]
    args += [f'ctx.cr[{field}].{bit}' for field in ir['fields'] for bit in ('lt', 'gt', 'eq', 'so')]
    lines = [f'    ctx.pc=0x{tag};', '    if(native_guest_regions::Enabled() && !nwii::runtime::g_trace_calls){',
             f'      const auto region_result=native_guest_regions::Try_{tag}(' + ','.join(args) + ');', '      if(region_result.valid){']
    lines += [f'        ctx.gpr[{reg}]=region_result.r{reg};' for reg in ir['dirty']]
    lines += [f'        ctx.cr[{field}].{bit}=region_result.c{field}{bit};' for field in ir['dirty_fields'] for bit in ('lt', 'gt', 'eq', 'so')]
    lines += ['        ctx.inst_count=region_result.count;', '        ctx.pc=region_result.pc;', '      }else{', original, '      }', '    }else{', original, '    }']
    return '\n'.join(lines)

PREFIX = r'''#pragma once
#include <bit>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include "runtime/cpu_context.h"
#include "runtime/native_guest_region_stats.h"
namespace native_guest_regions {
template<class Memory> inline bool ReadOrdinary32(Memory& memory,uint32_t address,uint32_t& value){
 const uint32_t physical=address&0x3fffffffu;
 const intptr_t bias=memory.page_bias[physical>>16];
 if(bias<=1)return false;
 std::memcpy(&value,reinterpret_cast<const void*>(bias+physical),4);value=__builtin_bswap32(value);return true;
}
template<class Memory> inline uint8_t* OrdinarySpan(Memory& memory,uint32_t address,uint32_t bytes,bool writing){
 const uint32_t physical=address&0x3fffffffu;
 if(!bytes || bytes-1>0x3fffffffu-physical)return nullptr;
 const intptr_t bias=memory.page_bias[physical>>16];
 if(bias<=1 || memory.page_bias[(physical+bytes-1)>>16]!=bias)return nullptr;
 if(writing && nwii::runtime::native_watch_span_may_hit(physical,bytes))return nullptr;
 return reinterpret_cast<uint8_t*>(bias+physical);
}
'''

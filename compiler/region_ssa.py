"""Experimental guest-state SSA for guarded, straight-line execution regions.

This is independent of generated C++ register promotion. Architectural writes
update a value graph; guards keep exact exit snapshots. Loads/stores remain
ordered effects, and observers publish state and invalidate every assumption.
Store forwarding requires an explicit private-memory proof; it is off by
default. This module is not connected to the production builder. It models
32-bit integer state only; FP and 64-bit instruction-count effects are not lifted.
"""
from dataclasses import dataclass
import re

MASK = 0xffffffff


@dataclass(frozen=True)
class Value:
    op: str
    args: tuple


def const(n):
    return Value('const', (n & MASK,))


def signed(n):
    return n - (1 << 32) if n & (1 << 31) else n


def apply(op, values):
    if op == 'add': return (values[0] + values[1]) & MASK
    if op == 'sub': return (values[0] - values[1]) & MASK
    if op == 'and': return values[0] & values[1]
    if op == 'or': return values[0] | values[1]
    if op == 'xor': return values[0] ^ values[1]
    if op == 'eq': return int(values[0] == values[1])
    if op == 'ltu': return int(values[0] < values[1])
    if op == 'lts': return int(signed(values[0]) < signed(values[1]))
    if op == 'rotl':
        a, shift = values[0], values[1] & 31
        return ((a << shift) | (a >> ((32-shift) & 31))) & MASK
    if op == 'clz': return 32 - values[0].bit_length()
    raise ValueError('Unknown pure operation: ' + op)


def expression(op, *args):
    if op not in {'add','sub','and','or','xor','eq','ltu','lts','rotl','clz'}:
        raise ValueError('Unknown pure operation: ' + op)
    args = tuple(v if isinstance(v, Value) else const(v) for v in args)
    if all(v.op == 'const' for v in args):
        return const(apply(op, [v.args[0] for v in args]))
    a = args[0]
    b = args[1] if len(args) > 1 else None
    if b == const(0) and op in {'add','sub','or','xor','rotl'}: return a
    if b == const(MASK) and op == 'and': return a
    if b == const(0) and op == 'and': return const(0)
    if a == b:
        if op in {'sub','xor','ltu','lts'}: return const(0)
        if op in {'and','or'}: return a
        if op == 'eq': return const(1)
    # Normalize stack-derived addresses across guest frame adjustments. Every
    # addition is modulo 2^32, including negative frame displacements.
    if op == 'add' and b.op == 'const' and a.op == 'add' and a.args[1].op == 'const':
        return expression('add', a.args[0], const(a.args[1].args[0]+b.args[0]))
    return Value(op, args)


@dataclass(frozen=True)
class Effect:
    kind: str
    pc: int
    args: tuple
    state: tuple


class Region:
    """SSA state has no guest-function boundary operation.

    A builder can cross calls by changing LR/PC values and adding their decoded
    instructions. Only real observation points invoke observe(). Guard exits
    reconstruct the exact prefix state, including overwritten CR values.
    """
    def __init__(self, fields):
        self.fields = tuple(fields)
        if len(set(self.fields)) != len(self.fields):
            raise ValueError('Duplicate architectural field')
        self.epoch = 0
        self.state = {f: Value('input', (0, f)) for f in self.fields}
        self.effects = []
        self.memory = {}
        self.serial = 0
        self.stats = dict(assignments=0, loads=0, forwarded_loads=0, stores=0, guards=0, observers=0)

    def get(self, field):
        return self.state[field]

    def qualify(self,value):
        if not isinstance(value,Value):return
        seen=set()
        def visit(node):
            if node in seen:return
            seen.add(node)
            if node.op=='input' and node.args[0]!=self.epoch:
                raise ValueError('Architectural input cannot cross an opaque observer')
            if node.op not in {'const','input','loaded'}:
                for child in node.args:visit(child)
        if self.epoch:visit(value)

    def set(self, field, value):
        if field not in self.state:
            raise ValueError('Unknown architectural field: ' + str(field))
        self.qualify(value)
        self.state[field] = value if isinstance(value, Value) else const(value)
        self.stats['assignments'] += 1

    def snapshot(self):
        return tuple((f,self.state[f]) for f in self.fields)

    def guard(self, pc, condition, expected, exit_pc):
        if not isinstance(condition,Value):
            raise TypeError('Guard condition must be an SSA value')
        self.qualify(condition);self.qualify(exit_pc)
        self.effects.append(Effect('guard',pc,(condition,bool(expected),exit_pc),self.snapshot()))
        self.stats['guards'] += 1

    def read32(self, pc, address, *, private=False):
        """private means a separately proven private ordinary-RAM location.

        No MMIO, watch, sharing or bounds proof is inferred from a trace. The
        execution adapter must validate it or take the instruction's exit.
        """
        if not isinstance(address,Value): address=const(address)
        self.qualify(address)
        self.stats['loads'] += 1
        if private and address in self.memory:
            self.stats['forwarded_loads'] += 1
            return self.memory[address]
        if not private:
            # An unqualified read can invoke MMIO/helper code. Do not carry a
            # private-memory fact across a possible observer.
            self.memory.clear()
        self.serial += 1
        result=Value('loaded',(self.serial,))
        self.effects.append(Effect('read32',pc,(address,result,private),self.snapshot()))
        if private: self.memory[address]=result
        return result

    def write32(self, pc, address, value, *, private=False):
        if not isinstance(address,Value): address=const(address)
        if not isinstance(value,Value): value=const(value)
        self.qualify(address);self.qualify(value)
        # Conservative: any intervening write may alias a remembered address.
        # No disjointness is guessed from different symbolic expressions.
        self.memory.clear()
        if private: self.memory[address]=value
        self.effects.append(Effect('write32',pc,(address,value,private),self.snapshot()))
        self.stats['stores'] += 1

    def observe(self, pc, name):
        self.effects.append(Effect('observe',pc,(name,self.epoch+1),self.snapshot()))
        self.epoch += 1
        self.state={f:Value('input',(self.epoch,f)) for f in self.fields}
        self.memory.clear()
        self.stats['observers'] += 1

    def finish(self, pc):
        self.qualify(pc)
        self.effects.append(Effect('exit',pc,(),self.snapshot()))

    def execute(self, initial, read32, write32, observer=None, private_ok=None):
        """Reference executor for graph/exit differential checks, not runtime.

        read32/write32 adapters implement the actual memory semantics. They are
        always called in recorded order. Guard/proof failure returns the state
        immediately before that instruction's observable effect.
        """
        inputs={0:dict(initial)}
        loaded={}
        def evaluate(value):
            if value.op=='const': return value.args[0]
            if value.op=='input': return inputs[value.args[0]][value.args[1]] & MASK
            if value.op=='loaded': return loaded[value.args[0]]
            return apply(value.op,[evaluate(v) for v in value.args])
        def materialize(effect):
            return {f:evaluate(v) for f,v in effect.state}
        for effect in self.effects:
            if effect.kind=='guard':
                condition,expected,pc=effect.args
                if bool(evaluate(condition))!=expected:
                    return dict(reason='guard',pc=evaluate(pc) if isinstance(pc,Value) else pc,state=materialize(effect))
            elif effect.kind=='read32':
                address,result,private=effect.args;address=evaluate(address)
                if private and private_ok is not None and not private_ok(address,4):
                    return dict(reason='memory',pc=effect.pc,state=materialize(effect))
                loaded[result.args[0]]=read32(address)&MASK
            elif effect.kind=='write32':
                address,value,private=effect.args;address=evaluate(address)
                if private and private_ok is not None and not private_ok(address,4):
                    return dict(reason='memory',pc=effect.pc,state=materialize(effect))
                write32(address,evaluate(value))
            elif effect.kind=='observe':
                state=materialize(effect)
                if observer is not None:
                    if observer(effect.args[0],state) is False:
                        return dict(reason='observer',pc=effect.pc,state=state)
                inputs[effect.args[1]]=state
            elif effect.kind=='exit':
                return dict(reason='exit',pc=evaluate(effect.pc) if isinstance(effect.pc,Value) else effect.pc,state=materialize(effect))
            else: raise ValueError(effect.kind)
        raise ValueError('Region has no terminal exit')

    def hot_values(self):
        """Values needed on the expected path; cold exit snapshots stay lazy."""
        roots=[]
        for effect in self.effects:
            if effect.kind=='guard': roots.append(effect.args[0])
            elif effect.kind=='read32': roots.append(effect.args[0])
            elif effect.kind=='write32': roots.extend(effect.args[:2])
            elif effect.kind in {'observe','exit'}: roots.extend(v for _,v in effect.state)
            if effect.kind=='exit' and isinstance(effect.pc,Value):roots.append(effect.pc)
        reached=set()
        def visit(value):
            if value in reached:return
            reached.add(value)
            if value.op not in {'const','input','loaded'}:
                for v in value.args:visit(v)
        for root in roots:visit(root)
        return reached


def emit_cpp(region, name='RunRegion'):
    """Emit a standalone integer-region prototype, with precise cold exits.

    Memory::TryRead32/TryWrite32 must validate ordinary-memory access before
    touching memory, decline MMIO/watches, and validate any private proof.
    Runtime::Observe publishes through State and returns false to side-exit.
    No C++ function is emitted per guest function and no recursive calls occur.
    The embedding translator must preserve instruction-count effects separately.
    """
    if not re.fullmatch(r'[A-Za-z_]\w*',name) or any(not re.fullmatch(r'[A-Za-z_]\w*',f) for f in region.fields):
        raise ValueError('C++ names must be identifiers')
    lines=['#include <cstdint>','#include <bit>',
           'struct RegionExit { unsigned reason; uint32_t pc; };',
           'template<class State,class Memory,class Runtime>',
           f'static RegionExit {name}(State& state,Memory& memory,Runtime& runtime){{']
    serial=0
    cached={}
    context_epoch=0
    def fresh():
        nonlocal serial
        serial+=1
        return 'v'+str(serial)
    def emit_value(value,cache,indent):
        if value.op=='const':return f'{value.args[0]}u'
        if value in cache:return cache[value]
        if value.op=='loaded':return 'load'+str(value.args[0])
        if value.op=='input':code='state.'+value.args[1]
        else:
            args=[emit_value(v,cache,indent) for v in value.args]
            if value.op in {'add','sub','and','or','xor','eq','ltu'}:
                operator={'add':'+','sub':'-','and':'&','or':'|','xor':'^','eq':'==','ltu':'<'}[value.op]
                code=f'({args[0]} {operator} {args[1]})'
            elif value.op=='lts':code=f'(std::bit_cast<int32_t>({args[0]}) < std::bit_cast<int32_t>({args[1]}))'
            elif value.op=='rotl':code=f'std::rotl({args[0]},int({args[1]}))'
            elif value.op=='clz':code=f'std::countl_zero({args[0]})'
            else:raise ValueError(value.op)
        target=fresh();lines.append(indent+f'const uint32_t {target}=uint32_t({code});');cache[value]=target
        return target
    def publish(effect,cache,indent):
        # Compute the complete exit snapshot before mutating any context slot.
        # Otherwise a swap can read a field after its original value was lost.
        values=[(field,emit_value(value,cache,indent)) for field,value in effect.state
                if value!=Value('input',(context_epoch,field))]
        for field,value in values:lines.append(indent+f'state.{field}={value};')
    def exit_block(effect,reason,pc,indent='  '):
        cache=dict(cached)
        target=emit_value(pc,cache,indent) if isinstance(pc,Value) else f'{pc}u'
        publish(effect,cache,indent)
        lines.append(indent+f'return {{{reason},{target}}};')
    for effect in region.effects:
        if effect.kind=='guard':
            condition,expected,pc=effect.args;value=emit_value(condition,cached,' ')
            lines.append(f' if(bool({value})!={str(expected).lower()}){{')
            exit_block(effect,1,pc);lines.append(' }')
        elif effect.kind=='read32':
            address,result,private=effect.args;address=emit_value(address,cached,' ')
            target='load'+str(result.args[0]);lines.append(f' uint32_t {target};')
            lines.append(f' if(!memory.TryRead32({address},{target},{str(private).lower()})){{')
            exit_block(effect,2,effect.pc);lines.append(' }')
        elif effect.kind=='write32':
            address,value,private=effect.args
            address=emit_value(address,cached,' ');value=emit_value(value,cached,' ')
            lines.append(f' if(!memory.TryWrite32({address},{value},{str(private).lower()})){{')
            exit_block(effect,2,effect.pc);lines.append(' }')
        elif effect.kind=='observe':
            publish(effect,cached,' ')
            name=effect.args[0]
            if not re.fullmatch(r'[A-Za-z_]\w*',name):raise ValueError('Invalid observer name')
            lines.append(f' if(!runtime.Observe("{name}",state))return {{3,{effect.pc}u}};')
            context_epoch+=1
            cached={}
        elif effect.kind=='exit':
            target=emit_value(effect.pc,cached,' ') if isinstance(effect.pc,Value) else f'{effect.pc}u'
            publish(effect,cached,' ');lines.append(f' return {{0,{target}}};')
        else:raise ValueError(effect.kind)
    lines+=['}']
    return '\n'.join(lines)+'\n'

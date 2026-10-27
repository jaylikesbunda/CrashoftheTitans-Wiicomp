"""Generates tests/decoder_variants.S: one function per (instruction form, register assignment, addressing mode).

Each function has the C signature  void variant(uint64_t* regs)  (Win64: rcx). It loads rax..r15 (except rsp) from regs, executes
exactly one candidate memory instruction, and stores all registers back, so the test can compare a non-faulting execution (RAM)
with a faulting one (emulated by the arena handler) register for register.
"""
import random, sys

R64 = ['rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi'] + ['r%d' % i for i in range(8, 16)]
R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'] + ['r%dd' % i for i in range(8, 16)]
R16 = ['ax', 'cx', 'dx', 'bx', 'sp', 'bp', 'si', 'di'] + ['r%dw' % i for i in range(8, 16)]
R8 = ['al', 'cl', 'dl', 'bl', 'spl', 'bpl', 'sil', 'dil'] + ['r%db' % i for i in range(8, 16)]
R8H = {0: 'ah', 1: 'ch', 2: 'dh', 3: 'bh'}

FORMS = [
    # name, mnemonic template (M = memory operand, R = register), width, kind, register file, prefixes
    ('ld8', 'movzbl @M@, @R@', 1, 'load', R32),
    ('ld16', 'movbe @M@, @R@', 2, 'load', R16),
    ('ld32', 'movbe @M@, @R@', 4, 'load', R32),
    ('ld64', 'movbe @M@, @R@', 8, 'load', R64),
    ('st8', 'movb @R@, @M@', 1, 'store', R8),
    ('st16', 'movbe @R@, @M@', 2, 'store', R16),
    ('st32', 'movbe @R@, @M@', 4, 'store', R32),
    ('st64', 'movbe @R@, @M@', 8, 'store', R64),
    # legacy forms the decoder also understands (loads only)
    ('lmov8', 'movb @M@, @R@', 1, 'load', R8),
    ('lmov16', 'movw @M@, @R@', 2, 'load_plain', R16),
    ('lmov32', 'movl @M@, @R@', 4, 'load_plain', R32),
    ('lmov64', 'movq @M@, @R@', 8, 'load_plain', R64),
    ('lzx16', 'movzwl @M@, @R@', 2, 'load_zx', R32),
    ('lsx8', 'movsbl @M@, @R@', 1, 'load_sx', R32),
    ('lsx16', 'movswl @M@, @R@', 2, 'load_sx', R32),
    ('lsx32', 'movslq @M@, @R@', 4, 'load_sx', R64),
]


def mem(base, index, scale, disp):
    inner = '%%%s' % base if base else ''
    if index:
        inner += ',%%%s,%d' % (index, scale)
    return '%s(%s)' % (disp, inner)


def main(out, count, seed, prefix=''):
    rng = random.Random(seed)
    variants = []
    lines = ['.text']
    for n in range(count):
        form = FORMS[n % len(FORMS)] if n < len(FORMS) * 40 else rng.choice(FORMS)
        name, tpl, width, kind, regfile = form
        dest = rng.choice([i for i in range(16) if i != 4])
        base = rng.choice([i for i in range(16) if i != 4])
        use_index = rng.random() < 0.5
        index = rng.choice([i for i in range(16) if i != 4]) if use_index else None
        scale = rng.choice([1, 2, 4, 8])
        # displacement shapes: disp32 equal to the arena base (what the accessors produce), none, disp8, other disp32
        dshape = rng.choice(['arena', 'arena', 'none', 'd8', 'd32'])
        disp = {'arena': 'ARENA_BASE', 'none': '0', 'd8': str(rng.randrange(-128, 128)), 'd32': str(rng.randrange(-0x40000000, 0x40000000))}[dshape]
        if index is not None and index == base:
            index = None
        # high-byte register variant for 8-bit forms without REX: only when every register is in rax..rbx and no REX is needed
        regname = regfile[dest]
        high = False
        if width == 1 and name in ('st8', 'lmov8') and dest < 4 and base < 8 and (index is None or index < 8) and rng.random() < 0.25:
            regname = R8H[dest]; high = True
        m = mem(R64[base], R64[index] if index is not None else None, scale, disp)
        text = tpl.replace('@M@', m).replace('@R@', '%' + regname)
        fn = 'var_%d' % n
        L = ['.globl %s' % fn, '%s:' % fn,
             '  push %rbx', '  push %rbp', '  push %rsi', '  push %rdi', '  push %r12', '  push %r13', '  push %r14', '  push %r15',
             '  push %rcx', '  sub $8, %rsp']
        for i in [i for i in range(16) if i not in (1, 4)]:
            L.append('  mov %d(%%rcx), %%%s' % (8 * i, R64[i]))
        L.append('  mov 8(%rcx), %rcx')
        L.append('  %s%s' % (prefix, text))
        order = [x for x in range(16) if x != 4]
        for i in order:
            L.append('  push %%%s' % R64[i])
        L.append('  mov %d(%%rsp), %%rax' % (8 * 16))
        for k, i in enumerate(order):
            L.append('  mov %d(%%rsp), %%rdx' % (8 * (14 - k)))
            L.append('  mov %%rdx, %d(%%rax)' % (8 * i))
        L.append('  add $%d, %%rsp' % (8 * 15))
        L += ['  add $8, %rsp', '  pop %rcx', '  pop %r15', '  pop %r14', '  pop %r13', '  pop %r12', '  pop %rdi', '  pop %rsi', '  pop %rbp', '  pop %rbx', '  ret']
        lines += L
        variants.append((fn, name, width, kind, dest, base, index if index is not None else -1, scale, dshape, disp, text, high))
    asm = '\n'.join(lines).replace('ARENA_BASE', '0x7FFF0000') + '\n'
    open(out + '.S', 'w').write(asm)
    with open(out + '.inc', 'w') as f:
        f.write('struct Variant { void (*fn)(uint64_t*); const char* name; unsigned width; int kind; int dest, base, index, scale; int dshape; long long disp; const char* text; bool high; };\n')
        f.write('extern "C" {\n')
        for v in variants:
            f.write('void %s(uint64_t*);\n' % v[0])
        f.write('}\nstatic const Variant g_variants[] = {\n')
        kinds = {'load': 0, 'store': 1, 'load_plain': 2, 'load_zx': 3, 'load_sx': 4}
        shapes = {'arena': 0, 'none': 1, 'd8': 2, 'd32': 3}
        for v in variants:
            disp = 0x7FFF0000 if v[8] == 'arena' else int(v[9])
            f.write('  {%s, "%s", %d, %d, %d, %d, %d, %d, %d, %dLL, "%s", %s},\n' % (v[0], v[1], v[2], kinds[v[3]], v[4], v[5], v[6], v[7], shapes[v[8]], disp, v[10].replace('%', '%%').replace('"', ''), 'true' if v[11] else 'false'))
        f.write('};\n')
    print('generated', len(variants), 'variants')


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), '.byte 0x3e; ' if len(sys.argv) > 4 and sys.argv[4] == 'ds' else '')

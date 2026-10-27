"""Emit LLVM IR for an integer/register-only guest leaf (nWii IR-emitter spike).

Reuses `guest_leaf_ir` for PC->semantic-Op decoding/lifting, then emits an LLVM
IR module over the same CPUContext ABI:

  * `ctx` is an opaque `ptr` argument (CPUContext never enters the IR type system).
  * guest GPRs and CR bits live in `alloca`s: the IR is emitted in memory form and
    LLVM's SSA construction promotes it, so a real SSA register allocator performs
    the cross-basic-block guest-register allocation.
  * the MMU read/write are `internal alwaysinline` functions with an inline
    mask + range check + bswap fast path; the cold path delegates to the same
    runtime method the C++ header calls.  Inlining exposes the mask and range
    test to GVN/LICM as ordinary arithmetic.
  * `inst_count` is an `i64` alloca accumulated along each edge and materialised
    once at the exit; CR writes are materialised at the compare instruction,
    exactly where the generated C++ writes them.

The hybrid backend (tools/fix_llvm_ir_backend.py, marker # NATIVE_LLVM_IR_V1)
drives this emitter for the functions listed in
reference/backend-reports/llvm-ir-functions.json.  For those it passes `--name`
as the function's exact linker symbol (`func_XXXXXXXX`; functions.h declares the
guest functions extern "C") so the clang object replaces the generated C++
definition with identical ABI; the extern "C" runtime entry points it calls are
supplied by runtime-overlays/llvm_ir_support.cpp.

Field byte offsets come from a tiny C++ `offsetof` probe compiled with the same
headers/config, because the layout is standard-library dependent (std::stack).

usage:
    python emit_llvm_ir.py --source f.cpp --name func_8023B080 \
        --offsets offsets.json -o f.ll
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guest_leaf_ir as G  # noqa: E402

MEM1_LIMIT = 24 * 1024 * 1024
MEM2_LIMIT = 64 * 1024 * 1024
MEM2_BASE = 0x10000000
GX_PIPE_PAGE = 0x0C008000
CR_BITS = ('lt', 'gt', 'eq', 'so')


def selected_words(source):
    return {int(m.group(1), 16): int(m.group(2), 16)
            for m in re.finditer(r'// 0x([0-9A-F]{8}): ([0-9A-F]{8})', source)}


class Emitter:
    def __init__(self, name, entry, ops, ir, off):
        self.name = name
        self.entry = entry
        self.ops = ops
        self.ir = ir
        self.off = off
        self.lines = []
        self.tmp = 0

    def fresh(self, tag):
        self.tmp += 1
        return f'%{tag}{self.tmp}'

    def e(self, text):
        self.lines.append('  ' + text)

    def lab(self, text):
        self.lines.append(text + ':')

    # ---- module --------------------------------------------------------
    def mmu(self):
        L = self.lines
        o = self.off
        L += [
            'define internal i32 @nwii_ir_read32(ptr %ctx, i32 %addr) alwaysinline {',
            'entry:',
            '  %paddr = and i32 %addr, 1073741823',
            f'  %in1 = icmp ult i32 %paddr, {MEM1_LIMIT}',
            '  br i1 %in1, label %m1, label %chk2',
            'm1:',
            '  %is12 = icmp eq i32 %paddr, 12',
            '  br i1 %is12, label %ret0, label %m1do',
            'm1do:',
            f'  %g1 = getelementptr i8, ptr %ctx, i64 {o["mem1_base"]}',
            '  %base1 = load ptr, ptr %g1, align 8',
            '  %idx1 = zext i32 %paddr to i64',
            '  %p1 = getelementptr i8, ptr %base1, i64 %idx1',
            '  %v1 = load i32, ptr %p1, align 1',
            '  %s1 = call i32 @llvm.bswap.i32(i32 %v1)',
            '  ret i32 %s1',
            'ret0:',
            '  ret i32 0',
            'chk2:',
            f'  %off2 = sub i32 %paddr, {MEM2_BASE}',
            f'  %in2 = icmp ult i32 %off2, {MEM2_LIMIT}',
            '  br i1 %in2, label %m2do, label %slow',
            'm2do:',
            f'  %g2 = getelementptr i8, ptr %ctx, i64 {o["mem2_base"]}',
            '  %base2 = load ptr, ptr %g2, align 8',
            '  %idx2 = zext i32 %off2 to i64',
            '  %p2 = getelementptr i8, ptr %base2, i64 %idx2',
            '  %v2 = load i32, ptr %p2, align 1',
            '  %s2 = call i32 @llvm.bswap.i32(i32 %v2)',
            '  ret i32 %s2',
            'slow:',
            '  %sv = call i32 @nwii_ir_read32_slow(ptr %ctx, i32 %addr)',
            '  ret i32 %sv',
            '}',
            '',
            'define internal void @nwii_ir_write32(ptr %ctx, i32 %addr, i32 %value) alwaysinline {',
            'entry:',
            '  %paddr = and i32 %addr, 1073741823',
            '  %sub = sub i32 %paddr, 65536',
            f'  %in1 = icmp ult i32 %sub, {MEM1_LIMIT - 65536}',
            '  br i1 %in1, label %m1, label %chk2',
            'm1:',
            '  %gw1 = load i32, ptr @_ZN4nwii7runtime12g_watch_addrE, align 4',
            '  %gwa = and i32 %gw1, 1073741823',
            '  %gwp = lshr i32 %gwa, 16',
            '  %pp = lshr i32 %paddr, 16',
            '  %wp = icmp eq i32 %pp, %gwp',
            '  br i1 %wp, label %slow, label %m1do',
            'm1do:',
            f'  %g1 = getelementptr i8, ptr %ctx, i64 {o["mem1_base"]}',
            '  %base1 = load ptr, ptr %g1, align 8',
            '  %idx1 = zext i32 %paddr to i64',
            '  %p1 = getelementptr i8, ptr %base1, i64 %idx1',
            '  %b1 = call i32 @llvm.bswap.i32(i32 %value)',
            '  store i32 %b1, ptr %p1, align 1',
            '  ret void',
            'chk2:',
            f'  %off2 = sub i32 %paddr, {MEM2_BASE}',
            f'  %in2 = icmp ult i32 %off2, {MEM2_LIMIT}',
            '  br i1 %in2, label %m2do, label %chk3',
            'm2do:',
            f'  %g2 = getelementptr i8, ptr %ctx, i64 {o["mem2_base"]}',
            '  %base2 = load ptr, ptr %g2, align 8',
            '  %idx2 = zext i32 %off2 to i64',
            '  %p2 = getelementptr i8, ptr %base2, i64 %idx2',
            '  %b2 = call i32 @llvm.bswap.i32(i32 %value)',
            '  store i32 %b2, ptr %p2, align 1',
            '  ret void',
            'chk3:',
            f'  %pg = and i32 %paddr, {0xFFFFF000}',
            f'  %iswg = icmp eq i32 %pg, {GX_PIPE_PAGE}',
            '  br i1 %iswg, label %wg, label %slow',
            'wg:',
            '  %gw3 = load i32, ptr @_ZN4nwii7runtime12g_watch_addrE, align 4',
            '  %wnz = icmp ne i32 %gw3, 0',
            '  %wm = and i32 %gw3, 1073741816',
            '  %wd = add i32 %paddr, 3',
            '  %wd2 = sub i32 %wd, %wm',
            '  %whit = icmp ult i32 %wd2, 11',
            '  %wmay = and i1 %wnz, %whit',
            '  br i1 %wmay, label %slow, label %wgdo',
            'wgdo:',
            '  call void @nwii_ir_gx_wgpipe_write32(i32 %value)',
            '  ret void',
            'slow:',
            '  call void @nwii_ir_write32_slow(ptr %ctx, i32 %addr, i32 %value)',
            '  ret void',
            '}',
            '',
        ]

    def preamble(self):
        L = self.lines
        L.append('; nWii LLVM-IR slice (NATIVE_LLVM_IR_V1 hybrid backend), generated by emit_llvm_ir.py')
        L.append(f'; guest entry 0x{self.entry:08X}')
        L.append('target triple = "x86_64-w64-windows-gnu"')
        L.append('')
        L.append('declare i32 @llvm.bswap.i32(i32)')
        L.append('declare i32 @llvm.fshl.i32(i32, i32, i32)')
        L.append('declare void @nwii_ir_interpret_step(ptr)')
        L.append('declare i32 @nwii_ir_read32_slow(ptr, i32)')
        L.append('declare void @nwii_ir_write32_slow(ptr, i32, i32)')
        L.append('declare void @nwii_ir_gx_wgpipe_write32(i32)')
        L.append('@_ZN4nwii7runtime12g_watch_addrE = external global i32')
        L.append('')

    # ---- helpers -------------------------------------------------------
    def gep(self, offset):
        n = self.fresh('gep')
        self.e(f'{n} = getelementptr i8, ptr %ctx, i64 {offset}')
        return n

    def load_ctx32(self, offset):
        p = self.gep(offset)
        n = self.fresh('ld')
        self.e(f'{n} = load i32, ptr {p}, align 4')
        return n

    def store_ctx32(self, offset, value):
        p = self.gep(offset)
        self.e(f'store i32 {value}, ptr {p}, align 4')

    def store_ctx8(self, offset, value):
        p = self.gep(offset)
        self.e(f'store i8 {value}, ptr {p}, align 1')

    def store_ctx64(self, offset, value):
        p = self.gep(offset)
        self.e(f'store i64 {value}, ptr {p}, align 8')

    def alloc(self, ty):
        n = self.fresh('a')
        self.e(f'{n} = alloca {ty}')
        return n

    def lda(self, ptr, ty):
        n = self.fresh('v')
        self.e(f'{n} = load {ty}, ptr {ptr}')
        return n

    def sta(self, ptr, ty, value):
        self.e(f'store {ty} {value}, ptr {ptr}')

    def add_ic(self, amount):
        if amount == 0:
            return
        cur = self.lda(self.ic, 'i64')
        n = self.fresh('ic')
        self.e(f'{n} = add i64 {cur}, {amount}')
        self.sta(self.ic, 'i64', n)

    def zext_i8(self, v):
        n = self.fresh('z8')
        self.e(f'{n} = zext i1 {v} to i8')
        return n

    def zext_i32(self, v):
        n = self.fresh('z32')
        self.e(f'{n} = zext i1 {v} to i32')
        return n

    # ---- guest function ------------------------------------------------
    def guest(self, guard=True):
        o = self.off
        ir = self.ir
        self.lines.append(f'define void @{self.name}(ptr %ctx) {{')
        # entry block: all allocas live here so mem2reg/SROA promote them to SSA
        self.lab('entry')
        self.rega = {}
        for r in ir['used']:
            self.rega[r] = self.alloc('i32')
        self.cra = {}
        for f in sorted(set(ir['fields']) | set(ir['dirty_fields'])):
            for bit in range(4):
                self.cra[(f, bit)] = self.alloc('i8')
        self.ic = self.alloc('i64')
        if guard:
            # entry guard, mirrors the generated prologue
            pc0 = self.load_ctx32(o['pc'])
            at = self.fresh('at')
            self.e(f'{at} = icmp eq i32 {pc0}, {self.entry}')
            self.e(f'br i1 {at}, label %body, label %dispatch')
            self.lab('dispatch')
            pc1 = self.load_ctx32(o['pc'])
            z = self.fresh('z')
            self.e(f'{z} = icmp eq i32 {pc1}, 0')
            self.e(f'br i1 {z}, label %out, label %interp')
            self.lab('interp')
            self.e('call void @nwii_ir_interpret_step(ptr %ctx)')
            self.e('br label %out')
            self.lab('body')
        for r in ir['used']:
            self.sta(self.rega[r], 'i32', self.load_ctx32(o['gpr'] + 4 * r))
        for f in sorted(set(ir['fields']) | set(ir['dirty_fields'])):
            word = self.load_ctx32(o['cr'] + 4 * f)
            for bit in range(4):
                sh = self.fresh('sh')
                self.e(f'{sh} = lshr i32 {word}, {bit}')
                t = self.fresh('t')
                self.e(f'{t} = trunc i32 {sh} to i8')
                m = self.fresh('m')
                self.e(f'{m} = and i8 {t}, 1')
                self.sta(self.cra[(f, bit)], 'i8', m)
        self.sta(self.ic, 'i64', self.load_ctx64(o['inst_count']))
        self.add_ic(ir['entry_count'])
        self.e(f'br label %L{self.ops[0].pc:08X}')

        for i, op in enumerate(self.ops):
            self.op(i, op)

        self.lab('out')
        self.e('ret void')
        self.lines.append('}')

    def load_ctx64(self, offset):
        p = self.gep(offset)
        n = self.fresh('ld')
        self.e(f'{n} = load i64, ptr {p}, align 8')
        return n

    def rd(self, r):
        return self.lda(self.rega[r], 'i32')

    def wr(self, r, v):
        self.sta(self.rega[r], 'i32', v)

    def op(self, i, op):
        x = op.operands
        k = op.kind
        self.lab(f'L{op.pc:08X}')
        self.add_ic(op.before)
        if k == 'load32':
            d, a, off = x
            ea = self.fresh('ea')
            self.e(f'{ea} = add i32 {self.rd(a)}, {off}')
            v = self.fresh('v')
            self.e(f'{v} = call i32 @nwii_ir_read32(ptr %ctx, i32 {ea})')
            self.wr(d, v)
            self.fallthrough(i, op)
        elif k == 'store32':
            d, a, off = x
            ea = self.fresh('ea')
            self.e(f'{ea} = add i32 {self.rd(a)}, {off}')
            self.e(f'call void @nwii_ir_write32(ptr %ctx, i32 {ea}, i32 {self.rd(d)})')
            self.fallthrough(i, op)
        elif k == 'addi':
            d, a, imm = x
            base = self.rd(a) if a else '0'
            v = self.fresh('v')
            self.e(f'{v} = add i32 {base}, {imm}')
            self.wr(d, v)
            self.fallthrough(i, op)
        elif k == 'ori':
            d, a, imm = x
            v = self.fresh('v')
            self.e(f'{v} = or i32 {self.rd(a)}, {imm}')
            self.wr(d, v)
            self.fallthrough(i, op)
        elif k == 'rotate':
            d, a, sh, m = x
            base = self.rd(a)
            v = self.fresh('v')
            self.e(f'{v} = call i32 @llvm.fshl.i32(i32 {base}, i32 {base}, i32 {sh})')
            w = self.fresh('w')
            self.e(f'{w} = and i32 {v}, {m}')
            self.wr(d, w)
            self.fallthrough(i, op)
        elif k == 'or':
            d, s1, s2 = x
            v = self.fresh('v')
            self.e(f'{v} = or i32 {self.rd(s1)}, {self.rd(s2)}')
            self.wr(d, v)
            self.fallthrough(i, op)
        elif k == 'add':
            d, s1, s2 = x
            v = self.fresh('v')
            self.e(f'{v} = add i32 {self.rd(s1)}, {self.rd(s2)}')
            self.wr(d, v)
            self.fallthrough(i, op)
        elif k == 'compare':
            field, reg, imm, signed = x
            base = self.rd(reg)
            lt = self.fresh('lt')
            gt = self.fresh('gt')
            eq = self.fresh('eq')
            self.e(f'{lt} = icmp {"slt" if signed else "ult"} i32 {base}, {imm}')
            self.e(f'{gt} = icmp {"sgt" if signed else "ugt"} i32 {base}, {imm}')
            self.e(f'{eq} = icmp eq i32 {base}, {imm}')
            bits = [lt, gt, eq]
            if op.set_so:
                so = self.fresh('so')
                self.e(f'{so} = icmp slt i32 {self.load_ctx32(self.off["xer"])}, 0')
                bits.append(so)
            for idx, v in enumerate(bits):
                self.sta(self.cra[(field, idx)], 'i8', self.zext_i8(v))
                self.store_ctx8(self.off['cr'] + 4 * field + idx, self.zext_i8(v))
            self.fallthrough(i, op)
        elif k == 'branch':
            self.e(f'br label %L{x[0]:08X}')  # 'after' is dead on an unconditional branch
        elif k == 'conditional':
            bo, bi, target = x
            field, bit = bi // 4, bi % 4
            cond = self.lda(self.cra[(field, bit)], 'i8')
            c = self.fresh('c')
            self.e(f'{c} = icmp ne i8 {cond}, 0')
            ft = f'ft{op.pc:08X}'
            if bo & 16:
                self.e(f'br i1 true, label %L{target:08X}, label %{ft}')
            elif bo & 8:
                self.e(f'br i1 {c}, label %L{target:08X}, label %{ft}')
            else:
                self.e(f'br i1 {c}, label %{ft}, label %L{target:08X}')
            self.lab(ft)
            self.add_ic(op.after)
            self.e(f'br label %L{self.ops[i + 1].pc:08X}')
        elif k == 'return':
            for r in self.ir['dirty']:
                self.store_ctx32(self.off['gpr'] + 4 * r, self.lda(self.rega[r], 'i32'))
            self.store_ctx64(self.off['inst_count'], self.lda(self.ic, 'i64'))
            self.store_ctx32(self.off['pc'], self.load_ctx32(self.off['lr']))
            self.e('br label %out')
        else:
            raise SystemExit('unsupported op kind: ' + k)

    def fallthrough(self, i, op):
        self.add_ic(op.after)
        self.e(f'br label %L{self.ops[i + 1].pc:08X}')


def build(name, ir, off, guard=True):
    e = Emitter(name, ir['entry'], ir['ops'], ir, off)
    e.preamble()
    e.mmu()
    e.guest(guard)
    return '\n'.join(e.lines) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--offsets', required=True)
    ap.add_argument('--no-guard', action='store_true')
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()
    source = Path(a.source).read_text(encoding='utf-8')
    off = json.loads(Path(a.offsets).read_text())
    ir = G.lift(source, selected_words(source))
    Path(a.out).write_text(build(a.name, ir, off, not a.no_guard), encoding='utf-8')
    print('wrote %s  entry=0x%08X ops=%d used=%s dirty=%s' %
          (a.out, ir['entry'], len(ir['ops']), ir['used'], ir['dirty']))


if __name__ == '__main__':
    main()

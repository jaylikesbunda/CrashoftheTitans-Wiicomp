"""Route translated hot reads through the protected fixed-address RAM window.

Cold functions, all stores, diagnostics and runtime MMU operations keep their
existing paths. The handler completes unexpected MMIO and unmapped reads using
the original slow accessor. No CPUContext or MMU layout changes are needed.
"""
import re

# Functions observed using hardware/unmapped reads keep the ordinary accessor.
# Other regions remain correct through the exception handler.
IO_FUNCTIONS=frozenset((0x803b4020,0x803b4c2c,0x803b4e4c,0x803b9218,0x803b9574,0x803babb8,0x803bf3b8,0x803bf6b8,0x803bfa08,0x803bfb38,0x803bffd8,0x803c15b0,0x803c1670,0x803c229c,0x803c25f8,0x803c31a8,0x803c4a08,0x803c4e88,0x803c523c,0x803c8d4c,0x803ca394,0x803ca3a8,0x803ca404,0x803ca430,0x803ca4a4,0x803ca56c,0x803ca5ec,0x803d5c44,0x803d5cc0,0x803d5ce8,0x803d5d00,0x803d5e80,0x803d5f8c,0x803dea08,0x803dea18,0x803dea28,0x803dea50,0x803decd4,0x803e415c,0x803eb030,))

INCLUDE='#include "runtime/native_fastmem.h" // NATIVE_PROTECTED_FASTMEM_V1\n'
READ=re.compile(r'ctx\.mmu\.(read8|read16|read32|read64|read_f32|read_f64)\(')
BACK=re.compile(r'native_fastmem::(read8|read16|read32|read64|read_f32|read_f64)\(ctx\.mmu,')
FUNCTION=re.compile(r'^\s*(?:(?:static|NWII_GUEST_ABI)\s+|__attribute__\(\([^\n]*?\)\)\s+)*void (?:func|native_guest_(?:primary|resume|continuation))_([0-9A-F]{8})\w*\(')

def restore(text):
    if INCLUDE not in text:return text
    return BACK.sub(r'ctx.mmu.\1(',text).replace(INCLUDE,'')

def hot_source(text):
    text=restore(text)
    lines=[];io=False
    for line in text.splitlines(keepends=True):
        match=FUNCTION.match(line)
        if match:io=int(match[1],16) in IO_FUNCTIONS
        lines.append(line if io else READ.sub(r'native_fastmem::\1(ctx.mmu,',line))
    changed=''.join(lines)
    return INCLUDE+changed if changed!=text else text

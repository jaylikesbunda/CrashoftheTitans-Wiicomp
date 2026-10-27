"""Isolated, bounded guest-PC tracing for execution-region selection.

Build from an explicitly supplied corrected runtime link plan. Production inputs
are read only; generated sources, objects, symbols and captures go to --work.
Only qualified non-held-out functions receive hooks. The capture is deliberately
unsuitable for timing comparisons. Process extraction uses VM_READ only.
"""
from pathlib import Path
import argparse
import ctypes
import ctypes.wintypes as W
import hashlib
import json
import re
import shlex
import struct
import subprocess
import time
from promote_regs import code_of

PORT = Path(__file__).resolve().parents[1]
ROOT = '803061E0'
OWNERS = (ROOT, '802CC108', '802CC1F0', '802CB758', '8029316C', '80293154')
CAPACITY = 262144

HEADER = r'''#pragma once
#include "runtime/native_hud.h"
#include "runtime/cpu_context.h"
#include <atomic>
namespace nwii::runtime {void native_call_indirect_cached(CPUContext&,uint32_t);}
struct GuestRegionTraceRow { uint32_t pc,tag; uint64_t ic; };
extern "C" {
extern GuestRegionTraceRow guest_region_trace_rows[262144];
extern std::atomic<uint64_t> guest_region_trace_count;
extern std::atomic<uint32_t> guest_region_trace_state;
extern std::atomic<uint32_t> guest_region_trace_completed;
}
namespace guest_region_trace {
extern bool active,saw_off,started;
extern unsigned invocation,depth;
inline void Record(uint32_t pc,uint64_t ic,unsigned owner,unsigned kind=0){
 if(!active || depth==0)return;
 const auto at=guest_region_trace_count.load(std::memory_order_relaxed);
 if(at>=262144){active=false;guest_region_trace_state.store(4,std::memory_order_release);return;}
 guest_region_trace_rows[at]={pc,(invocation<<16)|(owner<<8)|kind,ic};
 guest_region_trace_count.store(at+1,std::memory_order_release);
}
inline bool BeginRoot(nwii::runtime::CPUContext& ctx){
  if(!native_hud::on.load(std::memory_order_relaxed)){
   saw_off=true;if(!started)guest_region_trace_state.store(1,std::memory_order_release);
  }
  if(!started && saw_off && native_hud::on.load(std::memory_order_relaxed)){
   started=true;active=true;guest_region_trace_state.store(2,std::memory_order_release);
  }
  if(active){++depth;Record(0x803061E0,ctx.inst_count,0,1);return true;}
  return false;
}
inline void EndRoot(nwii::runtime::CPUContext& ctx,bool selected){
  if(!selected)return;
  Record(ctx.pc,ctx.inst_count,0,2);
  if(--depth==0){
   ++invocation;guest_region_trace_completed.store(invocation,std::memory_order_release);
   if(invocation==64){active=false;if(guest_region_trace_state.load(std::memory_order_relaxed)!=4)guest_region_trace_state.store(3,std::memory_order_release);}
  }
}
inline void Indirect(nwii::runtime::CPUContext& ctx,uint32_t site,unsigned owner){
 Record(ctx.pc,ctx.inst_count,owner,3);
 nwii::runtime::native_call_indirect_cached(ctx,site);
 Record(ctx.pc,ctx.inst_count,owner,4);
}
}
'''
DEFINITIONS = r'''
extern "C" {
GuestRegionTraceRow guest_region_trace_rows[262144]{};
std::atomic<uint64_t> guest_region_trace_count{0};
std::atomic<uint32_t> guest_region_trace_state{0};
std::atomic<uint32_t> guest_region_trace_completed{0};
}
namespace guest_region_trace {
bool active=false,saw_off=false,started=false;
unsigned invocation=0,depth=0;
}
'''


def qualified_owners():
    # This is the existing exclusion-only manifest, never the frozen benchmark.
    excluded = set(json.loads((PORT / 'reference/guest-regions/effect-exclusions.json').read_text())['addresses'])
    if set(OWNERS) & excluded:
        raise ValueError('Trace owner intersects held-out exclusions')
    return OWNERS


def instrument(text):
    """Hook labels after promotion, so instrumentation cannot create barriers."""
    owners = qualified_owners()
    pattern = re.compile(r'^(?:static )?void ((?:func_|native_guest_(?:resume|primary|continuation)_)([0-9A-F]{8}))\(NWII_CTX_PARAM ctx\) \{', re.M)
    edits = []
    stats = []
    for head in pattern.finditer(text):
        if head[2] not in owners:
            continue
        end = text.find('\n}\n', head.end())
        if end < 0:
            raise ValueError('Unterminated selected function')
        end += 2
        source = text[head.start():end]
        if 'guest_region_trace::' in source:
            raise ValueError('Already instrumented input')
        owner = owners.index(head[2])
        counter = 'ic' if re.search(r'uint64_t\s+ic\s*;', source) else 'ctx.inst_count'
        # ic is live at every admitted PPC label because its next observation
        # includes the generated instruction-count policy. Never read a GPR
        # local merely for a diagnostic; promotion can leave dead locals unset.
        rewritten, labels = re.subn(r'^(loc_([0-9A-F]{8}): ;)$',
            lambda m: m[0] + f'\n    guest_region_trace::Record(0x{m[2]},{counter},{owner});', source, flags=re.M)
        rewritten, calls = re.subn(r'nwii::runtime::native_call_indirect_cached\(ctx,\s*(\d+)\)',
            lambda m: f'guest_region_trace::Indirect(ctx,{m[1]},{owner})', rewritten)
        if head[1] == 'func_' + ROOT:
            # IOS can longjmp out of guest code. Do not introduce a nontrivial
            # C++ destructor across that recovery boundary. Unbalanced traces
            # are rejected; gameplay recovery keeps its existing behavior.
            returns=re.findall(r'\breturn\b[^;]*;','\n'.join(code_of(line) for line in rewritten.splitlines()))
            if any(s!='return;' for s in returns):
                raise ValueError('Unqualified root return shape')
            rewritten='\n'.join(code_of(line).replace('return;','{guest_region_trace::EndRoot(ctx,region_trace_selected);return;}')+line[len(code_of(line)):] for line in rewritten.splitlines())
            brace = rewritten.index('{')
            rewritten = rewritten[:brace+1] + '\n    const bool region_trace_selected=guest_region_trace::BeginRoot(ctx);' + rewritten[brace+1:]
        edits.append((head.start(), end, rewritten))
        stats.append(dict(name=head[1], labels=labels, indirect_sites=calls,
                          source_sha256=hashlib.sha256(source.encode()).hexdigest()))
    for start, end, rewritten in reversed(edits):
        text = text[:start] + rewritten + text[end:]
    return text, stats


def run(command, cwd, log):
    result = subprocess.run([str(s) for s in command], cwd=cwd, capture_output=True, text=True)
    log.write_text(result.stdout + result.stderr)
    print(log.name, result.returncode, (result.stdout + result.stderr)[-1600:], flush=True)
    if result.returncode:
        raise RuntimeError('Command failed; see ' + str(log))
    return result.stdout


def build(args):
    qualified_owners()
    base, corrected, work = args.base.resolve(), args.corrected.resolve(), args.work.resolve()
    if work == PORT or PORT in work.parents:
        raise ValueError('Generated experiment artifacts must be outside the repo')
    work.mkdir(parents=True, exist_ok=True)
    baseline = corrected / 'CrashNative.exe'
    digest = hashlib.file_digest(baseline.open('rb'), 'sha256').hexdigest()
    if digest != '1bd614ef2b33c789e978f04cc1dc13a4f1c4ee10642ca8229a653980c66dcc70':
        raise ValueError('Expected the rendering-corrected 2026.10.17-2 baseline')
    toolchain = base / 'storage/toolchain'
    commands = subprocess.run(['ninja', '-t', 'commands'], cwd=toolchain, capture_output=True, text=True, check=True).stdout.splitlines()
    link = json.loads((corrected / 'link-command.json').read_text())
    compiler = Path(link[0]).with_name('g++.exe')
    rsp = (corrected / 'link.rsp').read_text()
    (work / 'guest_region_trace.h').write_text(HEADER)
    rows = []
    found = set()
    definition_added = False
    staged_inputs = [(p,'crash-hot') for p in sorted((base / 'native-port/build/crash-hot').glob('hot_*.cpp'))]
    staged_inputs += [(p,'crash-split') for p in sorted((base / 'native-port/build/crash-split').glob('output_*.cpp'))]
    for staged, tree in staged_inputs:
        source = corrected / staged.name
        if not source.is_file():
            source = staged
        text = source.read_text()
        # Avoid creating output files for untouched translation units.
        patched, stats = instrument(text)
        if not stats:
            continue
        found.update(s['name'][5:] for s in stats if s['name'].startswith('func_'))
        patched = '#include "guest_region_trace.h"\n' + patched
        if not definition_added:
            patched += DEFINITIONS
            definition_added = True
        cpp = work / source.name
        obj = work / (source.name + '.obj')
        log = work / (source.stem+'-compile.log')
        suffix = 'native-port/build/' + tree + '/' + source.name + '.obj'
        command = next(s for s in commands if suffix in s.replace('\\', '/') and ' -c ' in s)
        argv = shlex.split(command, posix=True)
        argv[0] = str(compiler)
        for flag in ('-o', '-MT', '-MF'):
            argv[argv.index(flag)+1] = str(obj) + ('.d' if flag == '-MF' else '')
        argv[argv.index('-c')+1] = str(cpp)
        signature=dict(command=argv,source_sha256=hashlib.sha256(patched.encode()).hexdigest(),
            support_sha256=hashlib.sha256(HEADER.encode()).hexdigest(),baseline_sha256=digest)
        stamp=work/(source.name+'.compile-input.json')
        reuse=(args.resume and cpp.is_file() and cpp.read_text()==patched and obj.is_file()
            and stamp.is_file() and json.loads(stamp.read_text())==signature
            and log.is_file() and not log.read_text().strip()
            and obj.stat().st_mtime_ns>=cpp.stat().st_mtime_ns)
        if not reuse:cpp.write_text(patched)
        matches = [s for s in rsp.splitlines() if suffix in s.replace('\\', '/')]
        if not matches:
            matches = [s for s in rsp.splitlines() if s.replace('\\', '/').endswith('/'+source.name+'.obj')]
        if len(matches) != 1:
            raise ValueError('Ambiguous baseline object: ' + source.name)
        rsp = rsp.replace(matches[0], str(obj).replace('\\', '/'))
        if reuse:print(source.name,'reuse identical completed compile',flush=True)
        else:
            run(argv, toolchain, log)
            stamp.write_text(json.dumps(signature,indent=2))
        rows.append(dict(file=str(source), sha256=hashlib.file_digest(source.open('rb'), 'sha256').hexdigest(), functions=stats))
    if found != set(OWNERS):
        raise ValueError('Missing selected functions: ' + repr(set(OWNERS)-found))
    (work / 'link.rsp').write_text(rsp)
    link = [s.replace(str(corrected / 'link.rsp'), str(work / 'link.rsp')) for s in link]
    link[link.index('-o')+1] = str(work / 'CrashNative.exe')
    for i, s in enumerate(link):
        if s.startswith('-Wl,--out-implib,'):
            link[i] = '-Wl,--out-implib,' + str(work / 'libCrashNative.dll.a')
    run(link, toolchain, work / 'link.log')
    output = run([compiler.with_name('nm.exe'), '-n', '-C', work/'CrashNative.exe'], toolchain, work/'symbols.log')
    (work/'symbols.txt').write_text(output)
    metadata = dict(baseline_sha256=digest, executable_sha256=hashlib.file_digest((work/'CrashNative.exe').open('rb'),'sha256').hexdigest(), owners=OWNERS, capacity=CAPACITY, max_root_calls=64, files=rows, timing_build=False)
    (work/'trace-build.json').write_text(json.dumps(metadata, indent=2))
    print(json.dumps({k:v for k,v in metadata.items() if k!='files'}, indent=2))


def capture(args):
    work = args.work.resolve()
    metadata = json.loads((work/'trace-build.json').read_text())
    ps = "Get-Process CrashNative | ForEach-Object { @{pid=$_.Id;path=$_.Path;base=$_.MainModule.BaseAddress.ToInt64()} } | ConvertTo-Json -Compress"
    process = json.loads(subprocess.run(['powershell','-NoProfile','-Command',ps],capture_output=True,text=True,check=True).stdout.lstrip('\ufeff'))
    if not isinstance(process,dict):
        raise ValueError('Expected exactly one running game')
    digest = hashlib.file_digest(Path(process['path']).open('rb'),'sha256').hexdigest()
    if digest != metadata['executable_sha256']:
        raise ValueError('Running game does not match this experiment')
    symbols = {}
    for line in (work/'symbols.txt').read_text().splitlines():
        m = re.match(r'([0-9a-fA-F]+) [A-Za-z] (.*)',line)
        if m:
            symbols[m[2]] = int(m[1],16)-0x140000000+process['base']
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD,W.BOOL,W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.ReadProcessMemory.argtypes = [W.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = W.BOOL
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.OpenProcess(0x1010,False,process['pid'])
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    def read(name,size):
        buffer=ctypes.create_string_buffer(size);got=ctypes.c_size_t()
        if not kernel.ReadProcessMemory(handle,symbols[name],buffer,size,ctypes.byref(got)) or got.value!=size:
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.raw
    try:
        state=struct.unpack('<I',read('guest_region_trace_state',4))[0]
        completed=struct.unpack('<I',read('guest_region_trace_completed',4))[0]
        count=struct.unpack('<Q',read('guest_region_trace_count',8))[0]
        if count>CAPACITY:
            raise ValueError('Invalid capture size')
        raw=read('guest_region_trace_rows',count*16) if count else b''
        rows=[dict(pc=f'{pc:08X}',invocation=tag>>16,owner=(tag>>8)&255,kind=tag&255,ic=ic) for pc,tag,ic in struct.iter_unpack('<IIQ',raw)]
        result=dict(pid=process['pid'],sha256=digest,state=state,completed=completed,count=count,owners=metadata['owners'],timing_capture=False,rows=rows)
        out=work/('trace-'+time.strftime('%Y%m%d-%H%M%S')+'.json')
        out.write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2));print(out)
    finally:
        kernel.CloseHandle(handle)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='mode',required=True)
    p=sub.add_parser('build');p.add_argument('--base',type=Path,required=True);p.add_argument('--corrected',type=Path,required=True);p.add_argument('--work',type=Path,required=True)
    p.add_argument('--resume',action='store_true',help='Reuse identical completed source/object pairs from this same build plan')
    p=sub.add_parser('capture');p.add_argument('--work',type=Path,required=True)
    args=parser.parse_args()
    (build if args.mode=='build' else capture)(args)


if __name__=='__main__':
    main()

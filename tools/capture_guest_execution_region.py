"""Read-only, hash-qualified same-process A/B measurement of a region build.

Windows guest/render CPU and cycle accounting are separate from elapsed frame
time and HUD parser scope time. inst_count is the existing approximate counter,
not retired PPC instructions. Root RDTSCP totals are elapsed TSC ticks only.
"""
from pathlib import Path
import argparse
import collections
import ctypes as C
import ctypes.wintypes as W
import hashlib
import json
import re
import statistics
import struct
import subprocess
import time


def summarize(rows):
    groups=collections.defaultdict(list)
    for row in rows:
        if row['epoch'] and row['mode'] in (1,2):groups[(row['epoch'],row['mode'])].append(row)
    windows=[]
    for (epoch,mode),group in sorted(groups.items()):
        # Discard transition edges and allow renderer work to drain from the
        # preceding variant. All contributing snapshots have stable tags.
        usable=[r for r in group if r['time_ns']>=group[0]['time_ns']+500_000_000 and r['time_ns']<=group[-1]['time_ns']-300_000_000]
        if len(usable)<20:continue
        lo,hi=usable[0],usable[-1];xfb=hi['xfb']-lo['xfb'];units=hi['inst_count']-lo['inst_count']
        if xfb<=0 or units<=0:continue
        seconds=(hi['time_ns']-lo['time_ns'])/1e9
        result=dict(epoch=epoch,variant='baseline' if mode==1 else 'region',seconds=seconds,xfb=xfb,
            counter_units_per_xfb=units/xfb,whole_frame_ms=seconds*1000/xfb,threads={},scopes={})
        for role in ('guest','render'):
            a,b=lo['threads'][role],hi['threads'][role]
            cycles=b['cycles']-a['cycles'];ticks=(b['kernel_100ns']+b['user_100ns'])-(a['kernel_100ns']+a['user_100ns'])
            result['threads'][role]=dict(cpu_ms_per_xfb=ticks/10000/xfb,windows_cycles_per_counter_unit=cycles/units)
        for key,value in hi['scopes'].items():
            if key in lo['scopes']:
                delta=value-lo['scopes'][key]
                if delta>=0:result['scopes'][key]=delta/1000/xfb
        index=mode-1;roots=hi['completed'][index]-lo['completed'][index]
        root_units=hi['root_units'][index]-lo['root_units'][index];root_tsc=hi['root_tsc'][index]-lo['root_tsc'][index]
        result['root']=dict(completed=roots,attempted=hi['attempts'][index]-lo['attempts'][index],
            elapsed_tsc_ticks_per_call=root_tsc/roots if roots else None,
            elapsed_tsc_ticks_per_counter_unit=root_tsc/root_units if root_units else None,
            counter_units_per_call=root_units/roots if roots else None)
        windows.append(result)
    aggregate={}
    for variant in ('baseline','region'):
        selected=[w for w in windows if w['variant']==variant]
        if not selected:continue
        aggregate[variant]=dict(windows=len(selected),
            median_guest_cpu_ms_per_xfb=statistics.median(w['threads']['guest']['cpu_ms_per_xfb'] for w in selected),
            median_render_cpu_ms_per_xfb=statistics.median(w['threads']['render']['cpu_ms_per_xfb'] for w in selected),
            median_windows_guest_cycles_per_counter_unit=statistics.median(w['threads']['guest']['windows_cycles_per_counter_unit'] for w in selected),
            median_counter_units_per_xfb=statistics.median(w['counter_units_per_xfb'] for w in selected),
            median_whole_frame_ms=statistics.median(w['whole_frame_ms'] for w in selected),
            median_parser_scope_ms_per_xfb=statistics.median(w['scopes'].get('render:gx.parse',0) for w in selected))
    return windows,aggregate


def main(args):
    work=args.work.resolve();metadata=json.loads((work/'build.json').read_text())
    ps="Get-Process CrashNative | ForEach-Object { @{pid=$_.Id;path=$_.Path;base=$_.MainModule.BaseAddress.ToInt64()} } | ConvertTo-Json -Compress"
    process=json.loads(subprocess.run(['powershell','-NoProfile','-Command',ps],capture_output=True,text=True,check=True).stdout.lstrip('\ufeff'))
    if not isinstance(process,dict):raise ValueError('Expected exactly one running game')
    digest=hashlib.file_digest(Path(process['path']).open('rb'),'sha256').hexdigest()
    if digest!=metadata['executable_sha256']:raise ValueError('Running game does not match the measured build')
    symbols={}
    for line in (work/'symbols.txt').read_text().splitlines():
        m=re.match(r'([0-9a-fA-F]+) [A-Za-z] (.*)',line)
        if m:symbols[m[2]]=int(m[1],16)-0x140000000+process['base']
    k=C.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
    k.ReadProcessMemory.argtypes=[W.HANDLE,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)];k.ReadProcessMemory.restype=W.BOOL
    k.OpenThread.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenThread.restype=W.HANDLE
    k.QueryThreadCycleTime.argtypes=[W.HANDLE,C.POINTER(C.c_ulonglong)];k.QueryThreadCycleTime.restype=W.BOOL
    k.GetThreadTimes.argtypes=[W.HANDLE,C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p];k.GetThreadTimes.restype=W.BOOL
    k.CloseHandle.argtypes=[W.HANDLE]
    handle=k.OpenProcess(0x1010,False,process['pid']);threads={}
    if not handle:raise C.WinError(C.get_last_error())
    def read(address,size):
        buffer=C.create_string_buffer(size);got=C.c_size_t()
        if not k.ReadProcessMemory(handle,address,buffer,size,C.byref(got)) or got.value!=size:raise C.WinError(C.get_last_error())
        return buffer.raw
    def q(name):return struct.unpack('<Q',read(symbols[name],8))[0]
    def pair(name):return list(struct.unpack('<QQ',read(symbols[name],16)))
    def tag():
        value=q('execution_region_tag');return value>>2,value&3
    def thread_counts(th):
        cycles=C.c_ulonglong();times=[C.c_ulonglong() for _ in range(4)]
        if not k.QueryThreadCycleTime(th,C.byref(cycles)) or not k.GetThreadTimes(th,*[C.byref(v) for v in times]):raise C.WinError(C.get_last_error())
        return dict(cycles=cycles.value,kernel_100ns=times[2].value,user_100ns=times[3].value)
    slot_names={}
    def scopes(tids):
        # The MinGW vector stores three pointers; append-only Slot addresses
        # remain stable. Retry a changed header instead of trusting a resize.
        address=symbols['native_hud::slots']
        for _ in range(3):
            before=read(address,24);start,end,_=struct.unpack('<QQQ',before)
            if end<start or (end-start)%8 or end-start>4096*8:raise ValueError('Invalid HUD slot vector')
            pointers=struct.unpack('<'+'Q'*((end-start)//8),read(start,end-start)) if end>start else ()
            if read(address,24)==before:break
        else:raise ValueError('HUD slot vector kept changing')
        totals=collections.Counter()
        for pointer in pointers:
            name,tid,_,self_us=struct.unpack('<QIIQ',read(pointer,24))
            if name not in slot_names:slot_names[name]=read(name,128).split(b'\0',1)[0].decode('utf-8')
            text=slot_names[name]
            role='guest' if tid==tids['guest'] else 'render' if tid==tids['render'] else 'helper'
            totals[role+':'+text]+=self_us
        return dict(totals)
    try:
        ctx=q('nwii::runtime::g_ctx_ptr');tids={}
        if not ctx:raise ValueError('Guest context is not live')
        for role in ('guest','render'):
            tid=struct.unpack('<I',read(symbols['native_hud::'+role+'_tid'],4))[0];tids[role]=tid
            th=k.OpenThread(0x40,False,tid)
            if not th:raise C.WinError(C.get_last_error())
            threads[role]=th
        rows=[];start=time.perf_counter();last_epoch=None
        while time.perf_counter()-start<args.seconds:
            before=tag()
            row=dict(epoch=before[0],mode=before[1],time_ns=time.perf_counter_ns(),inst_count=struct.unpack('<Q',read(ctx+0x20378,8))[0],
                xfb=q('nwii::runtime::gx::s_native_xfb_copies'),threads={role:thread_counts(th) for role,th in threads.items()},
                attempts=pair('execution_region_attempts'),completed=pair('execution_region_completed'),
                root_tsc=pair('execution_region_tsc'),root_units=pair('execution_region_units'),scopes=scopes(tids))
            if tag()==before:
                rows.append(row)
                if row['epoch']!=last_epoch:
                    print('window',row['epoch'],'baseline' if row['mode']==1 else 'region' if row['mode']==2 else 'disarmed',flush=True);last_epoch=row['epoch']
            time.sleep(.1)
        windows,aggregate=summarize(rows)
        result=dict(pid=process['pid'],sha256=digest,tids=tids,windows=windows,aggregate=aggregate,rows=rows,
            limitations=['inst_count is an approximate existing counter, not retired PPC instructions.',
                'QueryThreadCycleTime is Windows scheduled-thread cycle accounting; root RDTSCP values are elapsed TSC ticks.',
                'HUD parser scopes measure exclusive elapsed time, separately from render-thread CPU and whole-frame time.',
                'Alternating windows reduce scene drift; they do not freeze simulation or prove equal executed work.'])
        out=work/('measurement-'+time.strftime('%Y%m%d-%H%M%S')+'.json');out.write_text(json.dumps(result,indent=2))
        print(json.dumps(aggregate,indent=2));print(out)
    finally:
        for th in threads.values():k.CloseHandle(th)
        k.CloseHandle(handle)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--work',type=Path,required=True);ap.add_argument('--seconds',type=int,default=112)
    args=ap.parse_args()
    if args.seconds<1 or args.seconds>600:raise ValueError('Measurement duration must be 1..600 seconds')
    main(args)

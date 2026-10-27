"""Analyze an existing capture, including full-tail symbol-prefix totals.

Leaf aggregation lives in summarize_cpu_etl.py. Never starts WPR or CrashNative.
"""
from pathlib import Path
from port_paths import default_storage
import argparse,json,re,subprocess,sys

def capture_interval(directory,clocks):
    directory=Path(directory);frequency=clocks['start']['qpc_frequency']
    if (directory/'profile-window.json').is_file():
        window=json.loads((directory/'profile-window.json').read_text())
        if window['qpc_frequency']!=frequency or window.get('frame_aligned') is not False:raise RuntimeError('Invalid independent profile window')
        lo,hi=window['start_qpc'],window['stop_qpc']
    else:
        with (directory/'trace.json').open() as stream:head=stream.read(512)
        start,stop=map(int,re.search(r'"start_us":(\d+),"stop_us":(\d+)',head).groups())
        lo,hi=start*frequency//1000000,stop*frequency//1000000
    if not clocks['start']['qpc']<=lo<hi<=clocks['stop']['qpc']:raise RuntimeError('Profile interval does not fit QPC capture bridges')
    return lo,hi,frequency

def main():
    ap=argparse.ArgumentParser();ap.add_argument('capture',type=Path);ap.add_argument('--decoder',type=Path,help='Use an already built ETL reader; do not compile while the game is open.');a=ap.parse_args();p=a.capture.resolve()
    root=Path(__file__).resolve().parent;build=default_storage()/'toolchain'
    build.mkdir(parents=True,exist_ok=True);exe=build/'read_cpu_etl.exe'
    if a.decoder:
        exe=a.decoder.resolve()
        if not exe.is_file():raise RuntimeError('Prebuilt ETL decoder missing')
    else:subprocess.run(['g++','-std=c++17','-O2','-static',str(root/'read_cpu_etl.cpp'),'-o',str(exe),'-ladvapi32'],check=True)
    proc=json.loads((p/'cpu-profile-process.json').read_text(encoding='utf-8-sig'))
    clocks=json.loads((p/'cpu-profile-clocks.json').read_text());lo,hi,freq=capture_interval(p,clocks)
    # This tracer's Windows steady_clock uses QPC epoch. Reject mismatched clocks
    # rather than accidentally analyze the wrong interval on another platform.
    subprocess.run([str(exe),str(p/'cpu-profile.etl'),str(p/'cpu-decoded'),str(lo),str(hi),*map(str,proc['threads'])],check=True)
    meta=json.loads((p/'cpu-decoded.meta.json').read_text())
    if meta['frequency']!=freq:raise RuntimeError('ETL QPC frequency disagrees with captured clock bridge')
    subprocess.run([sys.executable,str(root/'summarize_cpu_etl.py'),str(p)],check=True)
    subprocess.run([sys.executable,str(root/'profile_tls_callers.py'),str(p)],check=True)
if __name__=='__main__':main()

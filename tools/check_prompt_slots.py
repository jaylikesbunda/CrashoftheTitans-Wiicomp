"""Exercise production font identity resolution without running the game or a window."""
from pathlib import Path
import argparse,subprocess,tempfile,shutil
from port_paths import winlibs_bin
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--captured-banks',nargs=2,type=Path);args=parser.parse_args()
with tempfile.TemporaryDirectory(prefix='prompt-slots-') as temp:
 exe=Path(temp)/'check.exe';compiler=shutil.which('g++') or str(winlibs_bin()/'g++.exe')
 subprocess.run([compiler,'-std=c++17','-O2','-Wall','-Wextra','-I',str(root/'runtime-overlays'),str(root/'tools/prompt_slots_tests.cpp'),'-o',str(exe)],check=True)
 subprocess.run([str(exe),*(str(p) for p in (args.captured_banks or []))],check=True)

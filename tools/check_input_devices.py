"""Check production SDL input glue with virtual controllers in a silent hidden fixture."""
from pathlib import Path
import argparse, os, shutil, subprocess, tempfile
from port_paths import winlibs_bin
PORT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--storage',type=Path,required=True,help='Completed native build storage with the pinned static SDL library')
    args=parser.parse_args()
    rt=args.storage/'nwii-full-source/nWiiRuntime'
    sdl=args.storage/'toolchain/_deps/sdl2-build'
    with tempfile.TemporaryDirectory(prefix='input-devices-') as directory:
        root=Path(directory);(root/'runtime').mkdir()
        (root/'runtime/native_prompt_device.h').write_bytes((PORT/'runtime-overlays/native_prompt_device.h').read_bytes())
        (root/'input_mapping.h').write_bytes((PORT/'runtime-overlays/input_mapping.h').read_bytes())
        (root/'mapping.txt').write_text('a=South,Key:J\nb=East,Mouse:Right\n',encoding='utf-8')
        (root/'maps').mkdir()
        shutil.copy2(root/'mapping.txt',root/'maps/controller-0.txt')
        (root/'maps/prompt-mode.txt').write_text('6 auto\n')
        exe=root/'check.exe'
        compiler=shutil.which('g++') or str(winlibs_bin()/'g++.exe')
        command=[compiler,'-std=c++17','-O2','-I',str(root),'-I',str(rt/'include'),'-I',str(rt/'src/input/sources'),'-I',str(sdl/'include'),'-I',str(sdl/'include/SDL2'),'-I',str(sdl/'include-config-relwithdebinfo/SDL2'),str(PORT/'tools/input_devices_tests.cpp'),str(PORT/'runtime-overlays/gamepad_source_mapped.cpp'),str(sdl/'libSDL2.a')]
        command += ['-l'+name for name in ['m','kernel32','user32','gdi32','winmm','imm32','ole32','oleaut32','version','uuid','advapi32','setupapi','dinput8','shell32']]
        subprocess.run(command+['-o',str(exe)],check=True)
        env=os.environ.copy()
        env.update(NWII_INPUT_MAP=str(root/'mapping.txt'),NWII_PROMPT_PACK='device-tracking-only',SDL_AUDIODRIVER='dummy',SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT='0x045e/0x02ea,0x054c/0x05c4')
        env['NWII_INPUT_MAP_DIR']=str(root/'maps')
        subprocess.run([str(exe)],env=env,check=True,timeout=15)

if __name__=='__main__':main()

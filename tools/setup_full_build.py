"""Stage full runtime and map-driven exporter without touching pinned checkouts."""
from pathlib import Path
import shutil
from lazy_fpu_header import inject_lazy_fpu_header
PORT=Path(__file__).resolve().parents[1];SRC=PORT/'build/nwii-full-source'
def write_changed(path,text):
    if not path.exists() or path.read_text()!=text:path.write_text(text)

def stage_game_emitter_cmake(text, emitter_source):
    # The generator must rebuild while the runtime is only partly staged.
    # Its loader/analyzer/recompiler sources do not need runtime services.
    old=f'\nadd_executable(crash_game_emit "{emitter_source}")\ntarget_link_libraries(crash_game_emit PRIVATE nwiirecomp_lib)\n'
    new=f'''
# NATIVE_STANDALONE_GAME_EMITTER_V1
add_executable(crash_game_emit
    "{emitter_source}"
    src/recompiler.cpp
    src/symbols.cpp
    ../nWiiAnalyzer/src/analyzer.cpp
    ../nWiiAnalyzer/src/signature_scanner.cpp
    ../nWiiAnalyzer/src/dolphin_sigdb.cpp
    ../nWiiRuntime/src/core/loader.cpp
)
target_include_directories(crash_game_emit PRIVATE
    include ../nWiiAnalyzer/include ../nWiiRuntime/include
)
'''
    if new in text:return text
    if old in text:return text.replace(old,new,1)
    if 'crash_game_emit' in text:raise SystemExit('Game emitter target context changed')
    return text+new
if not SRC.exists():shutil.copytree(PORT/'vendor/NWiiRecomp',SRC,ignore=shutil.ignore_patterns('.git'))
p=SRC/'CMakeLists.txt';s=p.read_text().replace('add_subdirectory(nWiiStudio)','');write_changed(p,s)
p=SRC/'nWiiRuntime/include/runtime/cpu_context.h'
header=(PORT/'vendor/NWiiRecomp/nWiiRuntime/include/runtime/cpu_context.h').read_text()
for before,after in (
    ('float scale = std::pow(2.0f, -((int)ld_scale));',
     'int signed_scale = (ld_scale & 32u) ? int(ld_scale) - 64 : int(ld_scale);\n    float scale = std::ldexp(1.0f, -signed_scale);'),
    ('float scale = std::pow(2.0f, (int)st_scale);',
     'int signed_scale = (st_scale & 32u) ? int(st_scale) - 64 : int(st_scale);\n    float scale = std::ldexp(1.0f, signed_scale);')):
    if header.count(before)!=1:raise SystemExit('Quantization patch context changed')
    header=header.replace(before,after)
write_changed(p,inject_lazy_fpu_header(header))
p=SRC/'nWiiAnalyzer/include/analyzer/analyzer.h';s=p.read_text();needle='    void analyze(const std::vector<uint32_t>& additional_roots = {});'
if 'set_imported_functions' not in s:s=s.replace(needle,needle+'\n    void set_imported_functions(std::map<uint32_t, Function> functions) { functions_ = std::move(functions); }\n')
write_changed(p,s)
# GCC does not honor phone_source.cpp's MSVC pragma for Winsock linkage.
p=SRC/'nWiiRuntime/CMakeLists.txt';s=p.read_text()
if '# Native Windows libraries' not in s:
    s+='\n# Native Windows libraries\nif(WIN32)\n    target_link_libraries(nwiiruntime PUBLIC ws2_32)\nendif()\n'
write_changed(p,s)
# MinGW x64's low-level setjmp also takes an unwind-frame argument.
# A null frame retains upstream's non-unwinding guest-context switch.
p=SRC/'nWiiRecomp/src/recompiler.cpp';s=p.read_text()
old='    out << "      if (_setjmp(ctx.exception_jmp_buf) == 0) {\\n";'
new='    out << "#if defined(__MINGW64__)\\n      if (_setjmp(ctx.exception_jmp_buf, nullptr) == 0) {\\n#else\\n      if (_setjmp(ctx.exception_jmp_buf) == 0) {\\n#endif\\n";'
if old in s:s=s.replace(old,new)
elif new not in s:raise SystemExit('Dispatcher patch context changed')
write_changed(p,s)
# Apply the same compatibility-only edit to already generated output so all
# translated functions need not be re-emitted and recompiled for this repair.
g=PORT/'build/crash-generated/main_output.cpp'
if g.exists():
    s=g.read_text();old='      if (_setjmp(ctx.exception_jmp_buf) == 0) {'
    new='#if defined(__MINGW64__)\n      if (_setjmp(ctx.exception_jmp_buf, nullptr) == 0) {\n#else\n'+old+'\n#endif'
    if '#if defined(__MINGW64__)' not in s:s=s.replace(old,new)
    write_changed(g,s)
p=SRC/'nWiiRecomp/CMakeLists.txt';s=p.read_text()
s=stage_game_emitter_cmake(s,(PORT/'tools/game_emit.cpp').as_posix())
write_changed(p,s)
print('Full runtime staging updated; standalone Studio omitted, all runtime services included.')

# Use the same complete runtime/dependency build for the generated game.
# This avoids compiling another copy of SDL and does not trim runtime services.
generated=PORT/'build/crash-generated'
if (generated/'main_output.cpp').exists():
    p=SRC/'CMakeLists.txt';s=p.read_text()
    if '# Crash native target' not in s:
        files=sorted(generated.glob('output_*.cpp'))+[generated/'main_output.cpp']
        sources='\n'.join(f'    "{f.as_posix()}"' for f in files)
        s+=('\n# Crash native target\nadd_executable(CrashNative\n'
            '    "${CMAKE_CURRENT_SOURCE_DIR}/nWiiRuntime/src/core/main.cpp"\n'
            +sources+'\n)\ntarget_link_libraries(CrashNative PRIVATE nwiiruntime)\n'
            'target_include_directories(CrashNative PRIVATE nWiiRuntime/src)\n'
            'if(MINGW)\n    target_link_options(CrashNative PRIVATE -static -Wl,--stack,33554432)\nendif()\n'
            'if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")\n'
            '    target_compile_options(CrashNative PRIVATE -O0 -g1)\nendif()\n')
    if '# SDL Windows entry point' not in s:
        s+='\n# SDL Windows entry point\nif(WIN32)\n    target_link_libraries(CrashNative PRIVATE SDL2main)\nendif()\n'
    if '# Crash measured hot optimization' not in s:
        hot=[generated/'output_0.cpp',generated/'output_46.cpp',generated/'main_output.cpp']
        s+='\n# Crash measured hot optimization\noption(NWII_CRASH_HOT_OPTIMIZATION "Optimize measured integer hot code and dispatcher" ON)\n'
        s+='if(NWII_CRASH_HOT_OPTIMIZATION AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")\n'
        s+='    set_source_files_properties(\n'+''.join(f'        "{f.as_posix()}"\n' for f in hot)
        s+='        PROPERTIES COMPILE_OPTIONS "-O1")\nendif()\n'
    if '# Crash all-chunk optimization' not in s:
        allfiles=sorted(generated.glob('output_*.cpp'))+[generated/'main_output.cpp']
        s+='\n# Crash all-chunk optimization\noption(NWII_CRASH_OPT_ALL "Optimize every generated chunk (-O2)" ON)\n'
        s+='if(NWII_CRASH_OPT_ALL AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")\n'
        s+='    set_source_files_properties(\n'+''.join(f'        "{f.as_posix()}"\n' for f in allfiles)
        s+='        PROPERTIES COMPILE_OPTIONS "-O2;-fwrapv;-fno-strict-aliasing;-ffp-contract=off")\nendif()\n'
    if '# Crash main.cpp optimization' not in s:
        s+='\n# Crash main.cpp optimization\nif(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")\n    set_source_files_properties("${CMAKE_CURRENT_SOURCE_DIR}/nWiiRuntime/src/core/main.cpp" PROPERTIES COMPILE_OPTIONS "-O1")\nendif()\n'
    if '# Crash callback benchmark' not in s:
        s+='\n# Crash callback benchmark\nadd_executable(crash_callback_bench "'+(PORT/'tools/callback_benchmark.cpp').as_posix()+'")\n'
        s+='target_link_libraries(crash_callback_bench PRIVATE nwiiruntime)\n'
    if '# Crash callback context tests' not in s:
        s+='\n# Crash callback context tests\nadd_executable(crash_callback_context_tests "'+(PORT/'tools/callback_context_tests.cpp').as_posix()+'")\n'
        s+='target_link_libraries(crash_callback_context_tests PRIVATE nwiiruntime)\n'
    write_changed(p,s)
    p=generated/'CMakeLists.txt';s=p.read_text()
    if '# Crash Windows compatibility' not in s:
        s+='\n# Crash Windows compatibility\nif(WIN32)\n    target_link_libraries(CrashNative PRIVATE SDL2main)\nendif()\nif(MINGW)\n    target_link_options(CrashNative PRIVATE -static -Wl,--stack,33554432)\nendif()\nif(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")\n    target_compile_options(CrashNative PRIVATE -O0 -g1)\nendif()\n'
    write_changed(p,s)

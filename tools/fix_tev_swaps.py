"""Apply each TEV stage's raster and texture component swap tables."""
from pathlib import Path
PORT=Path(__file__).resolve().parents[1]
p=PORT/'build/nwii-full-source/nWiiRuntime/src/hle/gx/tev_shader_gen.cpp';s=p.read_text()
marker='        // NATIVE_TEV_SWAPS\n'
if marker not in s:
    needle='        uint32_t ksel_reg = state.bp[0xF6 + (i / 2)];'
    assert s.count(needle)==1
    code='''        // NATIVE_TEV_SWAPS
        const unsigned swaps=state.bp[0xc1+2*i];
        auto swizzle=[&](unsigned table) {
            const unsigned rg=state.bp[0xf6+table*2], ba=state.bp[0xf7+table*2];
            const char* channels="rgba";std::string result;
            result+=channels[rg&3];result+=channels[(rg>>2)&3];
            result+=channels[ba&3];result+=channels[(ba>>2)&3];return result;
        };
        fs << "    rasColor = rasColor." << swizzle(swaps&3) << ";\\n";
        fs << "    texColor = texColor." << swizzle((swaps>>2)&3) << ";\\n";
'''
    p.write_text(s.replace(needle,code+needle,1))
print('TEV raster/texture swap tables staged.')

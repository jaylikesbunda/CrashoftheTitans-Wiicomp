"""Remove the same-PC heuristic that consumes a DEC IRQ without delivery.

The guest idle loop is a normal repeated interrupt PC. Scanning SDA RAM for
alarm-like words and changing future timestamps is not hardware emulation.
"""
from pathlib import Path
port=Path(__file__).resolve().parents[1]
p=port/'build/nwii-full-source/nWiiRuntime/src/hle/ios.cpp';text=p.read_text()
marker='NATIVE_DEC_NO_REPEAT_DROP_V1'
if marker not in text:
    start=text.index('      {\n          static uint32_t dec_same_pc_count = 0;')
    end=text.index('      static uint32_t dcount = 0;',start)
    text=text[:start]+'      // '+marker+': deliver every due timer, including repeated idle PCs.\n\n'+text[end:]
assert 'dec_same_pc_count' not in text
assert 'force-expired alarm' not in text
if p.read_text()!=text:p.write_text(text)
p=port/'tools/build_native.py';text=p.read_text()
if "run('native-dec-repeat-drop-stage'" not in text:
    needle="run('native-game-build',"
    assert text.count(needle)==1
    text=text.replace(needle,"run('native-dec-repeat-drop-stage',[sys.executable,PORT/'tools/fix_dec_repeat_drop.py'])\n"+needle,1)
if p.read_text()!=text:p.write_text(text)
print('Repeated-PC DEC interrupts are delivered; no alarm scan or timestamp fabrication.')

"""Yield callback return sentinels to the dispatcher before fetching memory."""
from pathlib import Path

port = Path(__file__).resolve().parents[1]
path = port / 'build/nwii-full-source/nWiiRuntime/src/hle/interpreter.cpp'
source = path.read_text()
marker = '// NATIVE_INTERPRETER_CALLBACK_RETURN_V1'
if marker not in source:
    anchor = '        interpret_one(ctx);'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + '''
        // NATIVE_INTERPRETER_CALLBACK_RETURN_V1
        // This is a host dispatch boundary, not executable guest memory.
        // Return even with EE clear; the dispatcher owns caller restoration.
        if (ctx.pc == 0xFFFFFFFCu) return;
''', 1)
    path.write_text(source)
print('Interpreter callback return boundary staged')

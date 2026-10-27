"""Controller mapping, gyro pointer and motion for the physical Wii remote + Nunchuk adapter (launcher "Controls" page).

Installs, into the staged runtime:
  * src/input/sources/input_mapping.h     the mapping logic (runtime-overlays/input_mapping.h, unit-tested without SDL)
  * src/input/sources/gamepad_source.cpp  replaced by runtime-overlays/gamepad_source_mapped.cpp (SDL glue)
  * include/input/input_source.h          WiimoteState gains the Nunchuk accelerometer
  * src/hle/ios.cpp                       the emulated WPAD packets carry the accelerometers (they were always reported at rest)
  * src/input/sources/mouse_keyboard_source.cpp  loses its fixed Wii-button keys and mouse buttons; player 1's keyboard and mouse
                                          bindings now come from the mapping (input_mapping.h), so they can be rebound
The launcher writes NWII_INPUT_MAP; without it the previous fixed mapping applies unchanged.

Contained and fail-soft: every anchor is checked before anything is written. If one has moved (another stage changed that code), nothing is
installed, a one-line reason is printed and the stage exits 0, so the build is never broken by this feature. Idempotent.
"""
from pathlib import Path
import sys

MARK = 'NATIVE_INPUT_MAP_V1'
RAW = ('auto raw=[](float g)->uint16_t{int v=static_cast<int>(g*204.f+(g>=0.f?0.5f:-0.5f)); v=v<-511?-511:v>511?511:v; '
       'return static_cast<uint16_t>(static_cast<int16_t>(v));};')

# (file key, anchor line, new text, mode); mode 'after' keeps the anchor and appends, 'replace' substitutes the anchor.
EDITS = [
    ('header', '    int8_t nunchuk_x = 0, nunchuk_y = 0;\n',
     '    float nunchuk_ax = 0, nunchuk_ay = 0, nunchuk_az = 1; // ' + MARK + ': Nunchuk accelerometer, g\n', 'after'),
    ('wpad', '  uint16_t buttons=0; int8_t sx=0, sy=0;\n',
     '  float ax=0,ay=0,az=1, nax=0,nay=0,naz=1; // ' + MARK + ': accelerometers in g (remote, Nunchuk)\n', 'after'),
    ('wpad', '    s.ir_x=state.ir_x; s.ir_y=state.ir_y; s.ir_valid=connected; // NATIVE_WPAD_IR_V1\n',
     '    s.ax=state.accel_x; s.ay=state.accel_y; s.az=state.accel_z; s.nax=state.nunchuk_ax; s.nay=state.nunchuk_ay; s.naz=state.nunchuk_az; // ' + MARK + '\n',
     'after'),
    ('wpad', '  ctx.mmu.write16(p+6,204); // zero-calibrated stationary +1g, SDK calibration fallback\n',
     '  { // ' + MARK + ': accelerometers, 204 counts per g (the gravity unit reported by 803EC364), signed, 10-bit range\n'
     '    auto& ms=native_wpad_state();\n'
     '    ' + RAW + '\n'
     '    ctx.mmu.write16(p+2,raw(ms.ax)); ctx.mmu.write16(p+4,raw(ms.ay)); ctx.mmu.write16(p+6,raw(ms.az));\n'
     '  }\n', 'replace'),
    ('wpad', '  if (format>=3 && format<6) ctx.mmu.write16(p+0x2E,204);\n',
     '  if (format>=3 && format<6) { // ' + MARK + '\n'
     '    auto& ms=native_wpad_state();\n'
     '    ' + RAW + '\n'
     '    ctx.mmu.write16(p+0x2A,raw(ms.nax)); ctx.mmu.write16(p+0x2C,raw(ms.nay)); ctx.mmu.write16(p+0x2E,raw(ms.naz));\n'
     '  }\n', 'replace'),
]

# The upstream keyboard source pressed Wii buttons from fixed keys; the mapping now owns them.
KEYBOARD_MARK = 'NATIVE_INPUT_MAP_V3'
KEYBOARD_FIXED = (
    '    if (mouse_state & SDL_BUTTON(SDL_BUTTON_LEFT)) motes[0].buttons |= 0x0800; \n'
    '    if (mouse_state & SDL_BUTTON(SDL_BUTTON_RIGHT)) motes[0].buttons |= 0x0400; \n'
    '    if (keys[SDL_SCANCODE_RETURN]) motes[0].buttons |= 0x0010; \n'
    '    if (keys[SDL_SCANCODE_BACKSPACE]) motes[0].buttons |= 0x0001; \n'
    '    if (keys[SDL_SCANCODE_UP]) motes[0].buttons |= 0x0008; \n'
    '    if (keys[SDL_SCANCODE_DOWN]) motes[0].buttons |= 0x0004; \n'
    '    if (keys[SDL_SCANCODE_LEFT]) motes[0].buttons |= 0x0001; \n'
    '    if (keys[SDL_SCANCODE_RIGHT]) motes[0].buttons |= 0x0002; \n')
KEYBOARD_NEW = ('    (void)mouse_state; // ' + KEYBOARD_MARK + ': Wii buttons from keys and mouse buttons come from the mapping '
                '(gamepad_source.cpp, input_mapping.h)\n')


class Skip(Exception):
    pass


def apply_edits(texts):
    """texts: {'header': str, 'wpad': str}. Returns (new texts, number of edits applied); raises Skip if any anchor has moved."""
    out, applied = dict(texts), 0
    for key, anchor, new, mode in EDITS:
        text = out[key]
        first = new.strip().splitlines()[0].strip()
        # The later co-op stage moves the state copy from s to c; region staging
        # also relocates the SDK address in the accelerometer comment.
        installed = first in text
        if key == 'wpad' and 'NATIVE_WPAD_COOP_V1' in text:
            installed = installed or first.replace('s.', 'c.') in text
        if key == 'wpad' and '(the gravity unit reported by ' in first:
            installed = installed or (first.split('(the gravity unit reported by ')[0] in text and
                'ctx.mmu.write16(p+2,raw(ms.ax)); ctx.mmu.write16(p+4,raw(ms.ay)); ctx.mmu.write16(p+6,raw(ms.az));' in text)
        if installed:
            continue                                   # already applied
        if text.count(anchor) != 1:
            raise Skip('%s: expected one occurrence of %r, found %d' % (key, anchor.strip()[:60], text.count(anchor)))
        out[key] = text.replace(anchor, new if mode == 'replace' else anchor + new, 1)
        applied += 1
    return out, applied


def main():
    port = Path(__file__).resolve().parents[1]
    rt = port / 'build/nwii-full-source/nWiiRuntime'
    header, ios = rt / 'include/input/input_source.h', rt / 'src/hle/ios.cpp'
    sources = rt / 'src/input/sources'
    installs = [(port / 'runtime-overlays/input_mapping.h', sources / 'input_mapping.h'),
                (port / 'runtime-overlays/gamepad_source_mapped.cpp', sources / 'gamepad_source.cpp')]
    for p in [header, ios, sources / 'gamepad_source.cpp'] + [i[0] for i in installs]:
        if not p.is_file():
            raise Skip('missing ' + str(p.relative_to(port)))
    before = {'header': header.read_text(), 'wpad': ios.read_text()}
    after, applied = apply_edits(before)
    for key, path in (('header', header), ('wpad', ios)):
        if after[key] != before[key]:
            path.write_text(after[key])
    keyboard = sources / 'mouse_keyboard_source.cpp'
    if keyboard.is_file():
        text = keyboard.read_text()
        if KEYBOARD_MARK not in text:
            if text.count(KEYBOARD_FIXED) != 1:
                raise Skip('mouse_keyboard_source.cpp: the fixed Wii-button block has moved')
            keyboard.write_text(text.replace(KEYBOARD_FIXED, KEYBOARD_NEW, 1))
    for src, dst in installs:
        data = src.read_bytes()
        if not dst.exists() or dst.read_bytes() != data:
            dst.write_bytes(data)
    print('Input mapping installed: %d header/WPAD edits, mapping logic and SDL glue refreshed.' % applied)


if __name__ == '__main__':
    try:
        main()
    except Skip as reason:
        print('Input mapping not installed: ' + str(reason))

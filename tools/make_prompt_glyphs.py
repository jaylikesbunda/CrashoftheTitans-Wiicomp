"""Draw the launcher's button-prompt glyphs (our own art; nothing is copied from the game).

The game draws button prompts inside its text with 32 x 32 glyph textures in its font: light grey buttons with a soft highlight, a heavy
black outline, a faint shadow and black symbols, circles for most buttons, a rounded square for B and a trapezoid for Z. The launcher can
replace those with the buttons of the player's controller; this script draws the replacement set in the same style:

    launcher/src-tauri/assets/prompts/<family>/<input>.png     32 x 32 RGBA
    launcher/src-tauri/src/prompt_glyphs.rs                    the table the launcher embeds

<family> is xbox, playstation or kbm. Controller glyphs are named after the input as the controls page stores it (South, East,
LeftShoulder, DpadUp, ...); keyboard and mouse glyphs after the binding (Key_Space, Key_Left_Shift, Mouse_Left, ...).
Face buttons are circles like the Wii A, bumpers rounded squares like B, triggers trapezoids like Z, Start and Back small circles like
+ and -. Letters use Pillow's bundled Aileron Regular (CC0), thickened to match the game's heavy lettering.

    python tools/make_prompt_glyphs.py
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

PORT = Path(__file__).resolve().parents[1]
OUT = PORT / 'launcher/src-tauri/assets/prompts'
SIZE, K = 32, 8
S = SIZE * K
INK = (0, 0, 0, 255)


def px(v):
    return int(round(v * K))


def mask_for(shape, box):
    m = Image.new('L', (S, S), 0)
    d = ImageDraw.Draw(m)
    x0, y0, x1, y1 = [px(v) for v in box]
    if shape == 'circle':
        d.ellipse((x0, y0, x1, y1), fill=255)
    elif shape == 'square':
        d.rounded_rectangle((x0, y0, x1, y1), radius=px(4.5), fill=255)
    elif shape == 'trapezoid':
        inset = px(1.6)
        d.rounded_rectangle((x0, y0, x1, y1), radius=px(3), fill=255)
        cut = Image.new('L', (S, S), 0)
        ImageDraw.Draw(cut).polygon([(x0, y1), (x0 + inset, y1), (x0, y0 + px(2))], fill=255)
        ImageDraw.Draw(cut).polygon([(x1, y1), (x1 - inset, y1), (x1, y0 + px(2))], fill=255)
        m = ImageChops.subtract(m, cut)
    elif shape == 'cross':
        a = px(4.2)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        d.rounded_rectangle((cx - a, y0, cx + a, y1), radius=px(1.5), fill=255)
        d.rounded_rectangle((x0, cy - a, x1, cy + a), radius=px(1.5), fill=255)
    return m


def button(shape, box, highlight=(0.42, 0.34)):
    """A grey button in the game's style: shadow, outline, body with a soft highlight toward the upper left."""
    outer = mask_for(shape, box)
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    shadow.putalpha(outer.filter(ImageFilter.GaussianBlur(px(1.1))).point(lambda v: v * 120 // 255))
    img.alpha_composite(shadow, (0, px(0.9)))
    rim = Image.new('RGBA', (S, S), INK)
    rim.putalpha(outer)
    img.alpha_composite(rim)
    inner = outer.filter(ImageFilter.MinFilter(px(3.8) | 1))
    hx, hy = int(S * highlight[0]), int(S * highlight[1])
    grad = Image.radial_gradient('L').resize((S * 2, S * 2)).crop((S - hx, S - hy, 2 * S - hx, 2 * S - hy))
    body = Image.merge('RGBA', [grad.point(lambda v: max(178, 244 - v * 230 // 255))] * 3 + [inner])
    img.alpha_composite(body)
    return img


def font(size):
    return ImageFont.load_default(size=px(size))


def letter(img, text, size, box=(0, 0, SIZE, SIZE), weight=1.0, dy=0.0):
    d = ImageDraw.Draw(img)
    f = font(size)
    sw = px(weight)
    l, t, r, b = d.textbbox((0, 0), text, font=f, stroke_width=sw)
    x0, y0, x1, y1 = [px(v) for v in box]
    d.text(((x0 + x1 - (r - l)) / 2 - l, (y0 + y1 - (b - t)) / 2 - t + px(dy)), text, font=f, fill=INK, stroke_width=sw, stroke_fill=INK)


def finish(img):
    return img.resize((SIZE, SIZE), Image.LANCZOS)


FACE = (2.5, 2.5, 29.5, 29.5)       # the Wii A/1/2/C circles
SMALL = (7, 7, 25, 25)              # the Wii + and - circles


def face(text):
    img = button('circle', FACE)
    letter(img, text, 17, FACE, weight=1.15)
    return finish(img)


def ps_face(kind):
    img = button('circle', FACE)
    d = ImageDraw.Draw(img)
    c, r, w = S // 2, px(6.2), px(2.9)
    if kind == 'cross':
        d.line((c - r, c - r, c + r, c + r), fill=INK, width=w); d.line((c - r, c + r, c + r, c - r), fill=INK, width=w)
    elif kind == 'circle':
        d.ellipse((c - r, c - r, c + r, c + r), outline=INK, width=w)
    elif kind == 'square':
        q = px(5.4); d.rectangle((c - q, c - q, c + q, c + q), outline=INK, width=w)
    else:
        h = px(6.6); d.polygon([(c, c - h), (c + h, c + int(h * 0.72)), (c - h, c + int(h * 0.72))], outline=INK, width=w)
    return finish(img)


def bumper(text):                   # like the Wii B
    box = (3, 6, 29, 27)
    img = button('square', box)
    letter(img, text, 11, box, weight=0.55)
    return finish(img)


def trigger(text):                  # like the Wii Z
    box = (3, 3, 29, 29)
    img = button('trapezoid', box)
    letter(img, text, 11, box, weight=0.55)
    return finish(img)


def system(kind):                   # like the Wii + and -
    img = button('circle', SMALL)
    d = ImageDraw.Draw(img)
    c, w = S // 2, px(2.2)
    if kind == 'menu':
        for dy in (-3.2, 0, 3.2):
            d.line((c - px(4.2), c + px(dy), c + px(4.2), c + px(dy)), fill=INK, width=w)
    elif kind == 'view':
        d.rectangle((c - px(4.6), c - px(3.4), c + px(1.2), c + px(1.8)), outline=INK, width=w)
        d.rectangle((c - px(1.4), c - px(1.0), c + px(4.6), c + px(4.2)), fill=(200, 200, 200, 255), outline=INK, width=w)
    else:
        for x in (-3.4, 0, 3.4):
            d.line((c + px(x), c - px(4.2), c + px(x), c + px(4.2)), fill=INK, width=w)
    return finish(img)


def stick(text):
    img = button('circle', FACE)
    letter(img, text, 11, FACE, weight=0.55)
    return finish(img)


def dpad(direction):
    box = (3, 3, 29, 29)
    img = button('cross', box, highlight=(0.5, 0.5))
    c, a = SIZE/2, 4.2
    arm = {'DpadUp': (3,3,29,c-a), 'DpadDown': (3,c+a,29,29),
           'DpadLeft': (3,3,c-a,29), 'DpadRight': (c+a,3,29,29)}[direction]
    selected=Image.new('L',(S,S),0)
    ImageDraw.Draw(selected).rectangle(tuple(px(v) for v in arm),fill=255)
    fill=Image.new('RGBA',(S,S),INK)
    fill.putalpha(ImageChops.multiply(mask_for('cross',box),selected))
    img.alpha_composite(fill)
    return finish(img)


def family(name):
    g = {}
    if name == 'xbox':
        for k, t in {'South': 'A', 'East': 'B', 'West': 'X', 'North': 'Y'}.items(): g[k] = face(t)
        g.update(LeftShoulder=bumper('LB'), RightShoulder=bumper('RB'), LeftTrigger=trigger('LT'), RightTrigger=trigger('RT'),
                 LeftStick=stick('LS'), RightStick=stick('RS'), Start=system('menu'), Back=system('view'))
    else:
        for k, t in {'South': 'cross', 'East': 'circle', 'West': 'square', 'North': 'triangle'}.items(): g[k] = ps_face(t)
        g.update(LeftShoulder=bumper('L1'), RightShoulder=bumper('R1'), LeftTrigger=trigger('L2'), RightTrigger=trigger('R2'),
                 LeftStick=stick('L3'), RightStick=stick('R3'), Start=system('menu'), Back=system('create'))
    for k in ('DpadUp', 'DpadDown', 'DpadLeft', 'DpadRight'):
        g[k] = dpad(k)
    g.update(Guide=stick('PS' if name=='playstation' else 'X'), Touchpad=bumper('PAD'), Misc1=bumper('MIC' if name=='playstation' else 'SHR'))
    for i in range(1,5):g['Paddle%d'%i]=bumper('P%d'%i)
    return g


# Keyboard: every key the controls page can bind (its KeyboardEvent.code -> SDL name table), as a keycap like the Wii B.
KEYS = ([chr(c) for c in range(ord('A'), ord('Z') + 1)] + [str(d) for d in range(10)] + ['Keypad %d' % d for d in range(10)] +
        ['Space', 'Return', 'Keypad Enter', 'Backspace', 'Tab', 'CapsLock', 'Left Shift', 'Right Shift', 'Left Ctrl', 'Right Ctrl',
         'Left Alt', 'Right Alt', 'Up', 'Down', 'Left', 'Right', 'Insert', 'Delete', 'Home', 'End', 'PageUp', 'PageDown',
         '-', '=', '[', ']', ';', "'", ',', '.', '/', '`', '\\', 'Left GUI', 'Right GUI', 'Application',
         'PrintScreen', 'ScrollLock', 'Pause', 'Numlock', 'Keypad /', 'Keypad *', 'Keypad -', 'Keypad +', 'Keypad .', 'Keypad ='] + ['F%d'%i for i in range(1,25)])
KEY_LABEL = {'Space': 'SPACE', 'Return': 'ENTER', 'Keypad Enter': 'ENTER', 'Backspace': 'BKSP', 'Tab': 'TAB', 'CapsLock': 'CAPS',
             'Left Shift': 'SHIFT', 'Right Shift': 'SHIFT', 'Left Ctrl': 'CTRL', 'Right Ctrl': 'CTRL', 'Left Alt': 'ALT', 'Right Alt': 'ALT',
             'Insert': 'INS', 'Delete': 'DEL', 'Home': 'HOME', 'End': 'END', 'PageUp': 'PGUP', 'PageDown': 'PGDN',
             'Left GUI':'WIN','Right GUI':'WIN','Application':'MENU','PrintScreen':'PRT','ScrollLock':'SCRL','Pause':'PAUS','Numlock':'NUM'}
ARROWS = {'Up': 0, 'Right': 90, 'Down': 180, 'Left': 270}


def key_file(name):
    safe = {'Keypad /':'Keypad_Divide','Keypad *':'Keypad_Multiply','Keypad -':'Keypad_Minus','Keypad +':'Keypad_Plus','Keypad .':'Keypad_Period','Keypad =':'Keypad_Equals',
            '\\':'Backslash','-': 'Minus', '=': 'Equals', '[': 'LeftBracket', ']': 'RightBracket', ';': 'Semicolon', "'": 'Quote', ',': 'Comma',
            '.': 'Period', '/': 'Slash', '`': 'Backquote'}.get(name, name)
    return 'Key_' + safe.replace(' ', '_')


def keycap(name):
    box = (2.5, 4, 29.5, 28)
    img = button('square', box)
    if name in ARROWS:
        d = ImageDraw.Draw(img)
        import math
        c = S // 2
        pts = [(0, -6.2), (5.4, 2.6), (-5.4, 2.6)]
        a = math.radians(ARROWS[name])
        d.polygon([(c + px(x * math.cos(a) - y * math.sin(a)), c + px(0.8) + px(x * math.sin(a) + y * math.cos(a))) for x, y in pts], fill=INK)
        return finish(img)
    text = KEY_LABEL.get(name, name.replace('Keypad ', ''))
    size = 15 if len(text) == 1 else 10 if len(text) <= 3 else 7.6 if len(text) <= 4 else 6.6
    letter(img, text, size, box, weight=1.0 if len(text) == 1 else 0.45)
    return finish(img)


def mouse(button_name):
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    body = (8, 3, 24, 29)
    img.alpha_composite(button('square', body, highlight=(0.45, 0.3)))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = [px(v) for v in body]
    mid, split = (x0 + x1) // 2, px(13)
    w = px(1.3)
    d.line((x0, split, x1, split), fill=INK, width=w)
    d.line((mid, y0, mid, split), fill=INK, width=w)
    if button_name == 'Left':
        section=(x0,y0,mid,split)
    elif button_name == 'Right':
        section=(mid,y0,x1,split)
    elif button_name == 'Middle':
        section=(mid-px(1.6),y0,mid+px(1.6),split)
    else:
        letter(img, '4' if button_name == 'X1' else '5', 9, (8, 14, 24, 29), weight=0.6)
        section=None
    if section:
        selected=Image.new('L',(S,S),0);ImageDraw.Draw(selected).rectangle(section,fill=255)
        fill=Image.new('RGBA',(S,S),INK);fill.putalpha(ImageChops.multiply(mask_for('square',body),selected));img.alpha_composite(fill)
    return finish(img)


def kbm():
    g = {key_file(k): keycap(k) for k in KEYS}
    for b in ('Left', 'Right', 'Middle', 'X1', 'X2'):
        g['Mouse_' + b] = mouse(b)
    return g


if __name__ == '__main__':
    table = []
    for fam in ('xbox', 'playstation', 'kbm'):
        out = OUT / fam
        out.mkdir(parents=True, exist_ok=True)
        glyphs = kbm() if fam == 'kbm' else family(fam)
        for k, img in sorted(glyphs.items()):
            img.save(out / (k + '.png'), optimize=True)
            table.append((fam, k))
        print(fam, len(glyphs), 'glyphs ->', out)
    rs = ['//! Generated by tools/make_prompt_glyphs.py: the button-prompt glyphs the launcher can draw into the game font. Do not edit.',
          '', 'pub const GLYPHS: &[(&str, &str, &[u8])] = &[']
    rs += ['    ("%s", "%s", include_bytes!("../assets/prompts/%s/%s.png")),' % (f, k, f, k) for f, k in table]
    rs += ['];', '']
    (PORT / 'launcher/src-tauri/src/prompt_glyphs.rs').write_bytes(chr(10).join(rs).encode())
    print('table', len(table), 'entries')

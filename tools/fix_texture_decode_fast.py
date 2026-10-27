"""NATIVE_TEXTURE_DECODE_FAST_V1 -- texture decode, CMPR passthrough and storage reuse (src/hle/texture_cache_gl.cpp).

Runs after fix_texture_hash_cache.py and fix_texture_validation.py (its anchors are the text they leave). Four edits:
  1. decode:   decode_texture() takes the pointer decoders of runtime/native_texture_decode.h for non-paletted formats when the
               texture lies in one RAM bank; anything else runs the original per-pixel decoder. NWII_NO_FAST_TEXTURE_DECODE=1 opts out.
  2. CMPR:     a miss on a CMPR texture whose blocks are all 4-colour mode is uploaded as GL_COMPRESSED_RGBA_S3TC_DXT1_EXT when
               GL_EXT_texture_compression_s3tc is present (NWII_NO_CMPR_PASSTHROUGH=1 opts out). Otherwise the decoder is used.
  3. reuse:    when a content change replaces an entry, its GL name is reused for the new bytes (same key, so same size and
               format): glTexSubImage2D, and the mip-ready mark is cleared. The old name is no longer deleted on that path.
  4. trace:    texture.lookup_hash wraps the lookup, texture.upload already wraps the GL upload (F3 spike names).

Edits keep their original text base64-encoded in markers, so --restore is exact. Idempotent.
Pass --root to work on another copy of nWiiRuntime. The default is the build tree used by the other stages.
"""
from pathlib import Path
import argparse, base64, re

MARK = 'NATIVE_TEXTURE_DECODE_FAST_V1'
BLOCK = re.compile(r'/\* ' + MARK + r':(\w+) ([A-Za-z0-9+/=]*) \*/(.*?)/\* END_' + MARK + r':\1 \*/', re.S)
INCLUDE = '#include "runtime/native_texture_decode.h" // ' + MARK
CACHE = 'src/hle/texture_cache_gl.cpp'
OVERLAY_HEADER = 'native_texture_decode.h'
port = Path(__file__).resolve().parents[1]

S3TC_FN = (
    'static bool NativeS3tcAvailable() {\n'
    '    static const bool on = [] {\n'
    '        if (!native_texture_decode::CmprPassthroughEnabled()) return false;\n'
    '        GLint count = 0;\n'
    '        glGetIntegerv(GL_NUM_EXTENSIONS, &count);\n'
    '        for (GLint i = 0; i < count; i++) {\n'
    '            const char* name = reinterpret_cast<const char*>(glGetStringi(GL_EXTENSIONS, GLuint(i)));\n'
    '            if (name && std::strcmp(name, "GL_EXT_texture_compression_s3tc") == 0) return true;\n'
    '        }\n'
    '        return false;\n'
    '    }();\n'
    '    return on;\n'
    '}\n\n'
)

EDITS = [
    ('dec_fast', 'Color* pixels = (Color*)img.data;\n    uint32_t addr = stage.base_addr;',
     'Color* pixels = (Color*)img.data;\n    uint32_t addr = stage.base_addr;\n'
     '    if (native_texture_decode::TryDecode(ctx.mmu, addr, width, height, stage.format, pixels)) return img;'),
    ('dec_s3tc_fn', 'GLuint TextureCache::get_texture(CPUContext& ctx, const gx::TexStage& stage) {',
     S3TC_FN + 'GLuint TextureCache::get_texture(CPUContext& ctx, const gx::TexStage& stage) {\n'
     '    native_trace::Scope trace_lookup_hash("texture.lookup_hash");\n'
     '    GLuint reuse_tex = 0; bool reuse_valid = false; // NATIVE_TEXTURE_DECODE_FAST_V1: GL name kept from a content change'),
    ('dec_reuse_erase', 'total_bytes-=it->second.bytes;NativeTextureDelete(1,&it->second.tex);cache.erase(it);it=cache.end();',
     'total_bytes-=it->second.bytes;reuse_tex=it->second.tex;reuse_valid=true;cache.erase(it);it=cache.end();'),
    ('dec_img', '    Image img = decode_texture(ctx, stage);',
     '    std::vector<uint8_t> dxt; // NATIVE_TEXTURE_DECODE_FAST_V1: DXT1 blocks when the CMPR passthrough applies\n'
     '    const bool use_dxt = native_texture_decode::TryDxt1(ctx.mmu, stage.base_addr, stage.width, stage.height, stage.format, dxt) && NativeS3tcAvailable();\n'
     '    Image img = use_dxt ? Image{} : decode_texture(ctx, stage);'),
    ('dec_gen', '    NativeTextureGenerate(1, &tex);',
     '    if (reuse_valid) { tex = reuse_tex; mip_ready.erase(tex); } else NativeTextureGenerate(1, &tex);'),
    ('dec_upload',
     '{ native_trace::GPUScope budget_operation("gpu.texture_transfer_us",1); glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, stage.width, stage.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, img.data); }',
     '{ native_trace::GPUScope budget_operation("gpu.texture_transfer_us",1);\n'
     '      if (use_dxt) glCompressedTexImage2D(GL_TEXTURE_2D, 0, 0x83F1 /* GL_COMPRESSED_RGBA_S3TC_DXT1_EXT */, stage.width, stage.height, 0, GLsizei(dxt.size()), dxt.data());\n'
     '      else if (reuse_valid) glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, stage.width, stage.height, GL_RGBA, GL_UNSIGNED_BYTE, img.data);\n'
     '      else glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, stage.width, stage.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, img.data); }'),
]


def read(path):
    return path.read_bytes().decode('utf-8', 'surrogateescape')


def write(path, text):
    path.write_bytes(text.encode('utf-8', 'surrogateescape'))


def block(tag, original, new, eol):
    packed = base64.b64encode(original.encode('utf-8', 'surrogateescape')).decode()
    return f'/* {MARK}:{tag} {packed} */{new.replace(chr(10), eol)}/* END_{MARK}:{tag} */'


def edit(text, tag, old, new):
    if f'/* {MARK}:{tag} ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    old_eol = old.replace('\n', eol)
    assert text.count(old_eol) == 1, (tag, text.count(old_eol))
    return text.replace(old_eol, block(tag, old_eol, new, eol), 1)


def prepend_include(text):
    if f'/* {MARK}:include ' in text:
        return text
    eol = '\r\n' if '\r\n' in text else '\n'
    return block('include', '', INCLUDE + '\n', eol) + text


def unblock(text):
    return BLOCK.sub(lambda m: base64.b64decode(m.group(2)).decode('utf-8', 'surrogateescape'), text)


def apply(rt):
    overlay = port / 'runtime-overlays' / OVERLAY_HEADER
    (rt / 'include/runtime').mkdir(parents=True, exist_ok=True)
    (rt / 'include/runtime' / OVERLAY_HEADER).write_bytes(overlay.read_bytes())
    path = rt / CACHE
    text = read(path)
    for tag, old, new in EDITS:
        text = edit(text, tag, old, new)
    text = prepend_include(text)
    write(path, text)
    print('Texture decode fast path: decoders, CMPR passthrough, storage reuse, lookup trace.')


def restore(rt):
    path = rt / CACHE
    text = read(path)
    new = unblock(text)
    if new != text:
        write(path, new)
    print('Texture decode fast stage removed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    rt = args.root or port / 'build/nwii-full-source/nWiiRuntime'
    (restore if args.restore else apply)(rt)

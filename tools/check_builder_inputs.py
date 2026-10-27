"""Require identical builder input digests before accepting a completed runtime cache."""
from pathlib import Path
import json, tempfile
from build_resources import cache_state, sha
from builder_revision import digest

def main():
    with tempfile.TemporaryDirectory(prefix='builder-inputs-') as directory:
        root=Path(directory);port=root/'native-port'
        for sub in ('native-port/tools','orig','export','storage/toolchain'):
            (root/sub).mkdir(parents=True,exist_ok=True)
        script=port/'tools/fixture.py';script.write_bytes(b'input')
        identity=dict(schema_version=1,revision='unchanged',required_inputs={'tools/fixture.py':sha(script)})
        (port/'builder-revision.json').write_text(json.dumps(identity),encoding='utf-8')
        (root/'orig/main.dol').write_bytes(b'disc');(root/'export/index.tsv').write_bytes(b'index')
        exe=root/'storage/toolchain/CrashNative.exe';exe.write_bytes(b'runtime')
        wanted=dict(identity=identity,dol_sha256=sha(root/'orig/main.dol'),index_sha256=sha(root/'export/index.tsv'),environment={})
        marker=exe.with_name('runtime-build.json')
        metadata=dict(builder_revision=identity['revision'],builder_inputs=digest(identity),dol_sha256=wanted['dol_sha256'],executable_sha256=sha(exe))
        def write(): marker.write_text(json.dumps(metadata),encoding='utf-8')
        write();assert cache_state(root,wanted)=='complete'
        metadata['builder_inputs']='0'*64;write();assert cache_state(root,wanted) is None
        metadata.pop('builder_inputs');write();assert cache_state(root,wanted) is None
        metadata['builder_inputs']=digest(identity);write()
        script.write_bytes(b'changed');assert cache_state(root,wanted) is None
    print('Builder cache accepts matching inputs; rejects changed, missing and mismatched input digests.')

if __name__=='__main__':main()

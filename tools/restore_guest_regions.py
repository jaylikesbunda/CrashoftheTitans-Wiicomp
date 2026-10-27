"""Restore generated originals before replaying semantic/codegen stages."""
from pathlib import Path
from guest_region_sources import restore_regions
port=Path(__file__).resolve().parents[1]
path=port/'build/crash-generated/output_71.cpp'
if path.exists():
    source=path.read_text();restored=restore_regions(source)
    if restored!=source:path.write_text(restored)
print('Guest region wrappers restored before canonical staging.')

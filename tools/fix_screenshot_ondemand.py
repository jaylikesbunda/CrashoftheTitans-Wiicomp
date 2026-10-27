"""On-demand EFB screenshots: NWII_SCREENSHOT_ON_DEMAND=1 captures only when '<prefix>.now' exists (checked every 20 frames)."""
from pathlib import Path
PORT = Path(__file__).resolve().parents[1]
p = PORT / 'build/nwii-full-source/nWiiRuntime/src/core/main.cpp'
s = p.read_bytes().decode('utf-8')
old = '          if ((++shot_frame % 300) == 0) {'
new = ('          static const bool shot_on_demand = std::getenv("NWII_SCREENSHOT_ON_DEMAND") != nullptr;\n'
       '          bool shot_now = false;\n'
       '          ++shot_frame;\n'
       '          if (shot_on_demand) {\n'
       '            if ((shot_frame % 20) == 0) {\n'
       '              const std::string trigger = std::string(shot_pfx) + ".now";\n'
       '              if (FILE *tf = std::fopen(trigger.c_str(), "rb")) { std::fclose(tf); std::remove(trigger.c_str()); shot_now = true; }\n'
       '            }\n'
       '          } else shot_now = (shot_frame % 300) == 0;\n'
       '          if (shot_now) {')
nl = '\r\n' if '\r\n' in s else '\n'
old_n = old.replace('\n', nl); new_n = new.replace('\n', nl)
if new_n in s: print('already staged')
elif old in s or old_n in s:
    s = s.replace(old_n if old_n in s else old, new_n); p.write_bytes(s.encode('utf-8')); print('on-demand screenshots staged')
else: raise SystemExit('screenshot context changed')

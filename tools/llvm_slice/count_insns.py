"""Count host instructions of a single function in a COFF object."""
import re
import subprocess
import sys


def count(path, symbol, objdump):
    out = subprocess.run([objdump, '-d', '--no-show-raw-insn', path],
                         capture_output=True, text=True).stdout
    lines = out.splitlines()
    # find the block for <symbol>:
    start = None
    for i, ln in enumerate(lines):
        m = re.match(r'^([0-9a-f]+)\s+<(.+)>:', ln)
        if m and m.group(2) == symbol:
            start = i
            break
    if start is None:
        return None
    n = 0
    for ln in lines[start + 1:]:
        if re.match(r'^([0-9a-f]+)\s+<', ln):
            break
        if re.match(r'^\s+[0-9a-f]+:\s', ln):
            n += 1
    return n


if __name__ == '__main__':
    print(count(sys.argv[1], sys.argv[2], sys.argv[3]))

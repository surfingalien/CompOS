#!/usr/bin/env python3
"""Rebuild the repo from archive batch files.

Lines outside `### FILE:` blocks are ignored. A block looks like:

    ### FILE: path/to/file
    ```lang
    ...content...
    ```
    ### END FILE

Blocks terminate at `### END FILE` (or the next `### FILE:`), NOT at the
first ``` — so nested fences inside content are safe. Later CLI args
override earlier files at the same path (newest write wins).

Usage: python3 split.py batch1.txt [batch2.txt ...]
"""
import os, re, sys

FILE_HDR = re.compile(r"^###\s*FILE:\s*(.+?)\s*$")
END_HDR  = re.compile(r"^###\s*END\s+FILE\s*$")
FENCE    = re.compile(r"^`{3,}")

def parse(text):
    lines = text.splitlines(); i, n = 0, len(lines)
    while i < n:
        m = FILE_HDR.match(lines[i])
        if not m:
            i += 1; continue
        path = m.group(1).strip().strip("`"); i += 1
        while i < n and not FENCE.match(lines[i]):          # skip to opening fence
            if FILE_HDR.match(lines[i]) or END_HDR.match(lines[i]): break
            i += 1
        if i < n and FENCE.match(lines[i]): i += 1          # consume opening fence
        body = []
        while i < n and not END_HDR.match(lines[i]) and not FILE_HDR.match(lines[i]):
            body.append(lines[i]); i += 1
        while body and body[-1].strip() == "": body.pop()   # trailing blank lines
        if body and FENCE.match(body[-1]) and body[-1].strip("`").strip() == "":
            body.pop()                                      # closing fence
        yield path, "\n".join(body) + "\n"
        if i < n and END_HDR.match(lines[i]): i += 1

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    total = 0
    for arg in sys.argv[1:]:
        with open(arg, "r", encoding="utf-8-sig") as f: text = f.read()
        count = 0
        for path, content in parse(text):
            if not path or path.startswith("/") or ".." in path.split("/"):
                print(f"  ! skipping unsafe path: {path}"); continue
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as out: out.write(content)
            count += 1
        print(f"✓ {arg}: {count} files"); total += count
    print(f"Done — {total} file writes.")

if __name__ == "__main__":
    main()
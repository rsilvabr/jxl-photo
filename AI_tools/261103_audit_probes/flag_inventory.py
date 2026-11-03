"""Inventory: every argparse flag of the 4 child scripts (AST) vs every flag
literal the wrapper ever emits. Prints a table of child flags the wrapper
never passes (reachable only through free-form expert flags)."""
import ast
import re
import sys
from pathlib import Path

repo = Path(sys.argv[1])
children = ["jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
            "jxl_jpeg_transcoder.py", "jxl_recompressor.py"]


def child_flags(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            names = [a.value for a in node.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)
                     and a.value.startswith("--")]
            if not names:
                continue
            kw = {k.arg: k.value for k in node.keywords}
            hidden = False
            if "help" in kw and isinstance(kw["help"], ast.Attribute):
                hidden = kw["help"].attr == "SUPPRESS"
            action = kw.get("action")
            action = action.value if isinstance(action, ast.Constant) else None
            choices = None
            if "choices" in kw:
                try:
                    choices = ast.literal_eval(kw["choices"])
                except Exception:
                    choices = "<expr>"
            for n in names:
                out[n] = {"hidden": hidden, "action": action, "choices": choices,
                          "aliases": names}
    return out


wrapper = (repo / "jxl_photo.py").read_text(encoding="utf-8")
emitted = set(re.findall(r"['\"](--[a-z0-9][a-z0-9-]*)['\"]", wrapper))

total_missing = 0
for c in children:
    flags = child_flags(repo / c)
    missing = sorted(f for f, meta in flags.items()
                     if f not in emitted and not meta["hidden"]
                     and f not in ("--version", "--help"))
    total_missing += len(missing)
    print(f"\n=== {c}: {len(flags)} flags, wrapper emits {len(flags) - len(missing)}, "
          f"NOT emitted: {len(missing)}")
    for f in missing:
        meta = flags[f]
        extra = f" choices={meta['choices']}" if meta["choices"] else ""
        print(f"   {f}{extra}")
print("\nTotal child flags never emitted by the wrapper:", total_missing)

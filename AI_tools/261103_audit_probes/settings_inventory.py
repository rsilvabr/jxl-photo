"""Top-of-script USER SETTINGS of each child (module-level UPPER_CASE names
assigned a literal), and whether a CLI flag can set them (heuristic: the
setting name is assigned inside main(), i.e. a flag overrides it)."""
import ast
import sys
from pathlib import Path

repo = Path(sys.argv[1])
children = ["jxl_tiff_encoder.py", "jxl_tiff_decoder.py",
            "jxl_jpeg_transcoder.py", "jxl_recompressor.py"]
for c in children:
    tree = ast.parse((repo / c).read_text(encoding="utf-8"))
    settings = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
            value = node.value
        for t in targets:
            n = t.id
            if not n.isupper() or n.startswith("_") or value is None:
                continue
            try:
                v = ast.literal_eval(value)
            except Exception:
                continue
            if isinstance(v, (bool, int, float, str, tuple, type(None))):
                settings[n] = (node.lineno, v)
    main = next((f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == "main"), None)
    assigned_in_main = set()
    if main is not None:
        for node in ast.walk(main):
            if isinstance(node, ast.Global):
                assigned_in_main |= set(node.names)
    no_flag = [(ln, n, v) for n, (ln, v) in settings.items() if n not in assigned_in_main]
    print(f"\n=== {c}: {len(settings)} literal settings, {len(no_flag)} with no CLI override")
    for ln, n, v in sorted(no_flag):
        sv = repr(v)
        print(f"  {ln:5d} {n} = {sv[:60]}")

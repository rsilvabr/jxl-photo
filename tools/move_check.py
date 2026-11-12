#!/usr/bin/env python3
"""Prove that a refactor commit only MOVED code into new functions.

    python tools/move_check.py <rev-before> <rev-after | WORKTREE>
           [--files a.py b.py ...] [--allow-new NAME ...] [--allow-unreturned NAME ...]

For every function that exists on the "after" side and not on the "before"
side (module-level functions and methods of the six toolkit files by
default), it looks for the block the function came from: a contiguous run of
statements, at any nesting level of some function of the "before" side, whose
AST is the new function's body. The only differences a move may bring:

- `global` statements (anywhere), a docstring at the top of the new function
  and one final `return <name>` / `return <name>, <name>, ...`;
- in the function the block left, the run is replaced by ONE statement that
  calls the new function: `f(a, b)`, `x = f(a, b)` or `x, y = f(a, b)`, every
  argument being the variable of the same name.

Then it checks what an AST equality cannot see:

- every variable of the origin that the block reads (an `x += 1` reads x too)
  is a parameter of the new function: else the moved path, and only it,
  raises NameError or reads a module global of the same name;
- every variable the block assigns and the origin may read after it (the
  statements after the call, an enclosing loop's next turn, an enclosing
  try's handlers/finally) comes back through the call's targets: else the
  origin keeps a stale value, silently;
- every argument is a variable the origin defines (an extra parameter named
  after nothing would be a NameError on the moved path only);
- every name the origin declared `global` and the block assigns is declared
  `global` in the new function too: else the assignment creates a local and a
  setting silently stops applying;
- the block has no `return`/`yield`/`await` of its own: moved, it would leave
  the new function instead of the origin.

Comments are not in the AST: the moved lines are also compared as text,
indentation aside, and a difference is reported as a NOTE.

Exit status: 0 when every new function is a proven move, 1 otherwise.
Limits: a read-before-write inside the block is only seen for `x += ...`; a
nested scope that rebinds a variable the block also reads is not told apart.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import copy
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
WORKTREE = "WORKTREE"
DEFAULT_FILES = (
    "jxl_tiff_encoder.py", "jxl_tiff_decoder.py", "jxl_jpeg_transcoder.py",
    "jxl_recompressor.py", "jxl_photo.py", "jxl_photo_common.py",
)
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFS = _FUNCS + (ast.ClassDef,)
_SCOPES = _DEFS + (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_IMPLICIT_MODULE_NAMES = {"__file__", "__name__", "__doc__", "__spec__",
                          "__loader__", "__package__", "__builtins__"}


def read_side(rev: str, files: Sequence[str]) -> Dict[str, str]:
    """{file: source} at a git revision, or in the working tree."""
    out = {}
    for name in files:
        if rev == WORKTREE:
            path = REPO / name
            if path.exists():
                out[name] = path.read_text(encoding="utf-8")
            continue
        r = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{name}"],
                           capture_output=True)
        if r.returncode == 0:
            out[name] = r.stdout.decode("utf-8")
    return out


# --------------------------------------------------------------------------
# AST helpers
# --------------------------------------------------------------------------

class _NoGlobals(ast.NodeTransformer):
    def visit_Global(self, node):
        return None


def _dump(stmt: ast.stmt) -> Optional[str]:
    """The statement's AST without positions and without `global` lines."""
    clean = _NoGlobals().visit(copy.deepcopy(stmt))
    return None if clean is None else ast.dump(clean)


def _norm(stmts: Sequence[ast.stmt]) -> List[Tuple[int, str]]:
    """[(index in stmts, dump)] with the `global` statements left out."""
    out = []
    for i, s in enumerate(stmts):
        d = _dump(s)
        if d is not None:
            out.append((i, d))
    return out


def _child_lists(stmt: ast.AST) -> Iterator[List[ast.stmt]]:
    """The statement lists directly inside a compound statement."""
    for fld in ("body", "orelse", "finalbody"):
        lst = getattr(stmt, fld, None)
        if isinstance(lst, list) and lst and isinstance(lst[0], ast.stmt):
            yield lst
    for h in getattr(stmt, "handlers", None) or []:
        yield h.body
    for case in getattr(stmt, "cases", None) or []:
        yield case.body


def _stmt_lists(fn: ast.AST) -> Iterator[List[ast.stmt]]:
    """Every statement list of a function, nested ones included, but not the
    bodies of nested functions or classes (other scopes)."""
    stack = [fn.body]
    while stack:
        lst = stack.pop()
        yield lst
        for s in lst:
            if not isinstance(s, _DEFS):
                stack.extend(_child_lists(s))


def _qualified_functions(tree: ast.Module) -> Dict[str, ast.AST]:
    """{qualname: node} for module-level functions and class methods."""
    found = {}
    for node in tree.body:
        if isinstance(node, _FUNCS):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, _FUNCS):
                    found[f"{node.name}.{sub.name}"] = sub
    return found


def _every_function(tree: ast.Module) -> Dict[str, ast.AST]:
    """{qualname: node} for every function at any depth."""
    found: Dict[str, ast.AST] = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNCS):
                q = f"{prefix}{child.name}"
                found.setdefault(q, child)
                walk(child, q + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _walk_scope(nodes: Sequence[ast.AST]) -> Iterator[ast.AST]:
    """Nodes of these statements in their own scope: a nested function,
    class, lambda or comprehension is yielded itself but not entered."""
    stack = list(nodes)
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, _SCOPES):
            stack.extend(ast.iter_child_nodes(n))


def _nested_scopes(nodes: Sequence[ast.AST]) -> List[ast.AST]:
    return [n for n in _walk_scope(nodes) if isinstance(n, _SCOPES)]


def _stores(nodes: Sequence[ast.AST]) -> set:
    """Names bound in THIS scope by these statements."""
    out = set()
    for n in _walk_scope(nodes):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
        elif isinstance(n, _DEFS):
            out.add(n.name)
    return out


def _bound_inside_nested(nodes: Sequence[ast.AST]) -> set:
    """Names bound by nested scopes (parameters, comprehension targets...)."""
    out = set()
    for s in _nested_scopes(nodes):
        for n in ast.walk(s):
            if isinstance(n, ast.arg):
                out.add(n.arg)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
    return out


def _loads(nodes: Sequence[ast.AST], skip: Optional[set] = None) -> set:
    """Every name read, nested scopes included (a closure reads the block's).
    An `x += ...` reads x. Nodes whose id() is in `skip` are left out."""
    out = set()
    for stmt in nodes:
        for n in ast.walk(stmt):
            if skip and id(n) in skip:
                continue
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                out.add(n.id)
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                out.add(n.target.id)
    return out


def _read_before_write(block: Sequence[ast.stmt]) -> set:
    """Names the block augments (`x += ...`) before any plain top-level
    assignment of its own: their first value comes from outside."""
    first_plain: Dict[str, int] = {}
    for s in block:
        targets = s.targets if isinstance(s, ast.Assign) else (
            [s.target] if isinstance(s, ast.AnnAssign) else [])
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    first_plain.setdefault(n.id, s.lineno)
    out = set()
    for n in _walk_scope(block):
        if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
            name = n.target.id
            if name not in first_plain or first_plain[name] > n.lineno:
                out.add(name)
    return out


def _globals_declared(fn: ast.AST) -> set:
    out = set()
    for n in _walk_scope(fn.body):
        if isinstance(n, ast.Global):
            out.update(n.names)
    return out


def _module_names(tree: ast.Module) -> set:
    names = set(_stores(tree.body)) | set(_IMPLICIT_MODULE_NAMES)
    for n in ast.walk(tree):
        if isinstance(n, ast.Global):
            names.update(n.names)
    return names


def _params(fn: ast.AST) -> List[str]:
    a = fn.args
    names = [x.arg for x in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs))]
    return names + [x.arg for x in (a.vararg, a.kwarg) if x is not None]


def _names_of(value: Optional[ast.AST]) -> Optional[List[str]]:
    """['x'] / ['x', 'y'] for `x` / `x, y`; None for anything else."""
    if isinstance(value, ast.Name):
        return [value.id]
    if isinstance(value, ast.Tuple) and all(isinstance(e, ast.Name) for e in value.elts):
        return [e.id for e in value.elts]
    return None


def _body_of_new(fn: ast.AST) -> Tuple[List[ast.stmt], List[str]]:
    """(body without docstring and final `return <names>`, returned names)."""
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    returned: List[str] = []
    if body and isinstance(body[-1], ast.Return):
        value = body[-1].value
        names = [] if value is None else _names_of(value)
        if names is not None:
            returned = names
            body = body[:-1]
    return body, returned


def _call_of(stmt: ast.stmt, name: str) -> Optional[Tuple[List[str], ast.Call]]:
    """(assigned names, call) when stmt is `f(...)`, `x = f(...)` or `x, y = f(...)`."""
    def is_call(v):
        return (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id == name)
    if isinstance(stmt, ast.Expr) and is_call(stmt.value):
        return [], stmt.value
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and is_call(stmt.value):
        names = _names_of(stmt.targets[0])
        if names is not None:
            return names, stmt.value
    return None


def _path_to(lst: List[ast.stmt], target: ast.stmt, owner: Optional[ast.AST] = None):
    """[(statement list, index, compound statement owning the list)] from the
    function body down to `target`; None when it is not there."""
    for idx, s in enumerate(lst):
        if s is target:
            return [(lst, idx, owner)]
        if isinstance(s, _DEFS):
            continue
        for child in _child_lists(s):
            sub = _path_to(child, target, s)
            if sub:
                return [(lst, idx, owner)] + sub
    return None


def _reads_after(fn: ast.AST, call_stmt: ast.stmt) -> set:
    """Names the function may read AFTER call_stmt ran: the statements after
    it at every level, a whole enclosing loop (its next turn), and the
    handlers/else/finally of an enclosing try."""
    path = _path_to(fn.body, call_stmt) or []
    after: List[ast.AST] = []
    for lst, idx, owner in path:
        after.extend(lst[idx + 1:])
        if isinstance(owner, (ast.For, ast.AsyncFor, ast.While)):
            after.append(owner)
        elif isinstance(owner, ast.Try):
            if lst is owner.body:
                after.extend(h for h in owner.handlers)
                after.extend(owner.orelse)
            after.extend(owner.finalbody)
    return _loads(after, skip={id(x) for x in ast.walk(call_stmt)})


def _expand_calls(stmts: Sequence[ast.stmt], bodies: Dict[str, List[ast.stmt]]) -> List[ast.stmt]:
    """A copy of stmts where each statement calling a new function is replaced
    by that function's moved body, at any depth (nested scopes aside)."""
    out: List[ast.stmt] = []
    for s in stmts:
        hit = next((n for n in bodies if _call_of(s, n) is not None), None)
        if hit is not None:
            out.extend(copy.deepcopy(bodies[hit]))
            continue
        s = copy.deepcopy(s)
        if not isinstance(s, _DEFS):
            for fld in ("body", "orelse", "finalbody"):
                lst = getattr(s, fld, None)
                if isinstance(lst, list) and lst and isinstance(lst[0], ast.stmt):
                    setattr(s, fld, _expand_calls(lst, bodies))
            for h in getattr(s, "handlers", None) or []:
                h.body = _expand_calls(h.body, bodies)
        out.append(s)
    return out


def _text_lines(source: str, first: ast.stmt, last: ast.stmt) -> List[str]:
    lines = source.splitlines()[first.lineno - 1:last.end_lineno]
    return [ln.strip() for ln in lines
            if ln.strip() and not ln.strip().startswith("global ")]


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

@dataclass
class Result:
    new: str
    origin: str = ""
    lines: str = ""
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.origin) and not self.errors


def _index(trees: Dict[str, ast.Module]):
    """Every statement list of every function, with its normalized dumps."""
    idx = []
    for fname, tree in trees.items():
        for qual, fn in _every_function(tree).items():
            for lst in _stmt_lists(fn):
                idx.append((fname, qual, fn, lst, _norm(lst)))
    return idx


def check_moves(before: Dict[str, str], after: Dict[str, str],
                allow_new: Sequence[str] = (),
                allow_unreturned: Sequence[str] = ()) -> List[Result]:
    before_trees = {f: ast.parse(s) for f, s in before.items()}
    after_trees = {f: ast.parse(s) for f, s in after.items()}
    before_funcs = {(f, q) for f, t in before_trees.items() for q in _qualified_functions(t)}
    new_funcs = [(f, q, fn) for f, tree in after_trees.items()
                 for q, fn in _qualified_functions(tree).items()
                 if (f, q) not in before_funcs and q not in allow_new]
    if not new_funcs:
        return []
    index = _index(before_trees)
    after_all = {f: _every_function(t) for f, t in after_trees.items()}

    results: List[Result] = []
    moved_bodies: Dict[Tuple[str, str], List[ast.stmt]] = {}
    origins: Dict[Tuple[str, str], List[Tuple[str, Result]]] = {}

    for f, q, fn in new_funcs:
        res = Result(new=f"{f}::{q}")
        results.append(res)
        body, returned = _body_of_new(fn)
        want = [d for _, d in _norm(body)]
        if not want:
            res.errors.append("empty body: nothing was moved here")
            continue
        n = len(want)
        hits = []
        for fname, qual, bfn, lst, norm in index:
            dumps = [d for _, d in norm]
            for i in range(len(dumps) - n + 1):
                if dumps[i] == want[0] and dumps[i:i + n] == want:
                    hits.append((fname, qual, bfn, lst[norm[i][0]:norm[i + n - 1][0] + 1]))
        if not hits:
            res.errors.append('no block of the "before" side has this body '
                              "(a changed line, or not a move)")
            continue

        def calls_new(hit):
            node = after_all.get(hit[0], {}).get(hit[1])
            return node is not None and any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == fn.name for c in ast.walk(node))
        hits.sort(key=lambda h: (not calls_new(h), h[0] != f))
        bfile, bqual, bfn, block = hits[0]
        res.origin = f"{bfile}::{bqual}"
        res.lines = f"L{block[0].lineno}-{block[-1].end_lineno}"
        if len(hits) > 1:
            res.notes.append(f"{len(hits)} identical blocks on the before side; "
                             "checked against the one whose function calls it")

        # 1. no return/yield/await of the block's own
        for node in _walk_scope(block):
            if isinstance(node, (ast.Return, ast.Yield, ast.YieldFrom, ast.Await)):
                res.errors.append(f"the block has a `{type(node).__name__.lower()}` "
                                  f"(line {node.lineno}): moved, it leaves the new "
                                  f"function, not {bqual}")
                break

        params = _params(fn)
        new_globals = _globals_declared(fn)
        block_stores = _stores(block)
        nested = _bound_inside_nested(block)
        reads = (_loads(block) - block_stores - nested) | _read_before_write(block)
        known = _module_names(after_trees[f]) | set(dir(builtins))
        unused = [p for p in params if p not in reads and p not in block_stores]
        if unused:
            res.notes.append("parameters the block never reads: " + ", ".join(unused))
        moved_bodies[(f, fn.name)] = body

        # Every function that calls it: the chosen origin, and any other one
        # (two identical copies replaced by one function have two).
        sites = []
        for afile, funcs in after_all.items():
            if afile != f:
                continue        # a bare-name call reaches its own module's function only
            for aqual, anode in funcs.items():
                for lst in _stmt_lists(anode):
                    stmt = next((x for x in lst if _call_of(x, fn.name) is not None), None)
                    if stmt is not None:
                        sites.append((afile, aqual, anode, stmt))
                        break
        if not any((sf, sq) == (bfile, bqual) for sf, sq, _n, _s in sites):
            res.errors.append(f"{bqual} (after) has no statement `[x = ]{fn.name}(...)`")
        for afile, aqual, anode, call_stmt in sites:
            b_site = _every_function(before_trees[afile]).get(aqual) if afile in before_trees else None
            if b_site is None:
                res.errors.append(f"{aqual} calls {fn.name} but did not exist before: not a move")
                continue
            where = "" if (afile, aqual) == (bfile, bqual) else f" (in {aqual})"
            site_globals = _globals_declared(b_site)
            site_locals = (_stores(b_site.body) | set(_params(b_site))) - site_globals

            # 2. what the block reads from the origin comes in as a parameter
            from_origin = sorted((reads & site_locals) - set(params) - new_globals)
            if from_origin:
                res.errors.append("reads variables of the origin that are not parameters"
                                  f"{where}: " + ", ".join(from_origin))
            site_known = _module_names(after_trees[afile]) | set(dir(builtins))
            unknown = sorted(reads - site_locals - known - site_known - set(params) - new_globals)
            if unknown:
                res.errors.append(f"reads names defined nowhere for the new function{where}: "
                                  + ", ".join(unknown))

            # 3. a global of the origin assigned in the block stays global
            lost = sorted((block_stores & site_globals) - new_globals)
            if lost:
                res.errors.append("assigns names that are `global` in the origin without "
                                  f"declaring them `global` here{where}: " + ", ".join(lost))

            # 4. the call: same-named arguments, every parameter passed, targets
            targets, call = _call_of(call_stmt, fn.name)
            passed, bad = [], []
            for p_name, a in zip(params, call.args):
                if isinstance(a, ast.Name) and a.id == p_name:
                    passed.append(p_name)
                else:
                    bad.append(p_name)
            if len(call.args) > len(params):
                bad.append("extra positional arguments")
            for kw in call.keywords:
                if kw.arg is not None and isinstance(kw.value, ast.Name) and kw.value.id == kw.arg:
                    passed.append(kw.arg)
                else:
                    bad.append(kw.arg or "**kwargs")
            if bad:
                res.errors.append(f"arguments that are not the variable of the same name{where}: "
                                  + ", ".join(bad))
            missing = [p_name for p_name in params if p_name not in passed and p_name not in bad]
            if missing:
                res.errors.append("parameters the call does not pass (their defaults would "
                                  f"replace the origin's variables){where}: " + ", ".join(missing))
            undefined = sorted(p_name for p_name in passed
                               if p_name not in site_locals and p_name not in site_known)
            if undefined:
                res.errors.append(f"passes names the origin does not define{where}: "
                                  + ", ".join(undefined))
            if targets != returned:
                res.errors.append(f"the call assigns {targets or 'nothing'} but the new "
                                  f"function returns {returned or 'nothing'}{where}")

            # 5. what the block assigns and the origin reads after it comes back
            stale = sorted((((block_stores - new_globals) & site_locals)
                            & _reads_after(anode, call_stmt))
                           - set(targets) - set(allow_unreturned))
            if stale:
                res.errors.append("assigned in the block and read by the origin after it, "
                                  f"but not returned{where}: " + ", ".join(stale))
            origins.setdefault((afile, aqual), []).append((fn.name, res))

        # 6. comments and layout: a note, never an error
        old_text = _text_lines(before[bfile], block[0], block[-1])
        new_text = _text_lines(after[f], body[0], body[-1])
        if old_text != new_text:
            first = next((k for k, (a, b) in enumerate(zip(old_text, new_text)) if a != b),
                         min(len(old_text), len(new_text)))
            res.notes.append(f"the moved text differs from line {first + 1} of the block "
                             "(comments or layout; the code is the same)")

    # 7. every function that calls a moved block, with the calls expanded back,
    #    is the old function
    for (afile, aqual), moved in origins.items():
        b_fn = _every_function(before_trees[afile]).get(aqual)
        a_fn = after_all.get(afile, {}).get(aqual)
        if b_fn is None or a_fn is None:
            continue
        bodies = {name: moved_bodies[(afile, name)] for name, _ in moved}
        expanded = _expand_calls(a_fn.body, bodies)
        if [d for _, d in _norm(expanded)] != [d for _, d in _norm(b_fn.body)]:
            moved[0][1].errors.append(
                f"{aqual} changed beyond the call(s) to "
                + ", ".join(sorted({name for name, _ in moved})))
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="\n\n".join(__doc__.split("\n\n")[1:]),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before", help="git revision before the move")
    ap.add_argument("after", help=f"git revision after the move, or {WORKTREE}")
    ap.add_argument("--files", nargs="+", default=list(DEFAULT_FILES))
    ap.add_argument("--allow-new", nargs="+", default=[], metavar="NAME",
                    help="new functions that are not moves (not checked)")
    ap.add_argument("--allow-unreturned", nargs="+", default=[], metavar="NAME",
                    help="names the block assigns that the origin does not need back "
                         "(the loop rule is conservative)")
    args = ap.parse_args(argv)
    before = read_side(args.before, args.files)
    after = read_side(args.after, args.files)
    if not before or not after:
        print("nothing to compare: check the revisions and --files", file=sys.stderr)
        return 2
    results = check_moves(before, after, args.allow_new, args.allow_unreturned)
    if not results:
        print("no new function: nothing to check")
        return 0
    for r in results:
        where = f"  <- {r.origin} {r.lines}" if r.origin else ""
        print(f"{'OK  ' if r.ok else 'FAIL'} {r.new}{where}")
        for e in r.errors:
            print(f"     error: {e}")
        for note in r.notes:
            print(f"     note:  {note}")
    bad = sum(1 for r in results if not r.ok)
    print(f"{len(results) - bad} proven move(s), {bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

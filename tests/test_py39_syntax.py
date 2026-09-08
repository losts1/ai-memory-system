"""pyproject declares requires-python >= 3.9. PEP 604 unions (`X | None`) in
annotations are evaluated at definition time on 3.9 unless the module opts into
PEP 563 with `from __future__ import annotations`. This guards every script and
package module so the regression that v1.3.3 fixed (public issue #47) cannot
come back unnoticed on a 3.12 CI box."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted(
    p for pat in ("scripts/*.py", "scripts/rlm/*.py", "ai_memory/*.py", "ai_memory/eval/*.py")
    for p in ROOT.glob(pat)
)


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(a.name == "annotations" for a in node.names):
                return True
    return False


def _union_annotations(tree: ast.Module):
    """Yield (lineno, source-ish) for every BinOp `|` inside an annotation."""
    class V(ast.NodeVisitor):
        def __init__(self):
            self.hits = []

        def _check(self, ann):
            if ann is None:
                return
            for n in ast.walk(ann):
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                    self.hits.append(ann.lineno)
                    return

        def visit_FunctionDef(self, node):
            for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                self._check(a.annotation)
            if node.args.vararg:
                self._check(node.args.vararg.annotation)
            if node.args.kwarg:
                self._check(node.args.kwarg.annotation)
            self._check(node.returns)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_AnnAssign(self, node):
            self._check(node.annotation)
            self.generic_visit(node)

    v = V()
    v.visit(tree)
    return v.hits


@pytest.mark.parametrize("path", FILES, ids=[str(p.relative_to(ROOT)) for p in FILES])
def test_union_annotations_need_future_import(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = _union_annotations(tree)
    if hits and not _has_future_annotations(tree):
        pytest.fail(
            f"{path.relative_to(ROOT)} uses `X | Y` in annotations at lines {hits} "
            "without `from __future__ import annotations` — TypeError at import on Python 3.9"
        )

"""Small structural summary from the tree-sitter SAS grammar for triage and LLM context."""
from __future__ import annotations

import re


_VISIBLE_NODES = {
    "data_step", "proc_step", "proc_sql_step", "macro_definition", "macro_call_statement", "macro_call",
    "include_statement", "libname_statement",
}


def analyze(source: str) -> dict:
    """Summarize SAS block structure; generic PROC statements remain explicitly unparsed."""
    try:
        import tree_sitter_sas
        from tree_sitter import Language, Parser
    except ImportError as e:
        return {"available": False, "error": f"tree-sitter-sas unavailable: {e}"}

    try:
        parser = Parser(Language(tree_sitter_sas.language()))
        tree = parser.parse(source.encode("utf-8"))
    except Exception as e:
        return {"available": False, "error": f"SAS grammar failed: {type(e).__name__}: {e}"}

    counts: dict[str, int] = {}
    outline = []
    errors = 0
    stack = [tree.root_node]
    raw = source.encode("utf-8")
    while stack:
        node = stack.pop()
        name = node.type
        if name in _VISIBLE_NODES or name in {"macro_variable_ref", "generic_statement", "ERROR"}:
            counts[name] = counts.get(name, 0) + 1
        if name == "ERROR" or node.is_missing:
            errors += 1
        if name in _VISIBLE_NODES and len(outline) < 16:
            snippet = re.sub(r"\s+", " ", raw[node.start_byte:node.end_byte].decode("utf-8", "replace"))
            outline.append({"kind": name, "code": snippet[:180]})
        stack.extend(reversed(node.children))

    return {"available": True, "grammar": "tree-sitter-sas", "parse_errors": errors,
            "generic_statements": counts.get("generic_statement", 0),
            "constructs": {k: v for k, v in counts.items() if k != "generic_statement"},
            "outline": outline}

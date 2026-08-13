from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Query, QueryCursor
from tree_sitter_language_pack import get_language, get_parser

from app.graph import JsonObject, JsonValue, canonical_json

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}


@dataclass(frozen=True, slots=True)
class Function:
    id: str
    name: str
    qualified_name: str
    path: str
    kind: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    fingerprint: str
    calls: tuple[str, ...]

    def json(self, snapshot_id: str, mapped_node_ids: list[str]) -> JsonObject:
        return {
            "id": self.id,
            "name": self.name,
            "qualifiedName": self.qualified_name,
            "kind": self.kind,
            "path": self.path,
            "range": {
                "start": {"line": self.start_line, "column": self.start_column},
                "end": {"line": self.end_line, "column": self.end_column},
            },
            "fingerprint": self.fingerprint,
            "snapshotId": snapshot_id,
            "mappedNodeIds": mapped_node_ids,
            "calls": list(self.calls),
        }


def index_repository(
    repository: Path,
    tracked_paths: list[str],
    commit_sha: str,
    tree_hash: str,
    graph: JsonObject,
) -> JsonObject:
    functions: list[Function] = []
    errors: list[JsonValue] = []
    scanned_paths: list[str] = []
    for relative in sorted(tracked_paths):
        path = repository / relative
        language = LANGUAGES.get(path.suffix.lower())
        if language is None or not path.is_file():
            continue
        try:
            source = path.read_bytes()
            parsed = (
                _python_functions(relative, source)
                if language == "python"
                else _tree_functions(relative, language, source)
            )
            functions.extend(parsed)
            scanned_paths.append(relative)
        except (OSError, SyntaxError, UnicodeError, RuntimeError) as error:
            errors.append({"path": relative, "error": str(error)})

    function_facts: list[JsonObject] = [
        {
            "id": function.id,
            "path": function.path,
            "qualifiedName": function.qualified_name,
            "fingerprint": function.fingerprint,
            "calls": list(function.calls),
        }
        for function in functions
    ]
    payload: JsonObject = {
        "commitSha": commit_sha,
        "treeHash": tree_hash,
        "files": scanned_paths,
        "functions": function_facts,
    }
    scan_hash = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    snapshot_id = f"SNAPSHOT-REPOSITORY-LOCAL-{scan_hash[:16].upper()}"
    mappings = _graph_mappings(graph)
    values = [
        function.json(snapshot_id, mappings.get((function.path, function.qualified_name), []))
        for function in functions
    ]
    mapped = sum(bool(value["mappedNodeIds"]) for value in values)
    return {
        "snapshot": {
            "id": snapshot_id,
            "repositoryId": "REPOSITORY-LOCAL",
            "commitSha": commit_sha,
            "treeHash": tree_hash,
            "scanHash": scan_hash,
            "roots": sorted({path.split("/", 1)[0] for path in scanned_paths}),
            "extractors": _extractors(scanned_paths),
        },
        "coverage": {
            "functionCount": len(values),
            "mappedFunctionCount": mapped,
            "unmappedFunctionCount": len(values) - mapped,
        },
        "functions": values,
        "errors": errors,
    }


def _tree_functions(relative: str, language: str, source: bytes) -> list[Function]:
    tree = get_parser(language).parse(source)
    language_value = get_language(language)
    function_query = Query(
        language_value,
        "[(function_declaration) (arrow_function) (function_expression) "
        "(method_definition) (generator_function_declaration) "
        "(generator_function)] @function",
    )
    call_query = Query(
        language_value,
        "[(call_expression function: (_) @target) (new_expression constructor: (_) @target)]",
    )
    nodes = QueryCursor(function_query).captures(tree.root_node).get("function", [])
    nodes.sort(key=lambda node: node.start_byte)
    facts = [
        (
            node.start_byte,
            node.end_byte,
            node.type,
        )
        for node in nodes
    ]
    targets = QueryCursor(call_query).captures(tree.root_node).get("target", [])
    call_facts = [
        (node.start_byte, source[node.start_byte : node.end_byte].decode("utf-8", errors="replace"))
        for node in targets
    ]
    del nodes
    values: list[Function] = []
    for index, (start_byte, end_byte, kind) in enumerate(facts):
        start_row, start_column = _position(source, start_byte)
        end_row, end_column = _position(source, end_byte)
        name = _tree_name(source, start_byte, start_row, start_column)
        parents = [value for value in values if value.start_line <= start_row + 1 <= value.end_line]
        qualified_name = f"{parents[-1].qualified_name}.{name}" if parents else name
        key = f"{relative}:{qualified_name}"
        nested = [(start, end) for start, end, _ in facts[index + 1 :] if end <= end_byte]
        calls = {
            name
            for offset, name in call_facts
            if start_byte <= offset < end_byte
            and not any(start <= offset < end for start, end in nested)
        }
        values.append(
            Function(
                id=f"IMPL-SYMBOL-{hashlib.sha256(key.encode()).hexdigest()[:16].upper()}",
                name=name,
                qualified_name=qualified_name,
                path=relative,
                kind=kind,
                start_line=start_row + 1,
                start_column=start_column + 1,
                end_line=end_row + 1,
                end_column=end_column + 1,
                fingerprint=hashlib.sha256(source[start_byte:end_byte]).hexdigest(),
                calls=tuple(sorted(calls)),
            )
        )
    return values


def _python_functions(relative: str, source: bytes) -> list[Function]:
    text = source.decode("utf-8")
    tree = ast.parse(text, filename=relative)
    values: list[Function] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        child_scope = scope
        if isinstance(node, ast.ClassDef):
            child_scope = (*scope, node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            name = (
                node.name
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                else f"匿名函数@{node.lineno}:{node.col_offset + 1}"
            )
            qualified_name = ".".join((*scope, name))
            key = f"{relative}:{qualified_name}"
            segment = ast.get_source_segment(text, node) or ""
            values.append(
                Function(
                    id=f"IMPL-SYMBOL-{hashlib.sha256(key.encode()).hexdigest()[:16].upper()}",
                    name=name,
                    qualified_name=qualified_name,
                    path=relative,
                    kind={
                        ast.FunctionDef: "function_definition",
                        ast.AsyncFunctionDef: "async_function_definition",
                        ast.Lambda: "lambda",
                    }[type(node)],
                    start_line=node.lineno,
                    start_column=node.col_offset + 1,
                    end_line=node.end_lineno or node.lineno,
                    end_column=(node.end_col_offset or node.col_offset) + 1,
                    fingerprint=hashlib.sha256(segment.encode()).hexdigest(),
                    calls=tuple(sorted(_python_calls(node))),
                )
            )
            child_scope = (*scope, name)
        for child in ast.iter_child_nodes(node):
            visit(child, child_scope)

    visit(tree, ())
    return values


def _python_calls(function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    values: set[str] = set()

    def visit(node: ast.AST, root: bool = False) -> None:
        if not root and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            return
        if isinstance(node, ast.Call):
            values.add(ast.unparse(node.func))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(function, True)
    return values


def _tree_name(source: bytes, start_byte: int, row: int, column: int) -> str:
    end = source.find(b"(", start_byte)
    declaration = source[start_byte : end if end >= 0 else start_byte + 160].decode(
        "utf-8", errors="replace"
    )
    declared = re.search(r"function\s*\*?\s*([A-Za-z_$][\w$]*)", declaration)
    if declared is not None:
        return declared.group(1)
    prefix = source[max(0, start_byte - 160) : start_byte].decode("utf-8", errors="replace")
    assignment = re.search(r"([A-Za-z_$][\w$]*)\s*=\s*$", prefix)
    if assignment is not None:
        return assignment.group(1)
    return f"匿名函数@{row + 1}:{column + 1}"


def _position(source: bytes, offset: int) -> tuple[int, int]:
    row = source.count(b"\n", 0, offset)
    line_start = source.rfind(b"\n", 0, offset) + 1
    return row, offset - line_start


def _graph_mappings(graph: JsonObject) -> dict[tuple[str, str], list[str]]:
    mappings: dict[tuple[str, str], list[str]] = {}
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return mappings
    for item in nodes:
        if not isinstance(item, dict) or item.get("layer") != "implementation":
            continue
        details = item.get("details")
        anchors = item.get("anchors")
        if not isinstance(details, dict) or not isinstance(anchors, list) or not anchors:
            continue
        qualified_name = details.get("qualifiedName")
        anchor = anchors[0]
        if not isinstance(qualified_name, str) or not isinstance(anchor, dict):
            continue
        path = anchor.get("path")
        node_id = item.get("id")
        if isinstance(path, str) and isinstance(node_id, str):
            mappings.setdefault((path, qualified_name), []).append(node_id)
    return mappings


def _extractors(paths: list[str]) -> list[JsonObject]:
    values: list[JsonObject] = []
    if any(Path(path).suffix.lower() == ".py" for path in paths):
        values.append({"id": "PYTHON-AST", "version": "3.13"})
    if any(Path(path).suffix.lower() != ".py" for path in paths):
        values.append({"id": "TREE-SITTER", "version": "0.13.0"})
    return values

from __future__ import annotations

import ast
import hashlib
import json
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
class CallSite:
    name: str
    fingerprint: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def json(self) -> JsonObject:
        return {
            "name": self.name,
            "fingerprint": self.fingerprint,
            "range": {
                "start": {"line": self.start_line, "column": self.start_column},
                "end": {"line": self.end_line, "column": self.end_column},
            },
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
    call_sites: tuple[CallSite, ...]

    @property
    def calls(self) -> tuple[str, ...]:
        return tuple(sorted({call.name for call in self.call_sites}))

    def json(
        self,
        snapshot_id: str,
        graph_node_ids: list[str],
        mapped_node_ids: list[str],
        coverage: JsonObject,
    ) -> JsonObject:
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
            "extractorId": _extractor_id(self.path),
            "graphNodeIds": graph_node_ids,
            "mappedNodeIds": mapped_node_ids,
            "calls": list(self.calls),
            "callSites": [call.json() for call in self.call_sites],
            "coverage": coverage,
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
    file_facts: list[JsonObject] = []
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
            end_line, end_column = _position(source, len(source))
            file_facts.append(
                {
                    "path": relative,
                    "extractorId": _extractor_id(relative),
                    "fingerprint": hashlib.sha256(source).hexdigest(),
                    "range": {
                        "start": {"line": 1, "column": 0},
                        "end": {
                            "line": end_line + 1,
                            "column": max(1, end_column + 1),
                        },
                    },
                }
            )
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
    graph_nodes, mappings = _graph_mappings(graph)
    coverage_artifact, covered_lines = _load_coverage(repository)
    values = [
        function.json(
            snapshot_id,
            graph_nodes.get((function.path, function.qualified_name), []),
            mappings.get((function.path, function.qualified_name), []),
            _function_coverage(function, coverage_artifact, covered_lines),
        )
        for function in functions
    ]
    mapped = sum(bool(value["mappedNodeIds"]) for value in values)
    represented = sum(bool(value["graphNodeIds"]) for value in values)
    measured = sum(value["coverage"]["status"] != "UNKNOWN" for value in values)
    covered = sum(value["coverage"]["status"] == "COVERED" for value in values)
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
            "graphFunctionCount": represented,
            "mappedFunctionCount": mapped,
            "unmappedFunctionCount": len(values) - mapped,
            "status": "OBSERVED" if coverage_artifact is not None else "UNKNOWN",
            "measuredFunctionCount": measured,
            "coveredFunctionCount": covered,
            "uncoveredFunctionCount": measured - covered,
            "artifact": coverage_artifact,
        },
        "fileFacts": file_facts,
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
        call_sites = {
            (offset, name)
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
                call_sites=tuple(
                    _tree_call_site(source, offset, name) for offset, name in sorted(call_sites)
                ),
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
                    call_sites=tuple(_python_calls(node, text)),
                )
            )
            child_scope = (*scope, name)
        for child in ast.iter_child_nodes(node):
            visit(child, child_scope)

    visit(tree, ())
    return values


def _python_calls(
    function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    text: str,
) -> list[CallSite]:
    values: list[CallSite] = []

    def visit(node: ast.AST, root: bool = False) -> None:
        if not root and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            return
        if isinstance(node, ast.Call):
            values.append(
                CallSite(
                    ast.unparse(node.func),
                    hashlib.sha256(
                        (ast.get_source_segment(text, node.func) or ast.unparse(node.func)).encode()
                    ).hexdigest(),
                    node.func.lineno,
                    node.func.col_offset,
                    node.func.end_lineno or node.func.lineno,
                    node.func.end_col_offset or node.func.col_offset + 1,
                )
            )
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(function, True)
    return sorted(values, key=lambda item: (item.start_line, item.start_column, item.name))


def _tree_call_site(source: bytes, offset: int, name: str) -> CallSite:
    start_row, start_column = _position(source, offset)
    end_row, end_column = _position(source, offset + len(name.encode()))
    return CallSite(
        name,
        hashlib.sha256(source[offset : offset + len(name.encode())]).hexdigest(),
        start_row + 1,
        start_column,
        end_row + 1,
        end_column,
    )


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


def _graph_mappings(
    graph: JsonObject,
) -> tuple[
    dict[tuple[str, str], list[str]],
    dict[tuple[str, str], list[str]],
]:
    graph_nodes: dict[tuple[str, str], list[str]] = {}
    mappings: dict[tuple[str, str], list[str]] = {}
    modules_by_path: dict[str, str] = {}
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return graph_nodes, mappings
    for item in nodes:
        if not isinstance(item, dict) or item.get("layer") != "implementation":
            continue
        details = item.get("details")
        if item.get("kind") == "Module" and isinstance(details, dict):
            path = details.get("path")
            node_id = item.get("id")
            if isinstance(path, str) and isinstance(node_id, str):
                modules_by_path[path] = node_id
            continue
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
            graph_nodes.setdefault((path, qualified_name), []).append(node_id)
    sources_by_target: dict[str, list[str]] = {}
    for item in graph.get("edges", []):
        if not isinstance(item, dict) or item.get("kind") != "implemented_by":
            continue
        target_id = item.get("targetId")
        source_id = item.get("sourceId")
        if not isinstance(target_id, str) or not isinstance(source_id, str):
            continue
        sources_by_target.setdefault(target_id, []).append(source_id)
    for key, function_ids in graph_nodes.items():
        direct = {
            source for node_id in function_ids for source in sources_by_target.get(node_id, [])
        }
        inherited = sources_by_target.get(modules_by_path.get(key[0], ""), [])
        values = direct or set(inherited)
        if values:
            mappings[key] = sorted(values)
    return graph_nodes, mappings


def _extractors(paths: list[str]) -> list[JsonObject]:
    values: list[JsonObject] = []
    if any(Path(path).suffix.lower() == ".py" for path in paths):
        values.append({"id": "PYTHON-AST", "version": "3.13"})
    if any(Path(path).suffix.lower() != ".py" for path in paths):
        values.append({"id": "TREE-SITTER", "version": "0.13.0"})
    return values


def _extractor_id(path: str) -> str:
    return "PYTHON-AST" if Path(path).suffix.lower() == ".py" else "TREE-SITTER"


def _load_coverage(repository: Path) -> tuple[str | None, dict[str, set[int]]]:
    candidates = (
        repository / "coverage.json",
        repository / "prototype" / "coverage" / "coverage-final.json",
        repository / "coverage-final.json",
        repository / "lcov.info",
        repository / "prototype" / "coverage" / "lcov.info",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return str(path.relative_to(repository)), (
                _lcov_lines(path, repository)
                if path.name == "lcov.info"
                else _json_coverage_lines(path, repository)
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
    return None, {}


def _json_coverage_lines(path: Path, repository: Path) -> dict[str, set[int]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    files = value.get("files") if isinstance(value, dict) else None
    result: dict[str, set[int]] = {}
    if isinstance(files, dict):
        for name, facts in files.items():
            if not isinstance(name, str) or not isinstance(facts, dict):
                continue
            executed = facts.get("executed_lines")
            if isinstance(executed, list):
                relative = _coverage_path(name, repository)
                if relative is not None:
                    result[relative] = {int(line) for line in executed if isinstance(line, int)}
        return result
    if not isinstance(value, dict):
        return result
    for name, facts in value.items():
        if not isinstance(name, str) or not isinstance(facts, dict):
            continue
        statements = facts.get("statementMap")
        counts = facts.get("s")
        if not isinstance(statements, dict) or not isinstance(counts, dict):
            continue
        relative = _coverage_path(name, repository)
        if relative is None:
            continue
        result[relative] = {
            int(statement["start"]["line"])
            for key, statement in statements.items()
            if isinstance(statement, dict)
            and isinstance(statement.get("start"), dict)
            and isinstance(statement["start"].get("line"), int)
            and int(counts.get(key, 0)) > 0
        }
    return result


def _lcov_lines(path: Path, repository: Path) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:"):
            current = _coverage_path(line[3:], repository)
            if current is not None:
                result.setdefault(current, set())
        elif current is not None and line.startswith("DA:"):
            number, count, *_ = line[3:].split(",")
            if int(count) > 0:
                result[current].add(int(number))
    return result


def _coverage_path(value: str, repository: Path) -> str | None:
    path = Path(value)
    try:
        return path.resolve().relative_to(repository.resolve()).as_posix()
    except ValueError:
        candidate = (repository / path).resolve()
        try:
            return candidate.relative_to(repository.resolve()).as_posix()
        except ValueError:
            return None


def _function_coverage(
    function: Function,
    artifact: str | None,
    covered_lines: dict[str, set[int]],
) -> JsonObject:
    if artifact is None or function.path not in covered_lines:
        return {"status": "UNKNOWN", "artifact": artifact, "coveredLineCount": None}
    hits = covered_lines[function.path] & set(range(function.start_line, function.end_line + 1))
    return {
        "status": "COVERED" if hits else "UNCOVERED",
        "artifact": artifact,
        "coveredLineCount": len(hits),
    }

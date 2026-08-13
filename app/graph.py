from __future__ import annotations

import copy
import hashlib
import json
import textwrap
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import cast

from jsonschema import Draft202012Validator

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

LAYER_IDS = frozenset({"requirement", "design", "implementation", "verification"})
FLOW_EDGE_KINDS = frozenset({"next", "branch", "produces"})
TECHNICAL_FACT_EDGE_KINDS = frozenset(
    {
        "contains",
        "exposes",
        "calls",
        "reads",
        "writes",
        "invokes",
        "creates",
        "renders",
        "configured_by",
    }
)
ANCHORED_TECHNICAL_EDGE_KINDS = TECHNICAL_FACT_EDGE_KINDS - {"contains"}
TRACE_EDGE_LAYERS = {
    "realized_by": (frozenset({"requirement"}), frozenset({"design"})),
    "implemented_by": (
        frozenset({"requirement", "design"}),
        frozenset({"implementation"}),
    ),
    "verified_by": (
        frozenset({"requirement", "implementation"}),
        frozenset({"verification"}),
    ),
    "evidenced_by": (LAYER_IDS, frozenset({"verification"})),
}


class GraphError(ValueError):
    """统一图不满足固定模型约束。"""


def load_object(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GraphError(f"{path.name} 必须是 JSON 对象")
    return cast(JsonObject, value)


def canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def graph_hash(graph: JsonObject) -> str:
    return hashlib.sha256(canonical_json(graph).encode()).hexdigest()


def validate_document(document: JsonObject, schema: JsonObject) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document), key=lambda item: list(item.path)
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "根节点"
        raise GraphError(f"统一图 Schema 校验失败（{location}）：{first.message}")

    graph = _object(document.get("graph"), "graph")
    nodes = [_object(value, "node") for value in _list(graph.get("nodes"), "graph.nodes")]
    edges = [_object(value, "edge") for value in _list(graph.get("edges"), "graph.edges")]
    node_ids = [_string(node.get("id"), "node.id") for node in nodes]
    edge_ids = [_string(edge.get("id"), "edge.id") for edge in edges]
    if len(node_ids) != len(set(node_ids)):
        raise GraphError("统一图包含重复节点 ID")
    if len(edge_ids) != len(set(edge_ids)):
        raise GraphError("统一图包含重复关系 ID")

    known_nodes = set(node_ids)
    for entry_id in _list(graph.get("entryNodeIds"), "graph.entryNodeIds"):
        if entry_id not in known_nodes:
            raise GraphError(f"入口节点不存在：{entry_id}")
    for edge in edges:
        source_id = _string(edge.get("sourceId"), "edge.sourceId")
        target_id = _string(edge.get("targetId"), "edge.targetId")
        if source_id not in known_nodes or target_id not in known_nodes:
            raise GraphError(f"关系端点不存在：{edge['id']}")

    _validate_trace_edges(nodes, edges)
    _validate_code_contract(graph, nodes, edges)

    for node in nodes:
        if node.get("kind") != "Scenario":
            continue
        details = _object(node.get("details"), f"{node['id']}.details")
        for field in ("given", "when", "then"):
            values = _list(details.get(field), f"{node['id']}.details.{field}")
            if not values or not all(isinstance(value, str) and value.strip() for value in values):
                raise GraphError(f"场景 {node['id']} 必须包含非空的 {field}")

    expected_hash = _string(
        _object(document.get("revision"), "revision").get("contentHash"), "hash"
    )
    actual_hash = graph_hash(graph)
    if expected_hash != actual_hash:
        raise GraphError(f"内容哈希不一致：期望 {expected_hash}，实际 {actual_hash}")


def approval_errors(document: JsonObject) -> list[str]:
    graph = _object(document.get("graph"), "graph")
    nodes = [_object(value, "node") for value in _list(graph.get("nodes"), "graph.nodes")]
    edges = [_object(value, "edge") for value in _list(graph.get("edges"), "graph.edges")]
    errors = [
        f"仍有待确认节点：{node['title']}" for node in nodes if node.get("source") == "UNRESOLVED"
    ]
    realized_scenarios = {
        edge.get("sourceId") for edge in edges if edge.get("kind") == "realized_by"
    }
    for node in nodes:
        if node.get("kind") == "Scenario" and node.get("id") not in realized_scenarios:
            errors.append(f"场景尚未关联技术设计：{node['id']}")
    return errors


def apply_diff(
    current: JsonObject,
    diff: JsonObject,
    revision_id: str,
    schema: JsonObject,
) -> JsonObject:
    current_revision = _object(current.get("revision"), "revision")
    base_revision_id = _string(diff.get("baseRevisionId"), "baseRevisionId")
    if base_revision_id != current_revision.get("id"):
        raise GraphError("AI 图差异基于过期 revision")

    result = copy.deepcopy(current)
    graph = _object(result.get("graph"), "graph")
    nodes = [_object(value, "node") for value in _list(graph.get("nodes"), "graph.nodes")]
    edges = [_object(value, "edge") for value in _list(graph.get("edges"), "graph.edges")]

    deleted_nodes = set(_strings(diff.get("deleteNodeIds"), "deleteNodeIds"))
    deleted_edges = set(_strings(diff.get("deleteEdgeIds"), "deleteEdgeIds"))
    node_upserts = _unique_objects(diff.get("upsertNodes"), "upsertNodes")
    edge_upserts = _unique_objects(diff.get("upsertEdges"), "upsertEdges")

    node_map = {_string(node.get("id"), "node.id"): node for node in nodes}
    for node_id in deleted_nodes:
        node_map.pop(node_id, None)
    node_map.update(node_upserts)

    edge_map = {
        _string(edge.get("id"), "edge.id"): edge
        for edge in edges
        if edge.get("sourceId") not in deleted_nodes and edge.get("targetId") not in deleted_nodes
    }
    for edge_id in deleted_edges:
        edge_map.pop(edge_id, None)
    edge_map.update(edge_upserts)

    graph["nodes"] = list(node_map.values())
    graph["edges"] = list(edge_map.values())
    approvable = not approval_errors(result)
    result["revision"] = {
        "id": revision_id,
        "status": "CANDIDATE",
        "baseRevisionId": current_revision["id"],
        "contentHash": graph_hash(graph),
        "approvable": approvable,
    }
    validate_document(result, schema)
    return result


def with_code_snapshot(current: JsonObject, snapshot: JsonObject) -> JsonObject:
    result = copy.deepcopy(current)
    _object(result.get("graph"), "graph")["codeSnapshots"] = [copy.deepcopy(snapshot)]
    return result


def code_index_diff(current: JsonObject, index: JsonObject) -> JsonObject:
    """把确定性扫描结果转换为候选图差异，语义映射仍由 Agent 单独提交。"""
    revision = _object(current.get("revision"), "revision")
    graph = _object(current.get("graph"), "graph")
    snapshot = _object(index.get("snapshot"), "snapshot")
    snapshot_id = _string(snapshot.get("id"), "snapshot.id")
    functions = [_object(value, "function") for value in _list(index.get("functions"), "functions")]
    file_facts = [
        _object(value, "fileFact") for value in _list(index.get("fileFacts"), "fileFacts")
    ]
    coverage = _object(index.get("coverage"), "coverage")

    previous_implementation_ids = {
        _string(node.get("id"), "node.id")
        for node in _objects(graph, "nodes")
        if node.get("layer") == "implementation"
    }
    delete_edge_ids = [
        _string(edge.get("id"), "edge.id")
        for edge in _objects(graph, "edges")
        if edge.get("sourceId") in previous_implementation_ids
        or edge.get("targetId") in previous_implementation_ids
    ]
    repository_node_id = "IMPL-REPOSITORY-LOCAL"
    module_ids: dict[str, str] = {}
    upsert_nodes: list[JsonObject] = [
        {
            "id": repository_node_id,
            "layer": "implementation",
            "kind": "Repository",
            "title": "当前代码仓库",
            "summary": "固定 Git 快照中的项目自有代码。",
            "source": "DERIVED",
            "snapshotId": snapshot_id,
            "details": {
                "commitSha": snapshot["commitSha"],
                "functionCount": coverage["functionCount"],
                "mappedFunctionCount": coverage["mappedFunctionCount"],
                "coverageStatus": coverage["status"],
                "coveredFunctionCount": coverage["coveredFunctionCount"],
            },
        }
    ]
    upsert_edges: list[JsonObject] = []
    file_by_path = {_string(item.get("path"), "fileFact.path"): item for item in file_facts}
    functions_by_id = {_string(item.get("id"), "function.id"): item for item in functions}

    for function in functions:
        path = _string(function.get("path"), "function.path")
        module_id = module_ids.setdefault(path, _module_id(path))
        if not any(node["id"] == module_id for node in upsert_nodes):
            file_fact = file_by_path[path]
            upsert_nodes.append(
                {
                    "id": module_id,
                    "layer": "implementation",
                    "kind": "Module",
                    "title": path,
                    "summary": "包含项目函数的源码文件。",
                    "source": "DERIVED",
                    "snapshotId": snapshot_id,
                    "anchors": [_anchor(snapshot_id, file_fact)],
                    "details": {"path": path},
                }
            )
            upsert_edges.append(
                {
                    "id": _edge_id("CONTAINS", repository_node_id, module_id),
                    "sourceId": repository_node_id,
                    "targetId": module_id,
                    "kind": "contains",
                    "source": "DERIVED",
                    "snapshotId": snapshot_id,
                }
            )
        function_id = _string(function.get("id"), "function.id")
        coverage_value = _object(function.get("coverage"), "function.coverage")
        upsert_nodes.append(
            {
                "id": function_id,
                "layer": "implementation",
                "kind": "Symbol",
                "title": _string(function.get("qualifiedName"), "function.qualifiedName"),
                "summary": f"{path} 中的函数。",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [_anchor(snapshot_id, function)],
                "details": {
                    "qualifiedName": function["qualifiedName"],
                    "functionKind": function["kind"],
                    "coverage": coverage_value,
                    "unresolvedCalls": [],
                },
            }
        )
        upsert_edges.append(
            {
                "id": _edge_id("CONTAINS", module_id, function_id),
                "sourceId": module_id,
                "targetId": function_id,
                "kind": "contains",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
            }
        )

    lookup = _function_lookup(functions)
    function_nodes = {
        _string(node["id"], "node.id"): node
        for node in upsert_nodes
        if node.get("kind") == "Symbol"
    }
    for source_id, function in functions_by_id.items():
        unresolved: list[str] = []
        call_targets: dict[str, JsonObject] = {}
        for call in _objects(function, "callSites"):
            name = _string(call.get("name"), "call.name")
            target_id = _resolve_call(name, function, lookup)
            if target_id is None or target_id == source_id:
                unresolved.append(name)
                continue
            call_targets.setdefault(target_id, call)
        for target_id, call in call_targets.items():
            anchor_fact = {
                **call,
                "path": function["path"],
                "extractorId": function["extractorId"],
            }
            upsert_edges.append(
                {
                    "id": _edge_id("CALLS", source_id, target_id),
                    "sourceId": source_id,
                    "targetId": target_id,
                    "kind": "calls",
                    "source": "DERIVED",
                    "snapshotId": snapshot_id,
                    "anchors": [_anchor(snapshot_id, anchor_fact)],
                }
            )
        details = _object(function_nodes[source_id].get("details"), "node.details")
        details["unresolvedCalls"] = sorted(set(unresolved))

    coverage_status = coverage.get("status")
    if coverage_status == "OBSERVED":
        evidence_id = f"EVIDENCE-COVERAGE-{snapshot_id.rsplit('-', 1)[-1]}"
        upsert_nodes.append(
            {
                "id": evidence_id,
                "layer": "verification",
                "kind": "Evidence",
                "title": "函数覆盖率证据",
                "summary": "由现有测试覆盖率产物导入的函数级运行证据。",
                "source": "OBSERVED",
                "details": {
                    "artifact": coverage["artifact"],
                    "measuredFunctionCount": coverage["measuredFunctionCount"],
                    "coveredFunctionCount": coverage["coveredFunctionCount"],
                    "uncoveredFunctionCount": coverage["uncoveredFunctionCount"],
                },
            }
        )
        for function_id, node in function_nodes.items():
            value = _object(
                _object(node.get("details"), "node.details").get("coverage"), "coverage"
            )
            if value.get("status") == "UNKNOWN":
                continue
            upsert_edges.append(
                {
                    "id": _edge_id("VERIFIED", function_id, evidence_id),
                    "sourceId": function_id,
                    "targetId": evidence_id,
                    "kind": "verified_by",
                    "source": "OBSERVED",
                }
            )

    return {
        "baseRevisionId": revision["id"],
        "upsertNodes": upsert_nodes,
        "deleteNodeIds": sorted(previous_implementation_ids),
        "upsertEdges": upsert_edges,
        "deleteEdgeIds": delete_edge_ids,
    }


def changed_ids(base: JsonObject | None, current: JsonObject) -> tuple[set[str], set[str]]:
    if base is None:
        graph = _object(current.get("graph"), "graph")
        return (
            set(_ids(_list(graph.get("nodes"), "nodes"))),
            set(_ids(_list(graph.get("edges"), "edges"))),
        )
    base_graph = _object(base.get("graph"), "graph")
    current_graph = _object(current.get("graph"), "graph")
    base_nodes = {_string(item.get("id"), "id"): item for item in _objects(base_graph, "nodes")}
    current_nodes = {
        _string(item.get("id"), "id"): item for item in _objects(current_graph, "nodes")
    }
    base_edges = {_string(item.get("id"), "id"): item for item in _objects(base_graph, "edges")}
    current_edges = {
        _string(item.get("id"), "id"): item for item in _objects(current_graph, "edges")
    }
    return (
        {
            item_id
            for item_id in base_nodes.keys() | current_nodes.keys()
            if base_nodes.get(item_id) != current_nodes.get(item_id)
        },
        {
            item_id
            for item_id in base_edges.keys() | current_edges.keys()
            if base_edges.get(item_id) != current_edges.get(item_id)
        },
    )


def to_dot(
    document: JsonObject,
    layers: set[str],
    changed_node_ids: set[str],
    changed_edge_ids: set[str],
    focus_id: str | None,
) -> str:
    if not layers or not layers <= LAYER_IDS:
        raise GraphError("图层参数无效")
    graph = _object(document.get("graph"), "graph")
    nodes = {_string(node.get("id"), "id"): node for node in _objects(graph, "nodes")}
    edges = _objects(graph, "edges")
    if focus_id is not None and focus_id not in nodes:
        raise GraphError("焦点节点不存在")

    main_ids = _mainline_ids(graph, edges)
    visible = {node_id for node_id in main_ids if nodes[node_id].get("layer") in layers}
    visible_requirement_ids = {
        node_id
        for node_id in changed_node_ids
        if node_id in nodes
        and nodes[node_id].get("layer") == "requirement"
        and "requirement" in layers
    }
    visible_requirement_ids.update(
        _string(edge.get("sourceId"), "sourceId")
        for edge in edges
        if edge.get("kind") == "implemented_by"
        and edge.get("sourceId") in nodes
        and nodes[_string(edge.get("sourceId"), "sourceId")].get("layer") == "requirement"
        and "requirement" in layers
    )
    visible.update(visible_requirement_ids)
    visible.update(
        _string(edge.get("sourceId"), "sourceId")
        for edge in edges
        if edge.get("kind") == "contains"
        and edge.get("targetId") in visible_requirement_ids
        and edge.get("sourceId") in nodes
        and nodes[_string(edge.get("sourceId"), "sourceId")].get("layer") in layers
    )
    main_design_ids = {
        _string(edge.get("targetId"), "targetId")
        for edge in edges
        if edge.get("kind") == "realized_by" and edge.get("sourceId") in main_ids
    }
    visible.update(
        node_id
        for node_id in main_design_ids
        if node_id in nodes and nodes[node_id].get("layer") in layers
    )
    for layer in layers - {"requirement", "design"}:
        visible.update(node_id for node_id, node in nodes.items() if node.get("layer") == layer)
    focus_path_ids: set[str] = set()
    if focus_id:
        focus_path_ids = _focus_path_ids(graph, edges, focus_id)
        focus_neighbors = (
            focus_path_ids
            | {
                _string(edge.get("targetId"), "targetId")
                for edge in edges
                if edge.get("kind") == "contains" and edge.get("sourceId") == focus_id
            }
            | {
                _string(edge.get("sourceId"), "sourceId")
                for edge in edges
                if edge.get("kind") == "contains" and edge.get("targetId") == focus_id
            }
        )
        visible.update(
            node_id
            for node_id in focus_neighbors
            if node_id in nodes and nodes[node_id].get("layer") in layers
        )

    lines = [
        "digraph review {",
        'graph [rankdir="LR", bgcolor="transparent", pad="0.25", '
        'nodesep="0.38", ranksep="0.62", splines="ortho"];',
        'node [shape="box", style="rounded,filled", fontname="Arial", fontsize="12", '
        'margin="0.16,0.10", color="#8fb2ee", fillcolor="#ffffff", fontcolor="#222222"];',
        'edge [fontname="Arial", fontsize="10", color="#8b9098", '
        'fontcolor="#5f6368", arrowsize="0.7"];',
    ]
    for node_id in visible:
        node = nodes[node_id]
        label = "\n".join(textwrap.wrap(_string(node.get("title"), "title"), width=8))
        layer = _string(node.get("layer"), "layer")
        source = _string(node.get("source"), "source")
        color = "#8056a8" if layer == "design" else "#4e83db"
        fill = "#fbf8ff" if layer == "design" else "#f8fbff"
        if source == "UNRESOLVED":
            color, fill = "#d4380d", "#fff2e8"
        focused = node_id in focus_path_ids
        penwidth = "2.4" if node_id == focus_id else "1.8" if focused else "1.2"
        shape = "diamond" if node.get("kind") == "Decision" else "box"
        class_name = f"node layer-{layer}" + (" changed" if node_id in changed_node_ids else "")
        class_name += " focused" if focused else " dimmed" if focus_id else ""
        lines.append(
            f"{_dot(node_id)} [id={_dot(node_id)}, class={_dot(class_name)}, "
            f"label={_dot(label)}, shape={_dot(shape)}, "
            f"color={_dot(color)}, fillcolor={_dot(fill)}, penwidth={penwidth}];"
        )
    for edge in edges:
        source_id = _string(edge.get("sourceId"), "sourceId")
        target_id = _string(edge.get("targetId"), "targetId")
        if source_id not in visible or target_id not in visible:
            continue
        if layers == {"requirement", "design"}:
            source_layer = nodes[source_id].get("layer")
            target_layer = nodes[target_id].get("layer")
            if source_layer == target_layer == "design":
                continue
        kind = _string(edge.get("kind"), "kind")
        edge_id = _string(edge.get("id"), "id")
        label = edge.get("label") if isinstance(edge.get("label"), str) else ""
        style = "dashed" if kind in {"realized_by", "implemented_by", "verified_by"} else "solid"
        focused = source_id in focus_path_ids and target_id in focus_path_ids
        class_name = "edge focused" if focused else "edge dimmed" if focus_id else "edge"
        color = "#2467d8" if focused else "#52a86b" if edge_id in changed_edge_ids else "#8b9098"
        constraint = "false" if target_id in graph["entryNodeIds"] else "true"
        lines.append(
            f"{_dot(source_id)} -> {_dot(target_id)} [id={_dot(edge_id)}, "
            f"class={_dot(class_name)}, "
            f"label={_dot(label)}, style={_dot(style)}, color={_dot(color)}, "
            f"penwidth={'2.2' if focused else '1.0'}, "
            f"constraint={constraint}];"
        )
    lines.append("}")
    return "\n".join(lines)


def _mainline_ids(graph: JsonObject, edges: list[JsonObject]) -> set[str]:
    pending = list(_strings(graph.get("entryNodeIds"), "entryNodeIds"))
    result = set(pending)
    while pending:
        current = pending.pop()
        for edge in edges:
            if edge.get("sourceId") != current or edge.get("kind") not in FLOW_EDGE_KINDS:
                continue
            target_id = _string(edge.get("targetId"), "targetId")
            if target_id not in result:
                result.add(target_id)
                pending.append(target_id)
    return result


def _focus_path_ids(graph: JsonObject, edges: list[JsonObject], focus_id: str) -> set[str]:
    mainline_ids = _mainline_ids(graph, edges)
    anchors = (
        {focus_id}
        if focus_id in mainline_ids
        else {
            _string(candidate.get("targetId"), "targetId")
            for owner in edges
            if owner.get("kind") == "contains" and owner.get("targetId") == focus_id
            for candidate in edges
            if candidate.get("kind") == "contains"
            and candidate.get("sourceId") == owner.get("sourceId")
            and candidate.get("targetId") in mainline_ids
        }
    )
    if focus_id == "SCN-LOCAL-AUTO-DELIVERY-001":
        anchors.update(
            node_id
            for node_id in (
                "ACTION-DEVELOP-IN-WORKTREE",
                "ACTION-VERIFY-IN-WORKTREE",
                "ACTION-MERGE-LOCAL",
                "OUTCOME-DEVELOPMENT-RESULT",
            )
            if node_id in mainline_ids
        )
    if not anchors:
        return {focus_id}
    incoming: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("kind") not in FLOW_EDGE_KINDS:
            continue
        source_id = _string(edge.get("sourceId"), "sourceId")
        target_id = _string(edge.get("targetId"), "targetId")
        if source_id in mainline_ids and target_id in mainline_ids:
            outgoing.setdefault(source_id, set()).add(target_id)
            incoming.setdefault(target_id, set()).add(source_id)

    result = {focus_id, *anchors}
    pending = list(anchors)
    while pending:
        current = pending.pop()
        for source_id in incoming.get(current, set()):
            if source_id not in result:
                result.add(source_id)
                pending.append(source_id)
    pending = list(anchors)
    while pending:
        current = pending.pop()
        for target_id in outgoing.get(current, set()):
            if target_id not in result:
                result.add(target_id)
                pending.append(target_id)
    return result


def requirement_context(document: JsonObject, focus_id: str) -> JsonObject:
    """从统一图投影需求概要、直接场景和代码调用链。"""
    graph = _object(document.get("graph"), "graph")
    nodes = {_string(node.get("id"), "id"): node for node in _objects(graph, "nodes")}
    edges = _objects(graph, "edges")
    focus = nodes.get(focus_id)
    if focus is None or focus.get("layer") != "requirement":
        raise GraphError("只能展开需求节点")

    scenarios = [
        nodes[target_id]
        for edge in edges
        if edge.get("kind") == "contains" and edge.get("sourceId") == focus_id
        if (target_id := _string(edge.get("targetId"), "targetId")) in nodes
        and nodes[target_id].get("kind") == "Scenario"
    ]
    starts = _trace_targets(
        edges, {focus_id, *(_string(node.get("id"), "id") for node in scenarios)}
    )
    implementation_ids = {
        node_id for node_id in starts if nodes[node_id].get("layer") == "implementation"
    }
    implementation_ids.update(
        target_id
        for edge in edges
        if edge.get("kind") == "implemented_by" and edge.get("sourceId") in starts
        if (target_id := _string(edge.get("targetId"), "targetId")) in nodes
        and nodes[target_id].get("layer") == "implementation"
    )
    call_kinds = {"calls", "reads", "writes", "invokes"}
    ordered_ids: list[str] = []
    depths: dict[str, int] = {}
    callers: dict[str, str] = {}
    incoming_calls = {
        _string(edge.get("targetId"), "targetId")
        for edge in edges
        if edge.get("kind") in call_kinds
        and edge.get("sourceId") in implementation_ids
        and edge.get("targetId") in implementation_ids
    }
    pending = [
        (node_id, None, 0)
        for node_id in sorted(implementation_ids - incoming_calls) or sorted(implementation_ids)
    ]
    while pending:
        node_id, caller_id, depth = pending.pop(0)
        if node_id in ordered_ids:
            continue
        ordered_ids.append(node_id)
        depths[node_id] = depth
        if caller_id is not None:
            caller = nodes[caller_id]
            caller_details = caller.get("details")
            callers[node_id] = (
                caller_details.get("qualifiedName")
                if isinstance(caller_details, dict)
                and isinstance(caller_details.get("qualifiedName"), str)
                else _string(caller.get("title"), "title")
            )
        child_edges = sorted(
            (
                edge
                for edge in edges
                if edge.get("sourceId") == node_id and edge.get("kind") in call_kinds
                if edge.get("targetId") in nodes
                and nodes[_string(edge.get("targetId"), "targetId")].get("layer")
                == "implementation"
            ),
            key=_edge_line,
        )
        pending[0:0] = [
            (_string(edge.get("targetId"), "targetId"), node_id, depth + 1) for edge in child_edges
        ]

    verified_ids = {edge.get("sourceId") for edge in edges if edge.get("kind") == "verified_by"}
    code_path = []
    for node_id in ordered_ids:
        item = _context_node(nodes[node_id], edges, verified_ids)
        item["depth"] = depths[node_id]
        item["caller"] = callers.get(node_id)
        code_path.append(item)
    return {
        "focusId": focus_id,
        "pathNodeIds": sorted(_focus_path_ids(graph, edges, focus_id)),
        "scenarios": [_context_node(node, edges, verified_ids) for node in scenarios],
        "codePath": code_path,
        "codeSnapshot": _snapshot_summary(graph, nodes, ordered_ids),
    }


def node_context(document: JsonObject, focus_id: str) -> JsonObject:
    """返回任意统一图节点及一跳关系，供 MCP Agent 精确读取。"""
    graph = _object(document.get("graph"), "graph")
    nodes = {_string(node.get("id"), "id"): node for node in _objects(graph, "nodes")}
    focus = nodes.get(focus_id)
    if focus is None:
        raise GraphError("统一图节点不存在")
    edges = [
        edge
        for edge in _objects(graph, "edges")
        if focus_id in {edge.get("sourceId"), edge.get("targetId")}
    ]
    neighbor_ids = {
        _string(edge.get("targetId"), "targetId")
        if edge.get("sourceId") == focus_id
        else _string(edge.get("sourceId"), "sourceId")
        for edge in edges
    }
    return {
        "node": focus,
        "neighbors": [nodes[node_id] for node_id in sorted(neighbor_ids)],
        "edges": edges,
    }


def _trace_targets(edges: list[JsonObject], starts: set[str]) -> set[str]:
    result = set(starts)
    pending = list(starts)
    while pending:
        current = pending.pop()
        for edge in edges:
            if edge.get("sourceId") != current or edge.get("kind") not in {
                "realized_by",
                "implemented_by",
            }:
                continue
            target_id = _string(edge.get("targetId"), "targetId")
            if target_id not in result:
                result.add(target_id)
                pending.append(target_id)
    return result


def _edge_line(edge: JsonObject) -> int:
    anchors = edge.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        return 0
    anchor = _object(anchors[0], "anchor")
    source_range = _object(anchor.get("range"), "range")
    return int(_object(source_range.get("start"), "start")["line"])


def _context_node(node: JsonObject, edges: list[JsonObject], verified_ids: set[str]) -> JsonObject:
    anchors = node.get("anchors") if isinstance(node.get("anchors"), list) else []
    first_anchor = _object(anchors[0], "anchor") if anchors else None
    details = node.get("details") if isinstance(node.get("details"), dict) else {}
    node_id = _string(node.get("id"), "id")
    return {
        "id": node_id,
        "title": _string(node.get("title"), "title"),
        "summary": _string(node.get("summary"), "summary"),
        "kind": _string(node.get("kind"), "kind"),
        "source": _string(node.get("source"), "source"),
        "qualifiedName": details.get("qualifiedName")
        if isinstance(details.get("qualifiedName"), str)
        else None,
        "location": None
        if first_anchor is None
        else {
            "path": first_anchor["path"],
            "line": _object(_object(first_anchor["range"], "range")["start"], "start")["line"],
        },
        "verified": node_id in verified_ids,
        "relations": sum(node_id in {edge.get("sourceId"), edge.get("targetId")} for edge in edges),
    }


def _snapshot_summary(
    graph: JsonObject, nodes: dict[str, JsonObject], node_ids: list[str]
) -> JsonObject | None:
    snapshot_ids = {nodes[node_id].get("snapshotId") for node_id in node_ids if node_id in nodes}
    if len(snapshot_ids) != 1:
        return None
    snapshot_id = next(iter(snapshot_ids))
    if not isinstance(snapshot_id, str):
        return None
    snapshot = next(
        (item for item in _objects(graph, "codeSnapshots") if item.get("id") == snapshot_id),
        None,
    )
    if snapshot is None:
        return None
    return {
        "id": snapshot_id,
        "commitSha": snapshot["commitSha"],
        "roots": snapshot["roots"],
        "extractors": snapshot["extractors"],
    }


def _validate_code_contract(
    graph: JsonObject, nodes: list[JsonObject], edges: list[JsonObject]
) -> None:
    snapshots = [
        _object(value, "codeSnapshot")
        for value in _list(graph.get("codeSnapshots", []), "graph.codeSnapshots")
    ]
    snapshot_ids = [_string(snapshot.get("id"), "codeSnapshot.id") for snapshot in snapshots]
    if len(snapshot_ids) != len(set(snapshot_ids)):
        raise GraphError("统一图包含重复代码快照 ID")

    extractors_by_snapshot: dict[str, set[str]] = {}
    for snapshot in snapshots:
        snapshot_id = _string(snapshot.get("id"), "codeSnapshot.id")
        for root in _strings(snapshot.get("roots"), f"{snapshot_id}.roots"):
            _relative_path(root, f"{snapshot_id}.roots")
        extractors = [
            _object(value, f"{snapshot_id}.extractors")
            for value in _list(snapshot.get("extractors"), f"{snapshot_id}.extractors")
        ]
        extractor_ids = {
            _string(extractor.get("id"), f"{snapshot_id}.extractor.id") for extractor in extractors
        }
        if len(extractor_ids) != len(extractors):
            raise GraphError(f"代码快照包含重复提取器 ID：{snapshot_id}")
        extractors_by_snapshot[snapshot_id] = extractor_ids

    known_snapshots = set(snapshot_ids)
    nodes_by_id = {_string(node.get("id"), "node.id"): node for node in nodes}
    for node in nodes:
        snapshot_id = _validate_snapshot_reference(node, known_snapshots, extractors_by_snapshot)
        if node.get("layer") != "implementation":
            if snapshot_id is not None:
                raise GraphError(f"非实现节点不得声明代码快照：{node.get('id', '未知节点')}")
            continue
        node_id = _string(node.get("id"), "node.id")
        if snapshot_id is None:
            raise GraphError(f"实现节点缺少代码快照：{node_id}")
        if node.get("source") == "DECLARED":
            raise GraphError(f"实现事实不能标记为用户声明：{node_id}")
        if (
            node.get("source") == "DERIVED"
            and node.get("kind") != "Repository"
            and node.get("anchors") is None
        ):
            raise GraphError(f"静态实现节点缺少源码锚点：{node_id}")

    for edge in edges:
        snapshot_id = _validate_snapshot_reference(edge, known_snapshots, extractors_by_snapshot)
        source_node = nodes_by_id[_string(edge.get("sourceId"), "edge.sourceId")]
        target_node = nodes_by_id[_string(edge.get("targetId"), "edge.targetId")]
        implementation_nodes = [
            node for node in (source_node, target_node) if node.get("layer") == "implementation"
        ]
        kind = edge.get("kind")
        if not implementation_nodes:
            if snapshot_id is not None:
                raise GraphError(f"非实现关系不得声明代码快照：{edge.get('id', '未知关系')}")
            continue
        if kind not in TECHNICAL_FACT_EDGE_KINDS:
            continue
        edge_id = _string(edge.get("id"), "edge.id")
        if snapshot_id is None:
            raise GraphError(f"实现关系缺少代码快照：{edge_id}")
        if edge.get("source") == "DECLARED":
            raise GraphError(f"实现事实关系不能标记为用户声明：{edge_id}")
        endpoint_snapshots = {node.get("snapshotId") for node in implementation_nodes}
        if endpoint_snapshots != {snapshot_id}:
            raise GraphError(f"实现关系不能静默跨越代码快照：{edge_id}")
        if (
            edge.get("source") == "DERIVED"
            and kind in ANCHORED_TECHNICAL_EDGE_KINDS
            and edge.get("anchors") is None
        ):
            raise GraphError(f"静态实现关系缺少源码锚点：{edge_id}")


def _validate_snapshot_reference(
    item: JsonObject,
    known_snapshots: set[str],
    extractors_by_snapshot: dict[str, set[str]],
) -> str | None:
    value = item.get("snapshotId")
    if value is None:
        if item.get("anchors") is not None:
            raise GraphError(f"源码锚点缺少所属代码快照：{item.get('id', '未知元素')}")
        return None
    snapshot_id = _string(value, "snapshotId")
    if snapshot_id not in known_snapshots:
        raise GraphError(f"引用了不存在的代码快照：{snapshot_id}")
    if item.get("anchors") is None:
        return snapshot_id
    for value in _list(item.get("anchors"), f"{item.get('id', '元素')}.anchors"):
        anchor = _object(value, "codeAnchor")
        anchor_snapshot_id = _string(anchor.get("snapshotId"), "codeAnchor.snapshotId")
        if anchor_snapshot_id != snapshot_id:
            raise GraphError(f"源码锚点与元素代码快照不一致：{item.get('id', '未知元素')}")
        _relative_path(anchor.get("path"), "codeAnchor.path")
        extractor_id = _string(anchor.get("extractorId"), "codeAnchor.extractorId")
        if extractor_id not in extractors_by_snapshot[snapshot_id]:
            raise GraphError(f"源码锚点引用了未知提取器：{extractor_id}")
        source_range = _object(anchor.get("range"), "codeAnchor.range")
        start = _object(source_range.get("start"), "codeAnchor.range.start")
        end = _object(source_range.get("end"), "codeAnchor.range.end")
        start_position = (int(start["line"]), int(start["column"]))
        end_position = (int(end["line"]), int(end["column"]))
        if end_position <= start_position:
            raise GraphError(f"源码锚点结束位置必须晚于开始位置：{item.get('id', '未知元素')}")
    return snapshot_id


def _validate_trace_edges(nodes: list[JsonObject], edges: list[JsonObject]) -> None:
    nodes_by_id = {_string(node.get("id"), "node.id"): node for node in nodes}
    for edge in edges:
        kind = edge.get("kind")
        if kind not in TRACE_EDGE_LAYERS:
            continue
        source_layers, target_layers = TRACE_EDGE_LAYERS[kind]
        source_node = nodes_by_id[_string(edge.get("sourceId"), "edge.sourceId")]
        target_node = nodes_by_id[_string(edge.get("targetId"), "edge.targetId")]
        if (
            source_node.get("layer") not in source_layers
            or target_node.get("layer") not in target_layers
        ):
            raise GraphError(f"跨层追踪方向无效：{edge.get('id', '未知关系')}")


def _relative_path(value: JsonValue | None, name: str) -> str:
    path = _string(value, name)
    normalized = str(PurePosixPath(path))
    invalid = (
        path.startswith("/")
        or PureWindowsPath(path).is_absolute()
        or "\\" in path
        or "\0" in path
        or ".." in PurePosixPath(path).parts
        or path != normalized
    )
    if invalid:
        raise GraphError(f"{name} 必须是仓库内规范化相对路径")
    return path


def _module_id(path: str) -> str:
    return f"IMPL-MODULE-{hashlib.sha256(path.encode()).hexdigest()[:16].upper()}"


def _edge_id(kind: str, source_id: str, target_id: str) -> str:
    value = hashlib.sha256(f"{kind}:{source_id}:{target_id}".encode()).hexdigest()[:16].upper()
    return f"EDGE-IMPL-{kind}-{value}"


def _anchor(snapshot_id: str, fact: JsonObject) -> JsonObject:
    return {
        "snapshotId": snapshot_id,
        "path": fact["path"],
        "range": fact["range"],
        "extractorId": fact["extractorId"],
        "fingerprint": fact["fingerprint"],
    }


def _function_lookup(functions: list[JsonObject]) -> dict[str, list[JsonObject]]:
    lookup: dict[str, list[JsonObject]] = {}
    for function in functions:
        qualified = _string(function.get("qualifiedName"), "function.qualifiedName")
        name = _string(function.get("name"), "function.name")
        for key in {qualified, name, qualified.rsplit(".", 1)[-1]}:
            lookup.setdefault(key, []).append(function)
    return lookup


def _resolve_call(
    name: str,
    source: JsonObject,
    lookup: dict[str, list[JsonObject]],
) -> str | None:
    path = _string(source.get("path"), "function.path")
    qualified = _string(source.get("qualifiedName"), "function.qualifiedName")
    normalized = name.removeprefix("self.").removeprefix("cls.")
    short = normalized.rsplit(".", 1)[-1]
    scope = qualified.rsplit(".", 1)[0] if "." in qualified else ""
    candidates = [
        *lookup.get(normalized, []),
        *lookup.get(f"{scope}.{short}", []),
        *lookup.get(short, []),
    ]
    unique = {
        _string(candidate.get("id"), "function.id"): candidate
        for candidate in candidates
        if candidate.get("path") == path
    }
    if len(unique) != 1:
        unique = {
            _string(candidate.get("id"), "function.id"): candidate for candidate in candidates
        }
    return next(iter(unique)) if len(unique) == 1 else None


def _dot(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _object(value: JsonValue | None, name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise GraphError(f"{name} 必须是对象")
    return value


def _list(value: JsonValue | None, name: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise GraphError(f"{name} 必须是数组")
    return value


def _string(value: JsonValue | None, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise GraphError(f"{name} 必须是非空字符串")
    return value


def _strings(value: JsonValue | None, name: str) -> list[str]:
    values = _list(value, name)
    if not all(isinstance(item, str) for item in values):
        raise GraphError(f"{name} 只能包含字符串")
    return cast(list[str], values)


def _objects(parent: JsonObject, field: str) -> list[JsonObject]:
    return [_object(value, field) for value in _list(parent.get(field), field)]


def _ids(values: list[JsonValue]) -> list[str]:
    return [_string(_object(value, "item").get("id"), "id") for value in values]


def _unique_objects(value: JsonValue | None, name: str) -> dict[str, JsonObject]:
    objects = [_object(item, name) for item in _list(value, name)]
    result = {_string(item.get("id"), f"{name}.id"): item for item in objects}
    if len(result) != len(objects):
        raise GraphError(f"{name} 包含重复 ID")
    return result

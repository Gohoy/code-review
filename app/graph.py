from __future__ import annotations

import copy
import hashlib
import json
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
    if focus_id:
        focus_neighbors = {
            endpoint
            for edge in edges
            if edge.get("kind") in {"realized_by", "implemented_by", "verified_by"}
            if focus_id in {edge.get("sourceId"), edge.get("targetId")}
            for endpoint in (edge.get("sourceId"), edge.get("targetId"))
            if isinstance(endpoint, str)
        }
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
        layer = _string(node.get("layer"), "layer")
        source = _string(node.get("source"), "source")
        color = "#8056a8" if layer == "design" else "#4e83db"
        fill = "#fbf8ff" if layer == "design" else "#f8fbff"
        if source == "UNRESOLVED":
            color, fill = "#d4380d", "#fff2e8"
        penwidth = "2.4" if node_id == focus_id else "1.2"
        shape = "diamond" if node.get("kind") == "Decision" else "box"
        class_name = f"node layer-{layer}" + (" changed" if node_id in changed_node_ids else "")
        lines.append(
            f"{_dot(node_id)} [id={_dot(node_id)}, class={_dot(class_name)}, "
            f"label={_dot(_string(node.get('title'), 'title'))}, shape={_dot(shape)}, "
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
        color = "#52a86b" if edge_id in changed_edge_ids else "#8b9098"
        constraint = "false" if target_id in graph["entryNodeIds"] else "true"
        lines.append(
            f"{_dot(source_id)} -> {_dot(target_id)} [id={_dot(edge_id)}, "
            f"label={_dot(label)}, style={_dot(style)}, color={_dot(color)}, "
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

export function mergeTestEvidenceGraph(graph, layer, projection) {
  if (!graph || layer !== "verification") return graph;
  return {
    ...graph,
    nodes: [...graph.nodes, ...(projection.nodes || [])],
    edges: [...graph.edges, ...(projection.edges || [])],
  };
}

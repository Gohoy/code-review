export function mergeTestEvidenceGraph(graph, layer, projection) {
  if (!graph || layer !== "verification") return graph;
  return {
    ...graph,
    nodes: [...graph.nodes, ...(projection.nodes || [])],
    edges: [...graph.edges, ...(projection.edges || [])],
  };
}

export function retainTestEvidence(projection, implementationRunId) {
  if (projection.implementationRunId === implementationRunId) return projection;
  return { implementationRunId, nodes: [], edges: [] };
}

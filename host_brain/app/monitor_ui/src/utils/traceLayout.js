import dagre from '@dagrejs/dagre'

const NODE_WIDTH  = 160
const NODE_HEIGHT = 68

/**
 * 使用 Dagre 对 React Flow 节点/边进行自动布局（从左到右）
 */
export function applyDagreLayout(nodes, edges) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  // LR（从左到右）：主链路横向流动，层级清晰
  g.setGraph({ rankdir: 'LR', nodesep: 20, ranksep: 60, marginx: 24, marginy: 24 })

  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  })

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target)
  })

  dagre.layout(g)

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id)
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    }
  })

  return { nodes: layoutedNodes, edges }
}

export const INSIGHTS_CHAIN_NODE_COLORS = Object.freeze({
  airlock_ingress: '#667085',
  airlock_egress: '#667085',
  airlock: '#667085',
  governor: '#7A5AF8',
  recall_fusion: '#0BA5EC',
  prompt_builder: '#475467',
  llm_stream: '#F79009',
  llm_gateway: '#F79009',
  episodic_store: '#EE46BC',
  bond_update: '#D97706',
  pii_filter: '#667085',
  sub_agent: '#7C3AED',
  audit_snapshot: '#667085',
  airlock_quarantine: '#667085',
  airlock_sanitizer: '#667085',
  graph_executor: '#2970FF',
  qa_contract: '#12B76A',
  canon_proposal: '#6366F1',
  audit: '#667085',
})

export function resolveInsightsChainNodeColor(nodeName, fallback = '#667085') {
  return INSIGHTS_CHAIN_NODE_COLORS[nodeName] ?? fallback
}

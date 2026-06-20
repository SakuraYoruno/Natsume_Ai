import React, { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import {
  Mic,
  Volume2,
  Video,
  Brain,
  Shield,
  Wrench,
  Database,
  Cpu,
  GitBranch,
  Zap,
  MessageSquare,
  AlertCircle,
  Search,
  Filter,
  Users,
  ArrowRightLeft,
  CheckCircle,
} from 'lucide-react'
import { getTraceNodeDisplayName } from './traceNodeText.js'
import {
  resolveTraceStatusBorder,
  TRACE_LAYER_BADGE_META,
  TRACE_NODE_TYPE_META,
  TRACE_PROVIDER_BADGE_META,
} from './traceVisualMeta.js'

const NODE_TYPE_ICONS = {
  intent: MessageSquare,
  governor: Shield,
  immune: Shield,
  skill: Wrench,
  asr: Mic,
  tts: Volume2,
  vts: Video,
  llm: Brain,
  memory: Database,
  bond: Zap,
  system: Cpu,
  branch: GitBranch,
  recall: Search,
  retrieval: Filter,
  agent: Users,
  router: ArrowRightLeft,
  judge: CheckCircle,
  default: AlertCircle,
}

// ===== 自定义节点组件 =====
// data.inactive = true  → 灰色未激活（全图静态展示时）
// data.active   = true  → 高亮激活（请求链路经过时）
// data.layer    = 'edge'|'host'|'both' → 端侧标识
const CustomNode = memo(({ data, selected }) => {
  const nodeType = (data.node_type || data.type || 'default').toLowerCase()
  const cfg = TRACE_NODE_TYPE_META[nodeType] ?? TRACE_NODE_TYPE_META.default
  const Icon = NODE_TYPE_ICONS[nodeType] ?? NODE_TYPE_ICONS.default
  const isInactive = data.inactive === true
  const isActive   = data.active   === true
  const layer      = data.layer    ?? null

  // 未激活态：灰色
  const borderColor = isInactive
    ? '#E4E7EC'
    : isActive
      ? cfg.color
      : resolveTraceStatusBorder(data.status)

  const provider = data.provider ? TRACE_PROVIDER_BADGE_META[data.provider.toLowerCase()] : null
  const layerBadge = layer ? TRACE_LAYER_BADGE_META[layer] : null

  return (
    <div
      className="relative rounded-lg border-2 bg-white shadow-card cursor-pointer select-none transition-all hover:shadow-card-hover"
      style={{
        borderColor: selected ? '#2970FF' : borderColor,
        minWidth: 130,
        maxWidth: 170,
        opacity: isInactive ? 0.72 : 1,
        // 激活态：外发光效果
        boxShadow: selected
          ? '0 0 0 3px rgba(41,112,255,0.2)'
          : isActive
            ? `0 0 0 3px ${cfg.color}33, 0 2px 8px ${cfg.color}22`
            : undefined,
        transition: 'opacity 0.3s, box-shadow 0.3s, border-color 0.3s',
      }}
      onClick={() => data.onSelect?.(data)}
    >
      {/* 顶部色条：未激活时灰色 */}
      <div
        className="h-1 rounded-t-md"
        style={{ background: isInactive ? '#E4E7EC' : cfg.color }}
      />

      {/* 内容区 */}
      <div className="px-3 py-2">
        {/* 节点名 + 图标 */}
        <div className="flex items-center gap-2 mb-1">
          <span
            className="flex items-center justify-center w-6 h-6 rounded"
            style={{
              background: isInactive ? '#EAECF0' : cfg.bg,
              color: isInactive ? '#667085' : cfg.color,
            }}
          >
            <Icon size={13} />
          </span>
          <span className={`text-xs font-semibold truncate flex-1 ${isInactive ? 'text-gray-600' : 'text-gray-800'}`}>
            {getTraceNodeDisplayName(data, cfg.label)}
          </span>
        </div>

        {/* 耗时 + Provider 徽章 + 端侧标签 */}
        <div className="flex items-center gap-1.5 flex-wrap">
          {data.latency_ms != null && !isInactive && (
            <span className="text-xs text-gray-400">{data.latency_ms}ms</span>
          )}
          {provider && !isInactive && (
            <span
              className="text-xs px-1.5 py-0.5 rounded-full font-medium"
              style={{ background: provider.bg, color: provider.text }}
            >
              {provider.label}
            </span>
          )}
          {data.model_id && !isInactive && (
            <span className="text-xs text-gray-400 truncate">{data.model_id}</span>
          )}
          {layerBadge && (
            <span
              className="text-xs px-1.5 py-0.5 rounded-full font-medium"
              style={{ background: layerBadge.bg, color: layerBadge.text }}
            >
              {layerBadge.label}
            </span>
          )}
        </div>
      </div>

      {/* 状态指示点：未激活时灰色，激活时彩色 */}
      <div
        className="absolute top-2 right-2 w-2 h-2 rounded-full"
        style={{ background: isInactive ? '#D0D5DD' : isActive ? cfg.color : borderColor }}
      />

      {/* React Flow 连接点（LR 布局：左入右出） */}
      <Handle type="target" position={Position.Left}  style={{ background: '#D0D5DD', width: 7, height: 7 }} />
      <Handle type="source" position={Position.Right} style={{ background: '#D0D5DD', width: 7, height: 7 }} />
    </div>
  )
})

CustomNode.displayName = 'CustomNode'
export default CustomNode

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

// ===== 节点类型 → 图标 + 颜色 =====
const NODE_TYPE_CONFIG = {
  intent:    { icon: MessageSquare, color: '#2970FF', bg: '#EEF4FF', label: 'Intent'    },
  governor:  { icon: Shield,        color: '#7C3AED', bg: '#F5F3FF', label: 'Governor'  },
  skill:     { icon: Wrench,        color: '#0891B2', bg: '#ECFEFF', label: 'Skill'     },
  asr:       { icon: Mic,           color: '#059669', bg: '#ECFDF5', label: 'ASR'       },
  tts:       { icon: Volume2,       color: '#D97706', bg: '#FFFBEB', label: 'TTS'       },
  vts:       { icon: Video,         color: '#DB2777', bg: '#FDF2F8', label: 'VTS'       },
  llm:       { icon: Brain,         color: '#2970FF', bg: '#EEF4FF', label: 'LLM'       },
  memory:    { icon: Database,      color: '#7C3AED', bg: '#F5F3FF', label: 'Memory'    },
  bond:      { icon: Zap,           color: '#D97706', bg: '#FFFBEB', label: 'Bond'      },
  system:    { icon: Cpu,           color: '#475467', bg: '#F9FAFB', label: 'System'    },
  branch:    { icon: GitBranch,     color: '#0891B2', bg: '#ECFEFF', label: 'Branch'    },
  recall:    { icon: Search,        color: '#0891B2', bg: '#ECFEFF', label: 'Recall'    },
  retrieval: { icon: Filter,        color: '#0891B2', bg: '#ECFEFF', label: 'Retrieval' },
  agent:     { icon: Users,         color: '#7C3AED', bg: '#F5F3FF', label: 'Agent'     },
  router:    { icon: ArrowRightLeft, color: '#475467', bg: '#F9FAFB', label: 'Router'   },
  judge:     { icon: CheckCircle,   color: '#059669', bg: '#ECFDF5', label: 'Judge'     },
  default:   { icon: AlertCircle,   color: '#98A2B3', bg: '#F9FAFB', label: 'Node'      },
}

// ===== Provider 徽章颜色 =====
const PROVIDER_COLORS = {
  openai:   { bg: '#ECFDF5', text: '#059669', label: 'OpenAI'   },
  gemini:   { bg: '#EEF4FF', text: '#2970FF', label: 'Gemini'   },
  deepseek: { bg: '#FDF2F8', text: '#DB2777', label: 'DeepSeek' },
}

// ===== 状态颜色 =====
const STATUS_BORDER = {
  OK:       '#12B76A',
  UP:       '#12B76A',
  success:  '#12B76A',
  healthy:  '#12B76A',
  DEGRADED: '#F79009',
  degraded: '#F79009',
  FAIL:     '#F04438',
  DOWN:     '#F04438',
  error:    '#F04438',
  RUNNING:  '#2970FF',
  running:  '#2970FF',
}

// ===== 端侧标签颜色 =====
const LAYER_BADGE = {
  edge: { bg: '#ECFDF5', text: '#059669', label: 'Edge' },
  host: { bg: '#EEF4FF', text: '#2970FF', label: 'Host' },
  both: { bg: '#F5F3FF', text: '#7C3AED', label: 'Both' },
}

function getStatusBorder(status) {
  return STATUS_BORDER[status] ?? '#D0D5DD'
}

// ===== 自定义节点组件 =====
// data.inactive = true  → 灰色未激活（全图静态展示时）
// data.active   = true  → 高亮激活（请求链路经过时）
// data.layer    = 'edge'|'host'|'both' → 端侧标识
const CustomNode = memo(({ data, selected }) => {
  const nodeType = (data.node_type || data.type || 'default').toLowerCase()
  const cfg = NODE_TYPE_CONFIG[nodeType] ?? NODE_TYPE_CONFIG.default
  const Icon = cfg.icon
  const isInactive = data.inactive === true
  const isActive   = data.active   === true
  const layer      = data.layer    ?? null

  // 未激活态：灰色
  const borderColor = isInactive
    ? '#E4E7EC'
    : isActive
      ? cfg.color
      : getStatusBorder(data.status)

  const provider = data.provider ? PROVIDER_COLORS[data.provider.toLowerCase()] : null
  const layerBadge = layer ? LAYER_BADGE[layer] : null

  return (
    <div
      className="relative rounded-lg border-2 bg-white shadow-card cursor-pointer select-none transition-all hover:shadow-card-hover"
      style={{
        borderColor: selected ? '#2970FF' : borderColor,
        minWidth: 160,
        maxWidth: 210,
        opacity: isInactive ? 0.45 : 1,
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
              background: isInactive ? '#F2F4F7' : cfg.bg,
              color: isInactive ? '#98A2B3' : cfg.color,
            }}
          >
            <Icon size={13} />
          </span>
          <span className={`text-xs font-semibold truncate flex-1 ${isInactive ? 'text-gray-400' : 'text-gray-800'}`}>
            {data.display_name || data.label || cfg.label}
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

      {/* React Flow 连接点 */}
      <Handle type="target" position={Position.Left}  style={{ background: '#D0D5DD', width: 8, height: 8 }} />
      <Handle type="source" position={Position.Right} style={{ background: '#D0D5DD', width: 8, height: 8 }} />
    </div>
  )
})

CustomNode.displayName = 'CustomNode'
export default CustomNode

import {
  MessageSquare,
  Phone,
  Radio,
  Send,
  Wifi,
  Zap,
} from 'lucide-react'
import { formatDateTime } from '../../../../shared/formatters.js'
import { GATEWAY_PLATFORM_META } from '../../../../shared/statusMeta.js'
import GatewayStatusBadge from './GatewayStatusBadge.jsx'

const PLATFORM_ICONS = {
  wechat: MessageSquare,
  qq: MessageSquare,
  telegram: Send,
  feishu: Zap,
  wecom: Radio,
  line: MessageSquare,
}

const GATEWAY_TIME_OPTIONS = Object.freeze({
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

export default function GatewayPlatformCard({ data }) {
  const meta = GATEWAY_PLATFORM_META[data.platform] || { label: data.platform, color: '#666' }
  const Icon = PLATFORM_ICONS[data.platform] || MessageSquare

  return (
    <div className="card space-y-3 p-4" style={{ borderLeft: `4px solid ${meta.color}` }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon size={18} style={{ color: meta.color }} />
          <span className="font-semibold text-gray-800">{meta.label}</span>
        </div>
        <GatewayStatusBadge status={data.status} />
      </div>

      {data.status_detail ? <div className="text-xs text-gray-400">{data.status_detail}</div> : null}

      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div>
          <div className="text-lg font-bold text-blue-600">{data.inbound_count ?? 0}</div>
          <div className="text-gray-500">入站</div>
        </div>
        <div>
          <div className="text-lg font-bold text-green-600">{data.outbound_count ?? 0}</div>
          <div className="text-gray-500">出站</div>
        </div>
        <div>
          <div className={`text-lg font-bold ${(data.error_count ?? 0) > 0 ? 'text-red-500' : 'text-gray-400'}`}>
            {data.error_count ?? 0}
          </div>
          <div className="text-gray-500">错误</div>
        </div>
      </div>

      {(data.avg_latency_ms ?? 0) > 0 ? (
        <div className="text-xs text-gray-500">
          平均延迟 <span className="font-medium text-gray-700">{data.avg_latency_ms}ms</span>
        </div>
      ) : null}

      {data.bridge_status ? (
        <div className="flex items-center gap-1 text-xs">
          <Wifi size={11} className="text-green-500" />
          <span className="text-gray-500">
            桥接：<span className="font-medium text-gray-700">{data.bridge_status}</span>
          </span>
        </div>
      ) : null}

      {(data.call_active ?? 0) > 0 ? (
        <div className="flex items-center gap-1 text-xs text-blue-600">
          <Phone size={11} />
          <span>活跃通话：{data.call_active}</span>
        </div>
      ) : null}

      {data.last_event_ts_ms ? (
        <div className="text-xs text-gray-400">最后活动：{formatDateTime(data.last_event_ts_ms, { options: GATEWAY_TIME_OPTIONS })}</div>
      ) : null}
    </div>
  )
}

import React from 'react'
import { Link } from 'react-router-dom'
import {
  Brain,
  Heart,
  Orbit,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { buildDigitalLifeAlerts, getDigitalLifeRuntimeSummary, getNonLiveSlots } from '../utils/runtimeHealth.js'

const SEVERITY_STYLES = {
  critical: 'border-red-200 bg-red-50 text-red-700',
  warning: 'border-orange-200 bg-orange-50 text-orange-700',
  info: 'border-blue-200 bg-blue-50 text-blue-700',
}

function Pill({ icon: Icon, label, value }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-600">
      <Icon size={12} />
      <span>{label}</span>
      <span className="font-semibold text-gray-800">{value ?? '—'}</span>
    </span>
  )
}

export default function DigitalLifeRuntimeCard({ runtimeSnapshot, promptStats = null, compact = false }) {
  if (!runtimeSnapshot) {
    return null
  }

  const summary = getDigitalLifeRuntimeSummary(runtimeSnapshot)
  const runtimeAlerts = buildDigitalLifeAlerts(runtimeSnapshot, { promptStats })
  const nonLiveSlots = getNonLiveSlots(runtimeSnapshot)

  return (
    <div className="rounded-2xl border border-primary-100 bg-gradient-to-r from-primary-50 via-white to-pink-50 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-medium text-primary">
            <Sparkles size={12} />
            数字生命体运行摘要
          </div>
          <div className="mt-3 text-lg font-semibold text-gray-900">
            {summary.headline || `当前处于${summary.mode}，关系阶段为${summary.bondStage}，回复姿态为${summary.replyPosture}。`}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <Pill icon={Orbit} label="存在态" value={summary.mode} />
            <Pill icon={Heart} label="关系" value={summary.bondStage} />
            <Pill icon={Sparkles} label="回复" value={summary.replyPosture} />
            <Pill icon={Brain} label="风险" value={summary.riskLevel} />
            <Pill icon={ShieldAlert} label="槽位" value={nonLiveSlots.length === 0 ? '全部在线' : `${nonLiveSlots.length} 个异常`} />
          </div>
        </div>

        {!compact ? (
          <div className="flex flex-wrap gap-2">
            <Link to="/digital-life" className="btn-secondary text-sm">数字生命体总览</Link>
            <Link to="/response-policy" className="btn-secondary text-sm">回复策略</Link>
            <Link to="/self-model" className="btn-secondary text-sm">自我模型</Link>
          </div>
        ) : null}
      </div>

      {summary.notableSignals?.length > 0 && !compact ? (
        <div className="mt-4 rounded-xl border border-gray-200 bg-white/80 px-4 py-3 text-sm text-gray-600">
          {summary.notableSignals[0]}
        </div>
      ) : null}

      {runtimeAlerts.length > 0 ? (
        <div className="mt-4 space-y-2">
          {runtimeAlerts.slice(0, compact ? 3 : 4).map((item) => (
            <div
              key={`${item.reason_code}-${item.to || 'none'}`}
              className={`rounded-xl border px-3 py-2 text-sm ${SEVERITY_STYLES[item.severity] || SEVERITY_STYLES.info}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium">{item.title || item.reason_code}</div>
                  <div className="mt-1 text-xs opacity-90">{item.detail}</div>
                </div>
                {item.to ? (
                  <Link to={item.to} className="shrink-0 text-xs font-medium hover:underline">
                    查看
                  </Link>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-green-200 bg-green-50 px-3 py-1.5 text-xs font-medium text-green-700">
          <ShieldCheck size={12} />
          当前没有检测到明显的生命体运行风险
        </div>
      )}
    </div>
  )
}

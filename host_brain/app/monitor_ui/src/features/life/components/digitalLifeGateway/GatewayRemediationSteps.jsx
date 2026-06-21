import React from 'react'
import { Link } from 'react-router-dom'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'

export default function GatewayRemediationSteps({ steps }) {
  if (steps.length === 0) {
    return (
      <div className="mt-4">
        <EmptyStateCard
          title="暂无自愈建议"
          hint="闭环健康时，这里会保持空白或仅给出预防性提示。"
          className="p-4"
        />
      </div>
    )
  }

  return (
    <div className="mt-4 space-y-3">
      {steps.slice(0, 6).map((step) => (
        <div key={step.step_id} className="rounded-2xl border border-primary-100/70 bg-white/88 p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">
              {`P${step.priority}`}
            </span>
            <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
              {step.category}
            </span>
            <div className="text-sm font-semibold text-gray-900">{step.title}</div>
          </div>
          <div className="mt-2 text-sm leading-6 text-gray-600">{step.detail}</div>
          {step.operator_hint ? (
            <div className="mt-2 text-xs text-gray-500">{step.operator_hint}</div>
          ) : null}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {(step.evidence_codes || []).slice(0, 3).map((code) => (
              <span
                key={`${step.step_id}-${code}`}
                className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
              >
                {code}
              </span>
            ))}
            {step.target_path ? (
              <Link
                to={step.target_path}
                className="ml-auto inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
              >
                {step.target_label || '打开排查入口'}
              </Link>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  )
}

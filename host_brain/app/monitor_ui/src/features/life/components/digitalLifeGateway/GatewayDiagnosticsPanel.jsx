import React from 'react'
import { Link } from 'react-router-dom'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { compactText } from '../../../../utils/digitalLife.js'
import { getHumanoidDiagnosticStatusClass } from '../../digitalLifeGatewayModel.js'

export default function GatewayDiagnosticsPanel({
  humanoidLoopDiagnosticsData,
  humanoidDiagnostics,
}) {
  if (!humanoidLoopDiagnosticsData) return null

  return (
    <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-gray-600">异常回放摘要 / 根因归纳</div>
          <div className="mt-1 text-sm text-gray-700">
            {humanoidLoopDiagnosticsData?.summary || '当前暂无可展示的异常因果摘要。'}
          </div>
        </div>
        <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600">
          {`analysis ${humanoidLoopDiagnosticsData?.diagnosed_count ?? humanoidDiagnostics.length}`}
        </span>
      </div>

      {humanoidDiagnostics.length > 0 ? (
        <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
          {humanoidDiagnostics.map((item) => (
            <div key={`${item.request_id}-${item.root_cause_code}`} className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getHumanoidDiagnosticStatusClass(item.status)}`}>
                  {item.status}
                </span>
                <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">
                  {item.root_cause_code}
                </span>
                <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                  {item.suspected_breakpoint}
                </span>
                <span className="ml-auto font-mono text-[11px] text-gray-400">
                  {String(item.request_id || '').slice(0, 18)}
                </span>
              </div>

              <div className="mt-3 text-sm font-semibold leading-6 text-gray-900">
                {item.root_cause_summary}
              </div>

              <div className="mt-3 grid gap-3 xl:grid-cols-2">
                <div className="rounded-xl border border-primary-100/70 bg-primary-50/50 px-3 py-2">
                  <div className="text-[11px] font-semibold text-gray-500">输入摘要</div>
                  <div className="mt-1 text-sm leading-6 text-gray-700">
                    {compactText(item.input_text || '暂无输入采样', 120)}
                  </div>
                </div>
                <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2">
                  <div className="text-[11px] font-semibold text-gray-500">输出摘要</div>
                  <div className="mt-1 text-sm leading-6 text-gray-700">
                    {compactText(item.output_text || '暂无输出采样', 120)}
                  </div>
                </div>
              </div>

              {item.reason_hint ? (
                <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-800">
                  {`reason_hint=${item.reason_hint}`}
                </div>
              ) : null}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {(item.evidence_codes || []).slice(0, 4).map((code) => (
                  <span
                    key={`${item.request_id}-${code}`}
                    className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                  >
                    {code}
                  </span>
                ))}
                <Link
                  to={item.recommended_view_path}
                  className="ml-auto inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                >
                  {item.recommended_view_label || '打开异常回放'}
                </Link>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-4">
          <EmptyStateCard
            title="暂无异常因果摘要"
            hint="当前时间窗口内没有提取到可回放的代表性异常 request。"
            className="p-4"
          />
        </div>
      )}
    </div>
  )
}

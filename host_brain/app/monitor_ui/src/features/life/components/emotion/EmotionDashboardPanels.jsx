import { Activity, Heart, Zap } from 'lucide-react'

import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'

function HormoneBar({ view }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-600">{view.label}</span>
        <span className="font-mono font-semibold" style={{ color: view.color }}>{view.valueLabel}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-gray-100">
        <div className="h-full rounded-full" style={{ width: view.width, backgroundColor: view.color }} />
      </div>
    </div>
  )
}

function Gauge({ view }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-600">{view.label}</span>
        <span className={`font-mono font-bold ${view.color}`}>{view.valueLabel}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-gray-200">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: view.width,
            backgroundColor: view.backgroundColor,
          }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-gray-400">
        <span>-1.0</span>
        <span>0</span>
        <span>+1.0</span>
      </div>
    </div>
  )
}

export function CurrentEmotionPanel({ moodStyle, MoodIcon, emotionTagLabel, gauges, hormoneBars, gateView }) {
  return (
    <div className="card space-y-4 p-5">
      <h2 className="flex items-center gap-1 text-sm font-semibold text-gray-700">
        <Heart size={14} className="text-pink-500" />
        当前状态
      </h2>
      <div className="py-4 text-center">
        <div className={`inline-flex items-center gap-2 rounded-full px-4 py-2 ${moodStyle.bg}`}>
          <MoodIcon size={20} className={moodStyle.color} />
          <span className={`font-bold ${moodStyle.color}`}>{moodStyle.label}</span>
        </div>
        <div className="mt-2 text-xs text-gray-500">
          主情绪：<span className="text-gray-700">{emotionTagLabel}</span>
        </div>
        <div className="mt-2">
          <NoticeBanner variant="info" className="text-left">
            这里已经直接读取运行时快照，不再使用前端模拟情绪数据。
          </NoticeBanner>
        </div>
      </div>
      <div className="space-y-3">
        {gauges.map((gauge) => (
          <Gauge key={gauge.label} view={gauge} />
        ))}
      </div>
      <div className="space-y-2 border-t pt-3">
        <div className="text-xs font-medium text-gray-600">边缘系统基调（Endocrine）</div>
        {hormoneBars.map((bar) => (
          <HormoneBar key={bar.label} view={bar} />
        ))}
        <div className="flex items-center justify-between text-xs">
          <span className="text-gray-500">Gate</span>
          <span className={`font-semibold ${gateView.gateTone}`}>{gateView.gateLabel}</span>
        </div>
      </div>
    </div>
  )
}

export function EmotionDistributionPanel({ topEmotionRows, vtsStatCards }) {
  return (
    <div className="card space-y-4 p-5">
      <h2 className="flex items-center gap-1 text-sm font-semibold text-gray-700">
        <Activity size={14} className="text-indigo-500" />
        情绪标签分布
      </h2>
      {topEmotionRows.length > 0 ? (
        <div className="space-y-2">
          {topEmotionRows.map((emotion) => (
            <div key={emotion.key} className="flex items-center gap-2">
              <span className="w-20 shrink-0 truncate text-xs text-gray-600">{emotion.label}</span>
              <div className="h-4 flex-1 overflow-hidden rounded-full bg-gray-100">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${emotion.pct}%`,
                    backgroundColor: emotion.color,
                  }}
                />
              </div>
              <span className="w-12 text-right text-xs text-gray-500">{emotion.pct}%</span>
              <span className="w-8 text-right text-xs text-gray-400">{emotion.count}</span>
            </div>
          ))}
        </div>
      ) : (
        <EmptyStateCard title="暂无情绪样本" hint="等待情绪样本进入后再展示分布。" className="p-6" />
      )}

      {vtsStatCards.length > 0 ? (
        <div className="grid grid-cols-3 gap-2 border-t pt-2 text-center text-xs">
          {vtsStatCards.map((card) => (
            <div key={card.label}>
              <div className={`text-lg font-bold ${card.className}`}>{card.value}</div>
              <div className="text-gray-500">{card.label}</div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function BondTemperaturePanel({ bondMetrics, bondStageLabel, memorySubsystemRows }) {
  return (
    <div className="card space-y-4 p-5">
      <h2 className="flex items-center gap-1 text-sm font-semibold text-gray-700">
        <Zap size={14} className="text-pink-500" />
        关系温度
      </h2>
      <div className="py-4 text-center">
        <div className="relative mx-auto h-32 w-32">
          <svg viewBox="0 0 120 120" className="h-full w-full">
            <circle cx="60" cy="60" r="50" fill="none" stroke="#e5e7eb" strokeWidth="8" />
            <circle
              cx="60"
              cy="60"
              r="50"
              fill="none"
              stroke="#ec4899"
              strokeDasharray={bondMetrics.bondTemperatureArc}
              strokeLinecap="round"
              transform="rotate(-90 60 60)"
              className="transition-all duration-1000"
            />
            <text x="60" y="55" textAnchor="middle" className="fill-gray-800 text-lg font-bold">
              {bondMetrics.bondTemperatureDegree}
            </text>
            <text x="60" y="72" textAnchor="middle" className="fill-gray-500 text-[10px]">
              {bondStageLabel}
            </text>
          </svg>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="card p-2 text-center">
          <div className="font-bold text-pink-600">{bondStageLabel}</div>
          <div className="text-gray-500">当前阶段</div>
        </div>
        <div className="card p-2 text-center">
          <div className="font-bold text-gray-700">{bondMetrics.bondTemperatureFormatted}</div>
          <div className="text-gray-500">温度值</div>
        </div>
      </div>

      {memorySubsystemRows.length > 0 ? (
        <div className="space-y-1 border-t pt-2">
          <div className="mb-1 text-xs font-medium text-gray-600">记忆子系统成功率</div>
          {memorySubsystemRows.map((subsystem) => (
            <div key={subsystem.key} className="flex items-center justify-between text-xs">
              <span className="text-gray-600">{subsystem.label}</span>
              <span className={subsystem.tone}>{subsystem.successRate}%</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

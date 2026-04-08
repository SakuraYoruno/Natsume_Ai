import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Clock3,
  MoonStar,
  RefreshCw,
  ShieldAlert,
  Sun,
  Zap,
} from 'lucide-react'
import useAppStore from '../store/useAppStore.js'
import {
  fetchSysModeHistory,
  fetchSysModeStatus,
  switchSysMode,
  updateAutoRules,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import { DASH, formatMode, MODE_META } from '../utils/digitalLife.js'

const MODE_ORDER = ['normal', 'focus', 'gaming', 'sleep', 'maintenance', 'locked']

const MODE_COLOR_CLASS = {
  normal: {
    active: 'ring-2 ring-green-500 bg-green-50 border-green-300',
    hover: 'hover:bg-green-50 hover:border-green-300',
  },
  focus: {
    active: 'ring-2 ring-blue-500 bg-blue-50 border-blue-300',
    hover: 'hover:bg-blue-50 hover:border-blue-300',
  },
  gaming: {
    active: 'ring-2 ring-purple-500 bg-purple-50 border-purple-300',
    hover: 'hover:bg-purple-50 hover:border-purple-300',
  },
  sleep: {
    active: 'ring-2 ring-indigo-500 bg-indigo-50 border-indigo-300',
    hover: 'hover:bg-indigo-50 hover:border-indigo-300',
  },
  maintenance: {
    active: 'ring-2 ring-yellow-500 bg-yellow-50 border-yellow-300',
    hover: 'hover:bg-yellow-50 hover:border-yellow-300',
  },
  locked: {
    active: 'ring-2 ring-red-500 bg-red-50 border-red-300',
    hover: 'hover:bg-red-50 hover:border-red-300',
  },
  fallback: {
    active: 'ring-2 ring-gray-500 bg-gray-50 border-gray-300',
    hover: '',
  },
}

const AUTO_RULE_DEFS = {
  gaming_detect: {
    label: '检测游戏进程',
    desc: '识别到游戏场景后自动切换到游戏态。',
  },
  gaming_exit: {
    label: '游戏退出回收',
    desc: '游戏结束后回到正常态，恢复日常陪伴逻辑。',
  },
  idle_to_sleep: {
    label: '空闲转睡眠',
    desc: '长时间空闲并进入夜间窗口后自动转入睡眠态。',
  },
  wakeup_on_msg: {
    label: '消息唤醒',
    desc: '在睡眠态收到消息时自动唤醒。',
  },
  morning_wakeup: {
    label: '晨间唤醒',
    desc: '晨间首次交互时从睡眠态切回在线态。',
  },
}

const STATE_AXIS_ITEMS = [
  {
    title: '存在状态轴',
    desc: '决定现在以什么硬状态存在，是数字生命最外层的开关。',
  },
  {
    title: '离线时长',
    desc: 'absence_band 与 reconnect_style 影响重连氛围，不直接等于 sys_mode。',
  },
  {
    title: '回复姿态',
    desc: 'reply_posture 决定这轮会更温柔、克制、平衡还是收敛。',
  },
  {
    title: '内在驱动',
    desc: '动机、反思与风险护栏会继续塑造为什么这样回应。',
  },
]

function mapSourceText(source = '') {
  if (source === 'manual') return '手动'
  if (source === 'auto_gaming') return '游戏自动'
  if (source === 'auto_idle') return '睡眠自动'
  if (source === 'auto_wakeup') return '唤醒自动'
  if (source === 'skill') return '技能'
  if (source.startsWith('auto')) return '自动'
  return source || 'unknown'
}

function ModeButton({ mode, currentMode, validTransitions, onSwitch, switching }) {
  const meta = MODE_META[mode] || { emoji: '❔', label: mode, desc: '' }
  const isActive = mode === currentMode
  const canSwitch = validTransitions.includes(mode) && !isActive
  const isDisabled = !canSwitch && !isActive
  const colors = MODE_COLOR_CLASS[mode] || MODE_COLOR_CLASS.fallback

  return (
    <button
      className={[
        'relative flex min-h-[112px] flex-col items-center justify-center gap-1 rounded-xl border px-4 py-3 text-center transition-all duration-200',
        isActive ? `${colors.active} shadow-sm` : 'border-gray-200 bg-white',
        canSwitch ? `${colors.hover} cursor-pointer` : '',
        isDisabled ? 'cursor-not-allowed opacity-40' : '',
      ].join(' ').trim()}
      onClick={() => canSwitch && onSwitch(mode)}
      disabled={isDisabled || switching}
      title={isActive ? `当前模式：${meta.label}` : canSwitch ? `切换到 ${meta.label}` : `当前无法直接切换到 ${meta.label}`}
    >
      <span className="text-2xl">{meta.emoji}</span>
      <span className="text-sm font-semibold text-gray-800">{meta.label}</span>
      <span className="text-[11px] leading-5 text-gray-500">{meta.desc}</span>
      {isActive ? (
        <span className="absolute -right-1 -top-1 flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-green-500" />
        </span>
      ) : null}
    </button>
  )
}

export default function RuntimeMode() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const [status, setStatus] = useState(null)
  const [history, setHistory] = useState([])
  const [switching, setSwitching] = useState(false)
  const [switchResult, setSwitchResult] = useState(null)
  const [error, setError] = useState(null)

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchSysModeStatus()
      setStatus(data)
      setError(null)
    } catch (fetchError) {
      setError(fetchError.message || '存在状态轴加载失败')
    }
  }, [])

  const loadHistory = useCallback(async () => {
    try {
      const data = await fetchSysModeHistory({ limit: 15 })
      setHistory(data.items || [])
    } catch (fetchError) {
      console.warn('Failed to load mode history:', fetchError)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    loadHistory()
  }, [loadHistory, loadStatus])

  useEffect(() => {
    if (!pollingEnabled) return undefined
    const timer = setInterval(() => {
      loadStatus()
      loadHistory()
    }, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [loadHistory, loadStatus, pollingEnabled, pollingInterval])

  const handleSwitch = async (targetMode) => {
    const meta = MODE_META[targetMode] || { label: targetMode, semantic: '' }
    if (!window.confirm(`确认切换到 ${meta.label}？\n\n${meta.semantic || meta.desc || ''}`)) {
      return
    }

    setSwitching(true)
    setSwitchResult(null)
    try {
      const result = await switchSysMode({
        target_mode: targetMode,
        reason: 'WebUI 手动切换',
        source: 'manual',
      })
      setSwitchResult(result)
      await Promise.all([loadStatus(), loadHistory()])
    } catch (switchError) {
      setSwitchResult({ success: false, error: switchError.message || '切换失败' })
    } finally {
      setSwitching(false)
      setTimeout(() => setSwitchResult(null), 4000)
    }
  }

  const handleToggleRule = async (ruleKey, currentValue) => {
    try {
      await updateAutoRules({ rules: { [ruleKey]: !currentValue } })
      await loadStatus()
    } catch (updateError) {
      console.warn('Failed to update auto rule:', updateError)
    }
  }

  const currentMode = String(status?.sys_mode || 'normal').toLowerCase()
  const currentMeta = MODE_META[currentMode] || MODE_META.normal
  const validTransitions = status?.valid_transitions || []
  const autoRules = status?.auto_rules || {}
  const enabledRules = useMemo(
    () => Object.entries(AUTO_RULE_DEFS).filter(([key]) => autoRules[key] !== false).length,
    [autoRules]
  )

  if (error && !status) {
    return (
      <div className="space-y-4">
        <DigitalLifeNav />
        <NoticeBanner variant="error" title="存在状态轴加载失败">{error}</NoticeBanner>
        <button className="btn-primary" onClick={loadStatus}>重试</button>
      </div>
    )
  }

  if (!status && !error) {
    return (
      <div className="space-y-4">
        <DigitalLifeNav />
        <NoticeBanner variant="info" title="加载中">正在同步存在状态轴快照…</NoticeBanner>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">存在状态轴</h1>
          <p className="text-sm text-gray-500">管理数字生命的硬状态，不直接覆盖离线时长、动机和回复姿态。</p>
        </div>
        <button className="btn-secondary" onClick={() => Promise.allSettled([loadStatus(), loadHistory()])} disabled={switching}>
          <RefreshCw size={14} className={switching ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <DigitalLifeNav />

      <PageHero
        icon={Zap}
        eyebrow="当前存在态"
        title={`${currentMeta.emoji} ${currentMeta.label}`}
        description={currentMeta.semantic}
        gradientClass="from-primary-50 via-white to-indigo-50"
        metaItems={[
          `空闲：${status?.idle_minutes ?? 0} 分钟`,
          `可切换：${validTransitions.length} 项`,
          `自动规则：${enabledRules}/${Object.keys(AUTO_RULE_DEFS).length}`,
          status?.is_night_time ? '夜间窗口' : '日间窗口',
        ]}
      />

      {switchResult ? (
        <NoticeBanner
          variant={switchResult.success ? 'success' : 'error'}
          title={switchResult.success ? '模式切换成功' : '模式切换失败'}
        >
          {switchResult.success
            ? `${formatMode(switchResult.from_mode)} → ${formatMode(switchResult.to_mode)}`
            : switchResult.error || '未知错误'}
        </NoticeBanner>
      ) : null}

      {error && status ? <NoticeBanner variant="warning" title="状态同步异常">{error}</NoticeBanner> : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="当前模式" value={currentMeta.label} hint={currentMeta.scheduler} tone="text-primary" />
        <MetricCard label="空闲时间" value={`${status?.idle_minutes ?? 0} 分钟`} hint="用于辅助自动切换" tone="text-blue-600" />
        <MetricCard label="时间窗口" value={status?.is_night_time ? '夜间' : '日间'} hint={status?.is_night_time ? '适合睡眠态' : '适合在线态'} tone="text-indigo-600" />
        <MetricCard label="自动规则" value={`${enabledRules}/${Object.keys(AUTO_RULE_DEFS).length}`} hint={status?.degraded ? '当前状态轴降级' : '状态轴正常'} tone={status?.degraded ? 'text-orange-500' : 'text-green-600'} />
      </div>

      <div className="card p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-800">可切换模式</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {MODE_ORDER.map((mode) => (
            <ModeButton
              key={mode}
              mode={mode}
              currentMode={currentMode}
              validTransitions={validTransitions}
              onSwitch={handleSwitch}
              switching={switching}
            />
          ))}
        </div>
        <div className="mt-3 text-xs text-gray-500">
          灰色不可点击表示当前状态机不支持直接跳转；这是最外层硬状态，不等于完整人格状态。
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="card p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-800">自动切换规则</h2>
          <div className="space-y-3">
            {Object.entries(AUTO_RULE_DEFS).map(([key, rule]) => {
              const enabled = autoRules[key] !== false
              return (
                <div key={key} className="flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-medium text-gray-700">{rule.label}</div>
                    <div className="text-xs leading-5 text-gray-400">{rule.desc}</div>
                  </div>
                  <button
                    className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                      enabled ? 'bg-primary' : 'bg-gray-300'
                    }`}
                    onClick={() => handleToggleRule(key, enabled)}
                  >
                    <span
                      className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                        enabled ? 'translate-x-6' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
              )
            })}
          </div>
        </div>

        <div className="card p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-800">不是只有状态轴</h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {STATE_AXIS_ITEMS.map((item) => (
              <div key={item.title} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                <div className="text-sm font-semibold text-gray-800">{item.title}</div>
                <p className="mt-2 text-xs leading-5 text-gray-500">{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-800">切换历史</h2>
        {history.length > 0 ? (
          <div className="space-y-2">
            {history.map((item, index) => (
              <div key={item.event_id || index} className={`flex items-center gap-3 rounded-lg px-3 py-2 text-xs ${item.success ? 'bg-gray-50' : 'bg-red-50'}`}>
                <span className="font-medium text-gray-700">
                  {formatMode(item.from_mode)} → {formatMode(item.to_mode)}
                </span>
                <span className="rounded bg-gray-200 px-1.5 py-0.5 text-gray-600">{mapSourceText(item.source)}</span>
                <span className="text-gray-400">{new Date(item.ts_ms).toLocaleString('zh-CN')}</span>
                {!item.success ? <span className="text-red-500">失败</span> : null}
              </div>
            ))}
          </div>
        ) : (
          <EmptyStateCard title="暂无切换历史" hint="状态切换后会在这里留下记录。" className="p-6" />
        )}
      </div>
    </div>
  )
}

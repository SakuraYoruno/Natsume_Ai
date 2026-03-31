import React, { useState, useEffect, useCallback } from 'react'
import useAppStore from '../store/useAppStore.js'
import {
  fetchSysModeStatus,
  switchSysMode,
  fetchSysModeHistory,
  updateAutoRules,
} from '../api/obs.js'

// 模式定义：图标 + 颜色 + 中文名 + 说明
const MODE_DEFS = {
  normal:      { emoji: '🟢', color: 'green',  label: '正常',   desc: '全功能开放，所有子系统正常运作' },
  focus:       { emoji: '🎯', color: 'blue',   label: '专注',   desc: '抑制主动对话和 LOW 优先级调度，仅保留被动响应' },
  gaming:      { emoji: '🎮', color: 'purple', label: '游戏',   desc: '禁用本地 14B，强制 Gemini→Codex→DeepSeek 云端链，OCR/VTS/Bond/记忆跳过，延迟预算 <2s' },
  sleep:       { emoji: '💤', color: 'indigo',  label: '睡眠',   desc: '梦境生成触发、仅 HIGH+MEDIUM 优先级，CONTROL 类执行禁止' },
  maintenance: { emoji: '🔧', color: 'yellow', label: '维护',   desc: '阻断所有执行路径，系统维护中' },
  locked:      { emoji: '🔒', color: 'red',    label: '锁定',   desc: '阻断所有执行路径，系统锁定' },
}

// 自动规则定义
const AUTO_RULE_DEFS = {
  gaming_detect:  { label: '🎮 游戏进程检测', desc: '检测到游戏进程 → 自动切换 gaming' },
  gaming_exit:    { label: '🎮 游戏进程退出', desc: '游戏退出 → 自动恢复 normal' },
  idle_to_sleep:  { label: '💤 空闲自动睡眠', desc: '长时间空闲 + 深夜时段 → 自动切换 sleep' },
  wakeup_on_msg:  { label: '📱 消息唤醒',     desc: '收到用户消息 → 从 sleep 唤醒到 normal' },
  morning_wakeup: { label: '☀️ 早间唤醒',     desc: '06:00-10:00 首次互动 → 从 sleep 唤醒' },
}

const MODE_ORDER = ['normal', 'focus', 'gaming', 'sleep', 'maintenance', 'locked']

function ModeButton({ mode, currentMode, validTransitions, onSwitch, switching }) {
  const def = MODE_DEFS[mode] || { emoji: '❓', color: 'gray', label: mode, desc: '' }
  const isActive = mode === currentMode
  const canSwitch = validTransitions.includes(mode) && !isActive
  const isDisabled = !canSwitch && !isActive

  const colorMap = {
    green:  { active: 'ring-2 ring-green-500 bg-green-50 border-green-300', hover: 'hover:bg-green-50 hover:border-green-300' },
    blue:   { active: 'ring-2 ring-blue-500 bg-blue-50 border-blue-300', hover: 'hover:bg-blue-50 hover:border-blue-300' },
    purple: { active: 'ring-2 ring-purple-500 bg-purple-50 border-purple-300', hover: 'hover:bg-purple-50 hover:border-purple-300' },
    indigo: { active: 'ring-2 ring-indigo-500 bg-indigo-50 border-indigo-300', hover: 'hover:bg-indigo-50 hover:border-indigo-300' },
    yellow: { active: 'ring-2 ring-yellow-500 bg-yellow-50 border-yellow-300', hover: 'hover:bg-yellow-50 hover:border-yellow-300' },
    red:    { active: 'ring-2 ring-red-500 bg-red-50 border-red-300', hover: 'hover:bg-red-50 hover:border-red-300' },
    gray:   { active: 'ring-2 ring-gray-500 bg-gray-50 border-gray-300', hover: '' },
  }
  const colors = colorMap[def.color] || colorMap.gray

  return (
    <button
      className={`
        relative flex flex-col items-center gap-1 px-4 py-3 rounded-lg border transition-all duration-200
        ${isActive ? colors.active + ' shadow-sm' : 'border-gray-200 bg-white'}
        ${canSwitch ? colors.hover + ' cursor-pointer' : ''}
        ${isDisabled ? 'opacity-40 cursor-not-allowed' : ''}
      `}
      onClick={() => canSwitch && onSwitch(mode)}
      disabled={isDisabled || switching}
      title={isActive ? `当前模式：${def.label}` : canSwitch ? `切换到 ${def.label} 模式` : `不能从当前模式切换到 ${def.label}`}
    >
      <span className="text-2xl">{def.emoji}</span>
      <span className="text-sm font-medium">{def.label}</span>
      {isActive && (
        <span className="absolute -top-1 -right-1 flex h-3 w-3">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-3 w-3 bg-green-500" />
        </span>
      )}
    </button>
  )
}

export default function RuntimeMode() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const [status, setStatus] = useState(null)
  const [history, setHistory] = useState([])
  const [switching, setSwitching] = useState(false)
  const [switchResult, setSwitchResult] = useState(null)
  const [error, setError] = useState(null)

  // 拉取状态
  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchSysModeStatus()
      setStatus(data)
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  // 拉取历史
  const loadHistory = useCallback(async () => {
    try {
      const data = await fetchSysModeHistory({ limit: 15 })
      setHistory(data.items || [])
    } catch (e) {
      console.warn('Failed to load mode history:', e)
    }
  }, [])

  // 初始加载
  useEffect(() => {
    loadStatus()
    loadHistory()
  }, [loadStatus, loadHistory])

  // 轮询
  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(() => {
      loadStatus()
      loadHistory()
    }, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, loadStatus, loadHistory])

  // 切换模式
  const handleSwitch = async (targetMode) => {
    const def = MODE_DEFS[targetMode] || { label: targetMode }
    if (!window.confirm(`确认切换到 ${def.label} 模式？\n\n${def.desc || ''}`)) return

    setSwitching(true)
    setSwitchResult(null)
    try {
      const result = await switchSysMode({
        target_mode: targetMode,
        reason: `WebUI 手动切换`,
        source: 'manual',
      })
      setSwitchResult(result)
      // 刷新状态
      await loadStatus()
      await loadHistory()
    } catch (e) {
      setSwitchResult({ success: false, error: e.message })
    } finally {
      setSwitching(false)
      // 3 秒后清除结果提示
      setTimeout(() => setSwitchResult(null), 3000)
    }
  }

  // 切换自动规则
  const handleToggleRule = async (ruleKey, currentValue) => {
    try {
      await updateAutoRules({ rules: { [ruleKey]: !currentValue } })
      await loadStatus()
    } catch (e) {
      console.warn('Failed to update auto rule:', e)
    }
  }

  if (error && !status) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-500 text-lg mb-2">加载失败</p>
          <p className="text-gray-500 text-sm">{error}</p>
          <button className="mt-4 px-4 py-2 bg-primary text-white rounded hover:bg-primary-600" onClick={loadStatus}>
            重试
          </button>
        </div>
      </div>
    )
  }

  const currentMode = status?.sys_mode || 'normal'
  const currentDef = MODE_DEFS[currentMode] || MODE_DEFS.normal
  const validTransitions = status?.valid_transitions || []
  const autoRules = status?.auto_rules || {}

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div>
        <h1 className="text-xl font-bold text-gray-800">运行模式管理</h1>
        <p className="text-sm text-gray-500 mt-1">
          查看和切换系统运行模式，管理智能自动切换规则
        </p>
      </div>

      {/* 当前模式大展示 */}
      <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
        <div className="flex items-center gap-4">
          <span className="text-5xl">{currentDef.emoji}</span>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-2xl font-bold text-gray-800">{currentDef.label} 模式</h2>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium bg-${currentDef.color}-100 text-${currentDef.color}-700`}>
                {currentMode}
              </span>
            </div>
            <p className="text-gray-500 mt-1">{currentDef.desc}</p>
            {status && (
              <div className="flex items-center gap-4 mt-2 text-xs text-gray-400">
                <span>空闲 {status.idle_minutes} 分钟</span>
                <span>{status.is_night_time ? '🌙 深夜时段' : '☀️ 日间'}</span>
                {status.degraded && (
                  <span className="text-yellow-600">⚠️ 降级模式</span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 切换结果提示 */}
      {switchResult && (
        <div className={`rounded-lg p-3 text-sm ${switchResult.success ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
          {switchResult.success
            ? `✅ 已切换：${switchResult.from_mode} → ${switchResult.to_mode}`
            : `❌ 切换失败：${switchResult.error || '未知错误'}`
          }
        </div>
      )}

      {/* 模式切换按钮 */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-600 mb-3">切换模式</h3>
        <div className="grid grid-cols-6 gap-3">
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
        <p className="text-xs text-gray-400 mt-3">
          灰色不可点击的模式表示不能从当前模式直接切换。切换前会弹出确认对话框。
        </p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* 自动切换规则 */}
        <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-600 mb-3">智能自动切换规则</h3>
          <div className="space-y-3">
            {Object.entries(AUTO_RULE_DEFS).map(([key, def]) => {
              const enabled = autoRules[key] ?? true
              return (
                <div key={key} className="flex items-center justify-between">
                  <div>
                    <span className="text-sm font-medium">{def.label}</span>
                    <p className="text-xs text-gray-400">{def.desc}</p>
                  </div>
                  <button
                    className={`
                      relative inline-flex h-6 w-11 items-center rounded-full transition-colors
                      ${enabled ? 'bg-primary' : 'bg-gray-300'}
                    `}
                    onClick={() => handleToggleRule(key, enabled)}
                  >
                    <span className={`
                      inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-sm
                      ${enabled ? 'translate-x-6' : 'translate-x-1'}
                    `} />
                  </button>
                </div>
              )
            })}
          </div>
        </div>

        {/* 切换历史 */}
        <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-600 mb-3">切换历史</h3>
          {history.length === 0 ? (
            <p className="text-sm text-gray-400">暂无切换记录</p>
          ) : (
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {history.map((item, idx) => {
                const fromDef = MODE_DEFS[item.from_mode] || { emoji: '❓', label: item.from_mode }
                const toDef = MODE_DEFS[item.to_mode] || { emoji: '❓', label: item.to_mode }
                const timeStr = new Date(item.ts_ms).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                const dateStr = new Date(item.ts_ms).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
                return (
                  <div key={item.event_id || idx} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs ${item.success ? 'bg-gray-50' : 'bg-red-50'}`}>
                    <span>{fromDef.emoji}</span>
                    <span className="text-gray-400">→</span>
                    <span>{toDef.emoji}</span>
                    <span className="font-medium text-gray-700">{fromDef.label} → {toDef.label}</span>
                    <span className={`px-1.5 py-0.5 rounded text-xs ${
                      item.source === 'manual' ? 'bg-blue-100 text-blue-600' :
                      item.source.startsWith('auto') ? 'bg-green-100 text-green-600' :
                      'bg-gray-100 text-gray-600'
                    }`}>
                      {item.source === 'manual' ? '手动' :
                       item.source === 'auto_gaming' ? '🎮自动' :
                       item.source === 'auto_idle' ? '💤自动' :
                       item.source === 'auto_wakeup' ? '☀️自动' :
                       item.source}
                    </span>
                    {!item.success && <span className="text-red-500">✗</span>}
                    <span className="ml-auto text-gray-400">{dateStr} {timeStr}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* 调度器门控矩阵说明 */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-600 mb-3">模式影响矩阵</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left border-b border-gray-100">
                <th className="py-2 px-3 font-medium text-gray-500">模式</th>
                <th className="py-2 px-3 font-medium text-gray-500">调度器</th>
                <th className="py-2 px-3 font-medium text-gray-500">主要影响</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-gray-50">
                <td className="py-2 px-3">🟢 正常</td>
                <td className="py-2 px-3"><span className="text-green-600">HIGH + MEDIUM + LOW</span></td>
                <td className="py-2 px-3 text-gray-500">全功能开放</td>
              </tr>
              <tr className="border-b border-gray-50">
                <td className="py-2 px-3">🎯 专注</td>
                <td className="py-2 px-3"><span className="text-blue-600">仅 HIGH</span></td>
                <td className="py-2 px-3 text-gray-500">主动对话/通知静默，保留被动响应</td>
              </tr>
              <tr className="border-b border-gray-50">
                <td className="py-2 px-3">🎮 游戏</td>
                <td className="py-2 px-3"><span className="text-purple-600">仅 HIGH</span></td>
                <td className="py-2 px-3 text-gray-500">禁用本地 14B→强制 Gemini/Codex/DeepSeek 云端链、OCR 屏蔽、VTS/Bond 跳过</td>
              </tr>
              <tr className="border-b border-gray-50">
                <td className="py-2 px-3">💤 睡眠</td>
                <td className="py-2 px-3"><span className="text-indigo-600">HIGH + MEDIUM</span></td>
                <td className="py-2 px-3 text-gray-500">梦境生成、CONTROL 类禁止</td>
              </tr>
              <tr className="border-b border-gray-50">
                <td className="py-2 px-3">🔧 维护</td>
                <td className="py-2 px-3"><span className="text-yellow-600">全部禁止</span></td>
                <td className="py-2 px-3 text-gray-500">阻断所有执行路径</td>
              </tr>
              <tr>
                <td className="py-2 px-3">🔒 锁定</td>
                <td className="py-2 px-3"><span className="text-red-600">全部禁止</span></td>
                <td className="py-2 px-3 text-gray-500">阻断所有执行路径</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

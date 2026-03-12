import React, { useState, useRef, useCallback, useEffect } from 'react'
import { fetchUIAssertionStats, fetchAgentLoopHistory } from '../api/obs.js'
import { Bot, Play, Square, RefreshCw, CheckCircle, XCircle, Clock, Zap, ShieldAlert, History } from 'lucide-react'

// ===== 工具函数 =====
function formatTime(tsMs) {
  if (!tsMs) return '—'
  return new Date(tsMs).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function phaseBadge(phase) {
  const map = {
    AGENT_INIT:      { bg: 'bg-blue-100 text-blue-700',   label: '初始化' },
    LOOP_START:      { bg: 'bg-indigo-100 text-indigo-700', label: '开始' },
    ROUND_START:     { bg: 'bg-purple-100 text-purple-700', label: '轮次开始' },
    SCREENSHOT_DONE: { bg: 'bg-cyan-100 text-cyan-700',    label: '截图完成' },
    PLAN_GENERATED:  { bg: 'bg-yellow-100 text-yellow-700', label: '计划生成' },
    EXECUTE_DONE:    { bg: 'bg-orange-100 text-orange-700', label: '执行完成' },
    ROUND_DONE:      { bg: 'bg-teal-100 text-teal-700',    label: '轮次结束' },
    LOOP_DONE:       { bg: 'bg-green-100 text-green-700',  label: '循环结束' },
    ROUTE_ERROR:     { bg: 'bg-red-100 text-red-700',      label: '路由错误' },
  }
  const m = map[phase] || { bg: 'bg-gray-100 text-gray-600', label: phase }
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${m.bg}`}>{m.label}</span>
}

// ===== 主页面 =====
export default function AgentLoop() {
  const [goal, setGoal]           = useState('')
  const [maxRounds, setMaxRounds] = useState(5)
  const [roundTimeout, setRoundTimeout] = useState(30)
  const [totalTimeout, setTotalTimeout] = useState(180)
  const [uiAssertionData, setUiAssertionData] = useState(null)
  const [loopHistory, setLoopHistory] = useState([])  // TODO-86: 历史执行记录
  const [useMock, setUseMock]     = useState(false)
  const [useEdgeRpc, setUseEdgeRpc] = useState(false)
  const [imageConsent, setImageConsent] = useState(false)

  const [running, setRunning]     = useState(false)
  const [events, setEvents]       = useState([])
  const [loopResult, setLoopResult] = useState(null)
  const [agentInfo, setAgentInfo] = useState(null)
  const [infoLoading, setInfoLoading] = useState(false)

  const abortRef = useRef(null)
  const eventsEndRef = useRef(null)

  // TODO-63: 加载 UIAssertion 统计数据（近 1h）
  useEffect(() => {
    fetchUIAssertionStats({ window_minutes: 60 })
      .then(setUiAssertionData)
      .catch(() => {})
    // 每 5 分钟自动刷新
    const timer = setInterval(() => {
      fetchUIAssertionStats({ window_minutes: 60 })
        .then(setUiAssertionData)
        .catch(() => {})
    }, 5 * 60 * 1000)
    return () => clearInterval(timer)
  }, [])

  // TODO-86: 加载 Agent Loop 历史执行记录
  useEffect(() => {
    fetchAgentLoopHistory({ window_minutes: 1440, limit: 10 })
      .then(d => setLoopHistory(d?.items ?? []))
      .catch(() => {})
  }, [])

  // 加载 Agent 配置信息
  const loadInfo = useCallback(async () => {
    setInfoLoading(true)
    try {
      const res = await fetch('/v1/agent/info')
      if (res.ok) setAgentInfo(await res.json())
    } catch (_) {}
    setInfoLoading(false)
  }, [])

  React.useEffect(() => { loadInfo() }, [loadInfo])

  // 滚动到最新事件
  React.useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const handleRun = useCallback(async () => {
    if (!goal.trim()) return
    setRunning(true)
    setEvents([])
    setLoopResult(null)

    const body = {
      goal: goal.trim(),
      max_rounds: maxRounds,
      round_timeout_sec: roundTimeout,
      total_timeout_sec: totalTimeout,
      use_mock: useMock,
      use_edge_rpc: useEdgeRpc,
      image_upload_consent: imageConsent,
    }

    try {
      const controller = new AbortController()
      abortRef.current = controller

      const res = await fetch('/v1/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          try {
            const payload = JSON.parse(line.slice(5).trim())
            const ts = Date.now()
            setEvents((prev) => [...prev, { ...payload, _ts: ts }])
            if (payload.phase === 'LOOP_DONE') {
              setLoopResult(payload)
            }
          } catch (_) {}
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        setEvents((prev) => [...prev, { phase: 'ROUTE_ERROR', error: e.message, _ts: Date.now() }])
      }
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }, [goal, maxRounds, roundTimeout, totalTimeout, useMock, useEdgeRpc, imageConsent])

  const handleStop = useCallback(() => {
    abortRef.current?.abort()
    setRunning(false)
  }, [])

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Bot size={20} className="text-indigo-600" />
            Agent Loop 管理
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">启动自动化 Agent Loop，实时观测执行进度</p>
        </div>
        <button className="btn-secondary" onClick={loadInfo} disabled={infoLoading}>
          <RefreshCw size={14} className={infoLoading ? 'animate-spin' : ''} />
          刷新配置
        </button>
      </div>

      {/* Agent 配置信息卡片 */}
      {agentInfo && (
        <div className="card p-4 bg-indigo-50 border border-indigo-200">
          <div className="flex flex-wrap gap-4 text-sm text-indigo-700">
            <span>版本：<strong>{agentInfo.version}</strong></span>
            <span>默认轮次：<strong>{agentInfo.default_max_rounds}</strong></span>
            <span>单轮超时：<strong>{agentInfo.default_round_timeout_sec}s</strong></span>
            <span>总超时：<strong>{agentInfo.default_total_timeout_sec}s</strong></span>
            <span>ClawdBot：<strong className="font-mono text-xs">{agentInfo.clawdbot_base_url}</strong></span>
            <span>Mock 模式：<strong>{agentInfo.mock_mode_available ? '可用' : '不可用'}</strong></span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 左侧：参数配置 */}
        <div className="lg:col-span-1 space-y-4">
          <div className="card p-4 space-y-4">
            <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1.5">
              <Zap size={14} className="text-indigo-500" />
              执行参数
            </h2>

            {/* 目标输入 */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">自动化目标 *</label>
              <textarea
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-300 resize-none"
                rows={3}
                placeholder="例如：填写并提交登录表单"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                disabled={running}
              />
            </div>

            {/* 最大轮次 */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                最大轮次：<strong>{maxRounds}</strong>
              </label>
              <input
                type="range" min={1} max={20} step={1}
                value={maxRounds}
                onChange={(e) => setMaxRounds(Number(e.target.value))}
                disabled={running}
                className="w-full accent-indigo-500"
              />
            </div>

            {/* 单轮超时 */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                单轮超时：<strong>{roundTimeout}s</strong>
              </label>
              <input
                type="range" min={5} max={120} step={5}
                value={roundTimeout}
                onChange={(e) => setRoundTimeout(Number(e.target.value))}
                disabled={running}
                className="w-full accent-indigo-500"
              />
            </div>

            {/* 总超时 */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                总超时：<strong>{totalTimeout}s</strong>
              </label>
              <input
                type="range" min={10} max={600} step={10}
                value={totalTimeout}
                onChange={(e) => setTotalTimeout(Number(e.target.value))}
                disabled={running}
                className="w-full accent-indigo-500"
              />
            </div>

            {/* 开关选项 */}
            <div className="space-y-2">
              {[
                { label: 'Mock 模式（无需真实 ClawdBot）', value: useMock, set: setUseMock },
                { label: 'Edge RPC 模式', value: useEdgeRpc, set: setUseEdgeRpc },
                { label: '授权截图上传（高敏隐私）', value: imageConsent, set: setImageConsent },
              ].map(({ label, value, set }) => (
                <label key={label} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={value}
                    onChange={(e) => set(e.target.checked)}
                    disabled={running}
                    className="accent-indigo-500"
                  />
                  <span className="text-xs text-gray-600">{label}</span>
                </label>
              ))}
            </div>

            {/* 执行按钮 */}
            <div className="flex gap-2">
              <button
                className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                onClick={handleRun}
                disabled={running || !goal.trim()}
              >
                {running
                  ? <><RefreshCw size={14} className="animate-spin" />执行中…</>
                  : <><Play size={14} />启动</>
                }
              </button>
              {running && (
                <button
                  className="px-3 py-2 rounded border border-red-300 text-red-600 text-sm hover:bg-red-50 transition-colors"
                  onClick={handleStop}
                >
                  <Square size={14} />
                </button>
              )}
            </div>
          </div>

          {/* 执行结果摘要 */}
          {loopResult && (
            <div className={`card p-4 border ${loopResult.goal_achieved ? 'border-green-300 bg-green-50' : 'border-red-300 bg-red-50'}`}>
              <div className="flex items-center gap-2 mb-2">
                {loopResult.goal_achieved
                  ? <CheckCircle size={16} className="text-green-600" />
                  : <XCircle size={16} className="text-red-500" />
                }
                <span className={`text-sm font-semibold ${loopResult.goal_achieved ? 'text-green-700' : 'text-red-600'}`}>
                  {loopResult.goal_achieved ? '目标达成' : '目标未达成'}
                </span>
              </div>
              <div className="text-xs text-gray-600 space-y-1">
                <div>总轮次：<strong>{loopResult.total_rounds ?? '—'}</strong></div>
                <div>状态：<strong className="font-mono">{loopResult.final_status ?? '—'}</strong></div>
                {loopResult.total_latency_ms != null && (
                  <div>耗时：<strong>{loopResult.total_latency_ms}ms</strong></div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* 右侧：SSE 事件流 */}
        <div className="lg:col-span-2">
          {/* TODO-50: UIAssertion 统计侧边栏 */}
          {uiAssertionData && (uiAssertionData.total_assertions > 0) && (
            <div className="card p-3 mb-3 border border-red-100 bg-red-50">
              <div className="flex items-center gap-1.5 mb-2">
                <ShieldAlert size={13} className="text-red-500" />
                <span className="text-xs font-semibold text-red-700">UI 断言统计（近 1h）</span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <div className="text-base font-bold text-red-600">{uiAssertionData.sensitive_blocked}</div>
                  <div className="text-xs text-gray-500">敏感拦截</div>
                </div>
                <div>
                  <div className="text-base font-bold text-orange-500">{uiAssertionData.precondition_failed}</div>
                  <div className="text-xs text-gray-500">前置失败</div>
                </div>
                <div>
                  <div className="text-base font-bold text-yellow-600">{uiAssertionData.postcondition_failed}</div>
                  <div className="text-xs text-gray-500">后置失败</div>
                </div>
              </div>
            </div>
          )}
          <div className="card overflow-hidden">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
              <span className="text-xs font-medium text-gray-500 uppercase tracking-wide flex items-center gap-1.5">
                <Clock size={12} />
                实时事件流
              </span>
              {events.length > 0 && (
                <span className="text-xs text-gray-400">{events.length} 条事件</span>
              )}
            </div>
            <div className="h-[520px] overflow-y-auto p-3 space-y-2 bg-gray-900 font-mono text-xs">
              {events.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-gray-500">
                  <Bot size={32} className="mb-3 text-gray-600" />
                  <span>等待启动 Agent Loop…</span>
                </div>
              )}
              {events.map((ev, i) => (
                <div key={i} className="flex gap-2 items-start">
                  <span className="text-gray-500 shrink-0 w-20">{formatTime(ev._ts)}</span>
                  <span className="shrink-0">{phaseBadge(ev.phase)}</span>
                  <span className="text-gray-300 break-all">
                    {ev.phase === 'ROUND_START' && `第 ${ev.round} 轮`}
                    {ev.phase === 'SCREENSHOT_DONE' && `截图完成 (${ev.width ?? '?'}×${ev.height ?? '?'})`}
                    {ev.phase === 'PLAN_GENERATED' && `计划：${(ev.actions ?? []).length} 个指令`}
                    {ev.phase === 'EXECUTE_DONE' && `执行：${ev.success ? '✓ 成功' : '✗ 失败'} ${ev.action_type ?? ''}`}
                    {ev.phase === 'ROUND_DONE' && `轮次 ${ev.round} 完成 (${ev.latency_ms ?? '?'}ms)`}
                    {ev.phase === 'LOOP_DONE' && (ev.goal_achieved ? '✓ 目标达成' : '✗ 目标未达成')}
                    {ev.phase === 'AGENT_INIT' && `目标：${ev.goal ?? ''} | 模式：${ev.client_mode ?? ''}`}
                    {ev.phase === 'LOOP_START' && `Agent Loop 启动 max_rounds=${ev.max_rounds ?? '?'}`}
                    {ev.error && <span className="text-red-400"> 错误：{ev.error}</span>}
                  </span>
                </div>
              ))}
              <div ref={eventsEndRef} />
            </div>
          </div>
        </div>
      </div>

      {/* TODO-86: Agent Loop 历史执行记录面板 */}
      {loopHistory.length > 0 && (
        <div className="card overflow-hidden">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 flex items-center gap-2">
            <History size={13} className="text-gray-500" />
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">历史执行记录（近 24h，最近 10 次）</span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['目标', '状态', '达成', '轮次', '耗时', '模式', '时间'].map(h => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loopHistory.map((item, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-xs text-gray-700 max-w-48 truncate" title={item.goal}>
                    {item.goal || '—'}
                  </td>
                  <td className="px-4 py-2 text-xs font-mono text-gray-500">{item.final_status || '—'}</td>
                  <td className="px-4 py-2 text-xs">
                    {item.goal_achieved === true
                      ? <CheckCircle size={13} className="text-green-500" />
                      : item.goal_achieved === false
                        ? <XCircle size={13} className="text-red-400" />
                        : <span className="text-gray-300">—</span>
                    }
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-500">{item.total_rounds ?? '—'}</td>
                  <td className="px-4 py-2 text-xs text-gray-500">
                    {item.total_latency_ms != null ? `${item.total_latency_ms}ms` : '—'}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-400">{item.client_mode || '—'}</td>
                  <td className="px-4 py-2 text-xs text-gray-400 whitespace-nowrap">
                    {item.ts_ms
                      ? new Date(item.ts_ms).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
                      : '—'
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

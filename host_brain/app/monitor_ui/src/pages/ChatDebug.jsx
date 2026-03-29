import React, { useState, useRef, useCallback, useEffect } from 'react'
import { Send, RefreshCw, Bot, User, Zap, Shield, GitBranch, Clock, AlertTriangle, CheckCircle, Loader2 } from 'lucide-react'
import useAppStore from '../store/useAppStore.js'

// SSE 事件解析
function parseSSE(text) {
  return text.split('\n\n').filter(Boolean).map(chunk => {
    const match = chunk.match(/^data:\s*(.+)$/m)
    if (match) {
      try { return JSON.parse(match[1]) } catch { return null }
    }
    return null
  }).filter(Boolean)
}

function PhaseBadge({ phase }) {
  const styles = {
    PRESENCE: 'bg-blue-100 text-blue-700',
    COMMIT:   'bg-green-100 text-green-700',
    REJECT:   'bg-red-100 text-red-700',
    CHUNK:    'bg-purple-100 text-purple-700',
    NODE:     'bg-orange-100 text-orange-700',
  }
  return <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${styles[phase] || 'bg-gray-100 text-gray-600'}`}>{phase}</span>
}

function EventCard({ event, index }) {
  const ts = event.ts_ms ? new Date(event.ts_ms).toLocaleTimeString('zh-CN') : ''
  return (
    <div className={`flex gap-2 text-xs py-1.5 px-2 rounded ${index % 2 === 0 ? 'bg-gray-50' : ''}`}>
      <span className="text-gray-400 shrink-0 w-16">{ts || `#${index + 1}`}</span>
      <PhaseBadge phase={event.phase || event.event || '?'} />
      <span className="text-gray-600 truncate flex-1">
        {event.event && <span className="font-medium">{event.event} </span>}
        {event.scene_type && <span className="text-blue-600">scene={event.scene_type} </span>}
        {event.decision && <span className={event.decision === 'REJECT' ? 'text-red-600 font-bold' : 'text-green-600'}>{event.decision} </span>}
        {event.reason_code && <span className="text-orange-500">({event.reason_code})</span>}
        {event.content && <span className="text-gray-500">{event.content.slice(0, 80)}</span>}
        {event.token && <span className="text-purple-500">{event.token}</span>}
        {event.node_id && <span className="text-indigo-500">node={event.node_id}</span>}
      </span>
    </div>
  )
}

export default function ChatDebug() {
  const setSelectedRequestId = useAppStore((s) => s.setSelectedRequestId)
  const [message, setMessage] = useState('')
  const [chatHistory, setChatHistory] = useState([])
  const [sseEvents, setSseEvents] = useState([])
  const [streaming, setStreaming] = useState(false)
  const [currentResponse, setCurrentResponse] = useState('')
  const [requestId, setRequestId] = useState(null)
  const [stats, setStats] = useState(null)
  const chatEndRef = useRef(null)
  const eventsEndRef = useRef(null)
  const abortRef = useRef(null)

  // 自动滚动
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [chatHistory, currentResponse])
  useEffect(() => { eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [sseEvents])

  const sendMessage = useCallback(async () => {
    if (!message.trim() || streaming) return
    const userMsg = message.trim()
    setMessage('')
    setChatHistory(prev => [...prev, { role: 'user', content: userMsg }])
    setSseEvents([])
    setCurrentResponse('')
    setRequestId(null)
    setStreaming(true)
    setStats(null)

    const controller = new AbortController()
    abortRef.current = controller
    const startTime = Date.now()

    try {
      const res = await fetch('/v1/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg, session_id: `debug-${Date.now()}` }),
        signal: controller.signal,
      })

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let fullText = ''
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value, { stream: true })
        accumulated += chunk

        // 解析 SSE 事件
        const events = parseSSE(accumulated)
        accumulated = '' // 简化处理

        for (const ev of events) {
          setSseEvents(prev => [...prev, { ...ev, ts_ms: Date.now() }])

          // 提取 request_id
          if (ev.request_id) {
            setRequestId(ev.request_id)
            setSelectedRequestId(ev.request_id)
          }

          // 提取文本内容
          if (ev.token) fullText += ev.token
          if (ev.content) fullText = ev.content
          if (ev.text) fullText = ev.text
          if (ev.rendered_ja) fullText = ev.rendered_ja
          if (ev.narrative_zh) fullText = ev.narrative_zh

          setCurrentResponse(fullText)
        }
      }

      const elapsed = Date.now() - startTime
      setStats({ elapsed, eventCount: sseEvents.length })
      setChatHistory(prev => [...prev, { role: 'assistant', content: fullText || '(无文本输出)' }])
      setCurrentResponse('')

    } catch (err) {
      if (err.name !== 'AbortError') {
        setChatHistory(prev => [...prev, { role: 'error', content: `请求失败: ${err.message}` }])
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [message, streaming, setSelectedRequestId])

  const stopStream = () => { abortRef.current?.abort() }

  return (
    <div className="flex gap-4 h-[calc(100vh-7rem)]">
      {/* 左侧：聊天区 */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">聊天调试</h1>
            <p className="text-xs text-gray-500">直接对话 · SSE 事件实时展示 · 链路追踪</p>
          </div>
          {stats && (
            <div className="flex items-center gap-3 text-xs text-gray-500">
              <span><Clock size={11} className="inline" /> {stats.elapsed}ms</span>
              <span><Zap size={11} className="inline" /> {sseEvents.length} 事件</span>
            </div>
          )}
        </div>

        {/* 消息列表 */}
        <div className="flex-1 overflow-y-auto card p-4 space-y-3 mb-3">
          {chatHistory.length === 0 && (
            <div className="text-center text-gray-400 py-12">
              <Bot size={32} className="mx-auto mb-2 text-gray-300" />
              <p>发送一条消息开始调试</p>
              <p className="text-xs mt-1">SSE 事件流会在右侧实时展示</p>
            </div>
          )}
          {chatHistory.map((msg, i) => (
            <div key={i} className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : ''}`}>
              {msg.role !== 'user' && (
                <div className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 ${msg.role === 'error' ? 'bg-red-100' : 'bg-primary-100'}`}>
                  {msg.role === 'error' ? <AlertTriangle size={14} className="text-red-500" /> : <Bot size={14} className="text-primary" />}
                </div>
              )}
              <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user' ? 'bg-primary text-white' :
                msg.role === 'error' ? 'bg-red-50 text-red-700 border border-red-200' :
                'bg-gray-100 text-gray-800'
              }`}>
                <pre className="whitespace-pre-wrap font-sans">{msg.content}</pre>
              </div>
              {msg.role === 'user' && (
                <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center shrink-0">
                  <User size={14} className="text-gray-600" />
                </div>
              )}
            </div>
          ))}
          {currentResponse && (
            <div className="flex gap-2">
              <div className="w-7 h-7 rounded-full bg-primary-100 flex items-center justify-center shrink-0">
                <Loader2 size={14} className="text-primary animate-spin" />
              </div>
              <div className="bg-gray-100 rounded-lg px-3 py-2 text-sm text-gray-800">
                <pre className="whitespace-pre-wrap font-sans">{currentResponse}<span className="animate-pulse">▊</span></pre>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* 输入框 */}
        <div className="flex gap-2">
          <input
            className="flex-1 px-3 py-2.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-200"
            placeholder="输入消息… (Enter 发送)"
            value={message}
            onChange={e => setMessage(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendMessage()}
            disabled={streaming}
          />
          {streaming ? (
            <button className="btn-secondary px-4" onClick={stopStream}>
              <AlertTriangle size={14} /> 停止
            </button>
          ) : (
            <button className="btn-primary px-4" onClick={sendMessage} disabled={!message.trim()}>
              <Send size={14} /> 发送
            </button>
          )}
        </div>
      </div>

      {/* 右侧：SSE 事件流 */}
      <div className="w-96 shrink-0 flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-700">
            <GitBranch size={14} className="inline mr-1" />SSE 事件流
          </h2>
          <span className="text-xs text-gray-400">{sseEvents.length} 事件</span>
        </div>
        <div className="flex-1 overflow-y-auto card p-2 space-y-0.5">
          {sseEvents.length === 0 && (
            <div className="text-center text-gray-400 py-8 text-xs">等待聊天请求…</div>
          )}
          {sseEvents.map((ev, i) => <EventCard key={i} event={ev} index={i} />)}
          <div ref={eventsEndRef} />
        </div>

        {/* 请求 ID 快链 */}
        {requestId && (
          <div className="mt-2 text-xs text-gray-500 flex items-center gap-1">
            <CheckCircle size={11} className="text-green-500" />
            <span>request_id: </span>
            <a href={`#/trace/${requestId}`} className="text-primary hover:underline font-mono">{requestId.slice(0, 20)}…</a>
          </div>
        )}
      </div>
    </div>
  )
}

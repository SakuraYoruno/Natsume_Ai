import React, { useCallback, useEffect, useState } from 'react'
import { RefreshCw, ShieldCheck } from 'lucide-react'
import {
  bootstrapLocalOperatorSession,
  operatorSessionErrorMessage,
} from './operatorSessionApi.js'

export default function OperatorSessionGate({ children }) {
  const [state, setState] = useState({ status: 'loading', error: null })

  const connect = useCallback(async () => {
    setState({ status: 'loading', error: null })
    try {
      await bootstrapLocalOperatorSession()
      setState({ status: 'ready', error: null })
    } catch (error) {
      setState({ status: 'error', error })
    }
  }, [])

  useEffect(() => {
    void connect()
  }, [connect])

  if (state.status === 'ready') return children

  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-white to-cyan-50 px-5 py-10">
      <section
        className="w-full max-w-lg rounded-3xl border border-white/80 bg-white/90 p-7 shadow-xl shadow-slate-200/60 backdrop-blur"
        aria-live="polite"
      >
        <div className="flex items-start gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary-50 text-primary">
            <ShieldCheck size={22} />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-slate-900">
              {state.status === 'loading' ? '正在连接本机控制台' : '本机控制台尚未连接'}
            </h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {state.status === 'loading'
                ? '正在建立仅供本机浏览器使用的会话，数据准备好后会自动进入控制台。'
                : operatorSessionErrorMessage(state.error)}
            </p>
          </div>
        </div>
        {state.status === 'error' ? (
          <button
            type="button"
            className="mt-6 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-600"
            onClick={() => void connect()}
          >
            <RefreshCw size={15} />
            重新连接
          </button>
        ) : (
          <div className="mt-6 h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div className="h-full w-2/3 animate-pulse rounded-full bg-primary-400" />
          </div>
        )}
      </section>
    </main>
  )
}

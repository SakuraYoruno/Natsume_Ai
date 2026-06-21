import React from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { isChunkLoadError, recoverFromChunkError } from '../../utils/chunkRecovery.js'

/**
 * 全局错误边界 — 捕获子组件渲染异常，显示友好错误页 + 重试按钮。
 * React 的 ErrorBoundary 必须是 class 组件。
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null, errorInfo: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo })
    console.error('[ErrorBoundary] Uncaught error:', error, errorInfo)

    if (isChunkLoadError(error)) {
      recoverFromChunkError()
    }
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 p-8">
          <div className="max-w-lg w-full text-center space-y-6">
            <div className="flex justify-center">
              <span className="flex items-center justify-center w-16 h-16 rounded-2xl bg-red-100 text-red-500">
                <AlertTriangle size={32} />
              </span>
            </div>
            <div>
              <h1 className="text-xl font-semibold text-gray-900">页面渲染出错</h1>
              <p className="mt-2 text-sm text-gray-500">
                控制台遇到了一个意外错误。你可以尝试重新加载当前页面。
              </p>
            </div>
            {this.state.error && (
              <pre className="text-left rounded-lg border border-red-200 bg-red-50 p-4 text-xs text-red-700 overflow-auto max-h-40">
                {String(this.state.error)}
              </pre>
            )}
            <div className="flex items-center justify-center gap-3">
              <button
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary-600 transition-colors"
                onClick={this.handleReset}
              >
                <RefreshCw size={14} />
                重试当前页面
              </button>
              <button
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 bg-white text-gray-700 text-sm font-medium hover:bg-gray-50 transition-colors"
                onClick={() => window.location.reload()}
              >
                刷新整个应用
              </button>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

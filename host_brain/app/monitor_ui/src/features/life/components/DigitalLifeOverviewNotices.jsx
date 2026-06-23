import React from 'react'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'

function RuntimeErrorBanner({ runtimeError }) {
  if (!runtimeError) return null
  const runtimeErrorText = runtimeError instanceof Error ? runtimeError.message : String(runtimeError)
  const unavailable = /404|Not Found/i.test(runtimeErrorText)

  return (
    <NoticeBanner
      variant="info"
      title={unavailable ? '运行快照暂不可用' : '运行快照加载异常'}
    >
      {unavailable
        ? '当前环境没有接到运行快照，但页面其余部分仍可正常浏览。'
        : runtimeErrorText}
    </NoticeBanner>
  )
}

export default function DigitalLifeOverviewNotices({ error, refreshWarning, runtimeError }) {
  return (
    <>
      {error ? <NoticeBanner variant="error" title="总览统计加载失败">{error.message}</NoticeBanner> : null}
      {refreshWarning ? (
        <NoticeBanner variant="warning" title="总览刷新失败，当前展示旧快照">
          {refreshWarning.message}
        </NoticeBanner>
      ) : null}
      <RuntimeErrorBanner runtimeError={runtimeError} />
    </>
  )
}

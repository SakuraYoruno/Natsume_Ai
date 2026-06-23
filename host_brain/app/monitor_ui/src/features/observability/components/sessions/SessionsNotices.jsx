import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'

export default function SessionsNotices({ listError, suppressedCount, showNoSampleNotice }) {
  return (
    <>
      {listError ? (
        <NoticeBanner variant="error" title="会话流加载失败">
          {listError}
        </NoticeBanner>
      ) : null}

      {!listError && suppressedCount > 0 ? (
        <NoticeBanner variant="info" title="已隐藏噪声记录">
          当前页已自动隐藏 {suppressedCount} 条测试会话、纯问号空态消息或不可读摘要记录。
        </NoticeBanner>
      ) : null}

      {showNoSampleNotice ? (
        <NoticeBanner variant="info" title="当前窗口暂无会话沉积">
          当前筛选条件下还没有可展示的请求样本。
        </NoticeBanner>
      ) : null}
    </>
  )
}

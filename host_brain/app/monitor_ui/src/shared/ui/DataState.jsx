import React from 'react'
import EmptyStateCard from './EmptyStateCard.jsx'
import { LoadingNotice } from './LoadingNotice.jsx'
import NoticeBanner from './NoticeBanner.jsx'

export default function DataState({ loading, error, empty, emptyTitle = '暂无数据', emptyHint = '当前接口没有返回样本。', children }) {
  if (loading) {
    return <LoadingNotice>正在加载数据…</LoadingNotice>
  }
  if (error) {
    return (
      <NoticeBanner variant="warning" title="接口降级">
        {String(error.message || error)}
      </NoticeBanner>
    )
  }
  if (empty) {
    return <EmptyStateCard title={emptyTitle} hint={emptyHint} />
  }
  return children
}

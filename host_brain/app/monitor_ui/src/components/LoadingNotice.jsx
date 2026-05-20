import React from 'react'
import NoticeBanner from './NoticeBanner.jsx'

export function LoadingNotice({
  children = '正在同步最新观测数据…',
  title = '同步中',
  className = '',
}) {
  return (
    <NoticeBanner variant="info" title={title} className={className}>
      {children}
    </NoticeBanner>
  )
}

export function LazyLoadingNotice({
  children = '该分区会在进入视口后自动加载。',
  title = '延迟加载',
  className = '',
}) {
  return (
    <NoticeBanner variant="info" title={title} className={className}>
      {children}
    </NoticeBanner>
  )
}

export default LoadingNotice

import React from 'react'
import { Wifi } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import SectionHeading from '../../../../shared/ui/SectionHeading.jsx'
import GatewayPlatformCard from './GatewayPlatformCard.jsx'

export default function GatewayPlatformsSection({ platforms }) {
  return (
    <div>
      <SectionHeading icon={Wifi} title="平台状态" />
      {platforms.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {platforms.map((item) => (
            <GatewayPlatformCard key={item.platform} data={item} />
          ))}
        </div>
      ) : (
        <EmptyStateCard title="暂无平台样本" hint="当前窗口还没有观测到网关平台数据，待第一批消息到达后会自动刷新展示。" className="p-6" />
      )}
    </div>
  )
}

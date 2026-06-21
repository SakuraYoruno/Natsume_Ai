import React from 'react'
import { Sparkles } from 'lucide-react'
import PageHero from '../../../shared/ui/PageHero.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'

export default function DigitalLifeHeroSection({
  modeMeta,
  lifeBodyModel,
  heroDescription,
  heroMetaItems,
  overviewLoadState,
  error,
  refreshWarning,
  windowMinutes,
  overviewTrackedSections,
  overviewUpdatedAt,
}) {
  return (
    <PageHero
      icon={Sparkles}
      eyebrow="数字生命驾驶舱"
      title={`${modeMeta.emoji} ${lifeBodyModel.lifeState.label}`}
      description={heroDescription}
      gradientClass="from-primary-50 via-white to-pink-50"
      showPulseRail={false}
      metaItems={heroMetaItems}
      aside={(
        <SampleMeta
          className="max-w-md"
          compact
          loadState={overviewLoadState}
          loadStateDetail={error || refreshWarning}
          windowMinutes={windowMinutes}
          sampleSize={overviewTrackedSections}
          sampleLabel="关键分区"
          updatedAt={overviewUpdatedAt}
          source="/obs/life-body aggregate"
          definition="驾驶舱现在优先走 obs sidecar 的生命体聚合接口，再在前端补主体化视图层；关键分区数量表示当前窗口中已成功返回数据的核心观测分区数。"
        />
      )}
    />
  )
}

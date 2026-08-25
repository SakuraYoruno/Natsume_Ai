import RenderValue from '../../../../shared/ui/RenderValue.jsx'

export function renderImmuneStageTags(stage, keyPrefix) {
  if (stage.groupId !== 'immune') return null
  const decision = String(stage.immuneDecision || '').trim().toUpperCase()
  const riskLevel = String(stage.immuneRiskLevel || '').trim().toLowerCase()
  const reasonTags = Array.isArray(stage.immuneReasonTags) ? stage.immuneReasonTags : []
  if (!decision && !riskLevel && reasonTags.length === 0) return null

  return (
    <>
      {(decision || riskLevel) ? (
        <span
          key={`${keyPrefix}-immune-meta`}
          className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-1 text-[11px] font-medium text-orange-700"
        >
          {decision || 'IMMUNE'}
          {riskLevel ? `/${riskLevel}` : ''}
        </span>
      ) : null}
      {reasonTags.slice(0, 2).map((code, index) => (
        <span
          key={`${keyPrefix}-immune-reason-${index}`}
          className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700"
        >
          <RenderValue value={code} />
        </span>
      ))}
    </>
  )
}

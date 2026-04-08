import React from 'react'
import SectionHeading from '../SectionHeading.jsx'

export default function InsightSection({
  icon,
  title,
  color = 'text-gray-700',
  children,
  className = '',
}) {
  return (
    <section className={className}>
      <SectionHeading icon={icon} title={title} tone={color} />
      {children}
    </section>
  )
}

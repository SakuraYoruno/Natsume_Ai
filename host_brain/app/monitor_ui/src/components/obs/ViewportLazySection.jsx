import React, { useEffect, useRef, useState } from 'react'

export default function ViewportLazySection({
  children,
  placeholder = null,
  rootMargin = '320px 0px',
  className = '',
}) {
  const anchorRef = useRef(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (visible || typeof IntersectionObserver === 'undefined') {
      if (!visible && typeof IntersectionObserver === 'undefined') {
        setVisible(true)
      }
      return undefined
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
        }
      },
      { rootMargin },
    )

    const current = anchorRef.current
    if (current) {
      observer.observe(current)
    }

    return () => observer.disconnect()
  }, [rootMargin, visible])

  return (
    <div ref={anchorRef} className={className}>
      {visible ? children : placeholder}
    </div>
  )
}

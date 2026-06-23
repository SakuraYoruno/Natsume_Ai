export default function FlowSection({ title, hint, children }) {
  return (
    <section className="card relative overflow-hidden p-4">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_36%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_32%)]" />
      <div className="relative">
        <div className="mb-2 flex items-center gap-2">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        </div>
        {hint ? <p className="mb-3 text-xs text-gray-500">{hint}</p> : null}
        <div className="lifeline-rail mb-4 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>
        {children}
      </div>
    </section>
  )
}

import { Suspense } from 'react'
import { HashRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import ErrorBoundary from './shared/ui/ErrorBoundary.jsx'
import Layout from './app/shell/Layout.jsx'
import OperatorSessionGate from './app/session/OperatorSessionGate.jsx'
import { legacyRedirects, routeManifest } from './app/routeManifest.jsx'

function RouteLoading() {
  return (
    <div className="card relative overflow-hidden p-6">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,_rgba(41,112,255,0.08),_transparent_40%),radial-gradient(circle_at_bottom_left,_rgba(16,185,129,0.07),_transparent_38%)]" />
      <div className="relative space-y-4">
        <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-semibold text-primary shadow-sm">
          <span className="lifeline-node" />
          数字生命体唤醒中
        </div>

        <div className="lifeline-rail h-8 px-3">
          <div className="lifeline-wave absolute inset-0 opacity-70" />
          <div className="relative flex h-full items-center justify-between">
            {[0, 1, 2, 3, 4].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.28}s` }} />
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <div key={index} className="rounded-2xl border border-white/80 bg-white/75 p-4 shadow-sm backdrop-blur-sm">
              <div className="h-2.5 w-24 rounded-full bg-primary-100/80" />
              <div className="mt-3 h-5 w-2/3 rounded-full bg-gray-200/80" />
              <div className="mt-2 h-2.5 w-full rounded-full bg-gray-100" />
              <div className="mt-1.5 h-2.5 w-4/5 rounded-full bg-gray-100" />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function withSuspense(element) {
  return <Suspense fallback={<RouteLoading />}>{element}</Suspense>
}

function routePath(path) {
  return String(path || '').replace(/^\//, '')
}

function LegacyRedirect({ to }) {
  const params = useParams()
  const target = Object.entries(params).reduce(
    (path, [key, value]) => path.replace(`:${key}`, encodeURIComponent(value || '')),
    to,
  )
  return <Navigate to={target} replace />
}

export default function App() {
  return (
    <ErrorBoundary>
      <OperatorSessionGate>
        <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<Navigate to="/cockpit" replace />} />
              {routeManifest.map((route) => (
                <Route key={route.path} path={routePath(route.path)} element={withSuspense(route.element)} />
              ))}
              {legacyRedirects.map((redirect) => (
                <Route
                  key={`${redirect.from}->${redirect.to}`}
                  path={routePath(redirect.from)}
                  element={<LegacyRedirect to={redirect.to} />}
                />
              ))}
              <Route path="*" element={<Navigate to="/cockpit" replace />} />
            </Route>
          </Routes>
          <Toaster position="top-right" toastOptions={{ duration: 4000, style: { fontSize: '13px' } }} />
        </HashRouter>
      </OperatorSessionGate>
    </ErrorBoundary>
  )
}

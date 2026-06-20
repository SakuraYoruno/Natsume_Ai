import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import path from 'node:path'

const MANIFEST_PATH = path.resolve(process.cwd(), 'src/app/routeManifest.jsx')
const ROUTE_RE = /\{\s*path:\s*'(?<path>[^']+)'.*?legacyPaths:\s*\[(?<legacy>[^\]]*)\].*?label:\s*'(?<label>[^']+)'.*?smokeTest:\s*(?<smokeTest>true|false)/gs
const STRING_RE = /'([^']*)'/g
const FATAL_TEXT_NEEDLES = [
  'Failed to fetch dynamically imported module',
  'Cannot read properties of undefined',
  'Cannot read properties of null',
  'ReferenceError:',
  'SyntaxError:',
  '[vite] Internal server error',
]

function parseManifestRoutes() {
  const source = readFileSync(MANIFEST_PATH, 'utf-8')
  return Array.from(source.matchAll(ROUTE_RE), (match) => ({
    path: match.groups.path,
    label: match.groups.label,
    legacyPaths: Array.from(match.groups.legacy.matchAll(STRING_RE), (legacy) => legacy[1]),
    smokeTest: match.groups.smokeTest === 'true',
  }))
}

function concreteRoutePath(routePath) {
  return routePath
    .replace(/\/\*$/, '')
    .replace(/:runId/g, 'smoke-run')
    .replace(/:pathologyId/g, 'smoke-pathology')
}

function routeUrl(baseURL, routePath) {
  return new URL(`#${concreteRoutePath(routePath)}`, baseURL).toString()
}

function isSimpleLegacyPath(legacyPath) {
  return !legacyPath.includes(':') && !legacyPath.includes('*')
}

async function assertRouteShell(page, route) {
  const root = page.locator('#root')
  await expect(root).toBeVisible()

  const bodyText = await root.innerText({ timeout: 5_000 })
  expect.soft(bodyText.trim().length, `${route.path} rendered an empty shell`).toBeGreaterThan(40)
  expect.soft(bodyText, `${route.path} should expose its nav label`).toContain(route.label)
  for (const needle of FATAL_TEXT_NEEDLES) {
    expect.soft(bodyText, `${route.path} rendered fatal text: ${needle}`).not.toContain(needle)
  }
}

const manifestRoutes = parseManifestRoutes()
const smokeRoutes = manifestRoutes.filter((route) => route.smokeTest)
const simpleLegacyRedirects = smokeRoutes.flatMap((route) =>
  route.legacyPaths
    .filter(isSimpleLegacyPath)
    .map((legacyPath) => ({
      from: legacyPath,
      to: route.path,
      label: route.label,
    })),
)

test.describe('Monitor route smoke', () => {
  test.describe.configure({ mode: 'serial' })

  test('route manifest exposes smoke routes', () => {
    expect(smokeRoutes.length).toBeGreaterThan(20)
  })

  for (const route of smokeRoutes) {
    test(`${route.path} opens without a fatal React error`, async ({ page, baseURL }) => {
      const pageErrors = []
      page.on('pageerror', (error) => pageErrors.push(error.message))

      await page.goto(routeUrl(baseURL, route.path), { waitUntil: 'domcontentloaded' })
      await page.waitForTimeout(800)

      await assertRouteShell(page, route)
      expect(pageErrors, `${route.path} page errors`).toEqual([])
    })
  }

  for (const redirect of simpleLegacyRedirects) {
    test(`${redirect.from} redirects to ${redirect.to}`, async ({ page, baseURL }) => {
      await page.goto(routeUrl(baseURL, redirect.from), { waitUntil: 'domcontentloaded' })
      await expect
        .poll(async () => new URL(page.url()).hash)
        .toBe(`#${concreteRoutePath(redirect.to)}`)
    })
  }
})

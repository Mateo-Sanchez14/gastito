import {
  SESSION_COOKIE_NAME,
  checkBasicAuthHeader,
  createSessionToken,
  getGateCredentials,
  isSecureRequest,
  loginPath,
  redirectToPath,
  sessionCookieOptions,
  shouldRefreshSession,
  verifySessionToken,
} from '@/lib/web-auth'
import { NextRequest, NextResponse } from 'next/server'

/**
 * gastito: a simple shared-credential gate over the whole web UI.
 *
 * A friend-group lock — one username/password that everyone shares, set via
 * WEB_BASIC_AUTH_USER / WEB_BASIC_AUTH_PASS in the container environment. If
 * either is unset (e.g. local dev) the gate is disabled, so nothing changes
 * locally and the app stays open until you opt in.
 *
 * How you get in: a login form at `/login` that hands out a signed, HttpOnly
 * session cookie good for a year (see `lib/web-auth.ts`), renewed on use. This
 * replaced plain HTTP Basic Auth, which browsers only remember for the current
 * browsing session — on a phone that meant retyping the password constantly,
 * and in the installed PWA the native prompt is worse still.
 *
 * A valid `Authorization: Basic` header is still accepted, so browsers that
 * already saved the credentials (and `curl -u`, scripts) keep working; browsers
 * get upgraded to a cookie on their next request. The bot ingestion API
 * (`/api/bot/*`, which has its own bearer auth) and health checks are excluded
 * via the matcher below so the bot and uptime probes are never challenged.
 *
 * NOTE: do NOT add WEB_BASIC_AUTH_* to scripts/build.env. Keeping them absent
 * at build time leaves them as live runtime env lookups (read from the running
 * container) instead of being inlined as build-time literals.
 */

/** Reachable without a session: the login flow itself. */
const PUBLIC_PATHS = [/^\/login\/?$/, /^\/api\/auth\//]

/**
 * Public files (logos, PWA icons, manifest, robots). The login page renders the
 * shared layout, so its images have to load before you are logged in.
 */
const PUBLIC_ASSET = /\.(png|jpe?g|svg|ico|webmanifest|txt|xml)$/

export async function middleware(req: NextRequest) {
  const creds = getGateCredentials()

  // Not configured -> no gate (keeps local dev frictionless).
  if (!creds) return NextResponse.next()

  const { pathname } = req.nextUrl
  if (
    PUBLIC_PATHS.some((re) => re.test(pathname)) ||
    PUBLIC_ASSET.test(pathname)
  )
    return NextResponse.next()

  const session = await verifySessionToken(
    req.cookies.get(SESSION_COOKIE_NAME)?.value,
    creds,
  )
  if (session) {
    if (!shouldRefreshSession(session.expiresAt)) return NextResponse.next()
    // Sliding window: keep regular visitors permanently logged in.
    const res = NextResponse.next()
    res.cookies.set(
      SESSION_COOKIE_NAME,
      await createSessionToken(creds),
      sessionCookieOptions(isSecureRequest(req)),
    )
    return res
  }

  // Saved Basic credentials still let you in — and buy you a cookie, so the
  // browser stops needing them.
  if (checkBasicAuthHeader(req.headers.get('authorization'), creds)) {
    const res = NextResponse.next()
    res.cookies.set(
      SESSION_COOKIE_NAME,
      await createSessionToken(creds),
      sessionCookieOptions(isSecureRequest(req)),
    )
    return res
  }

  // API callers (tRPC, uploads) get a plain 401 — no `WWW-Authenticate`, which
  // would make the browser pop its native prompt in the middle of a fetch.
  if (pathname.startsWith('/api/'))
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 })

  // 303 on writes so an expired session doesn't replay a POST at the login page.
  return redirectToPath(
    req,
    loginPath({ next: req.nextUrl.pathname + req.nextUrl.search }),
    req.method === 'GET' || req.method === 'HEAD' ? 307 : 303,
  )
}

export const config = {
  // Gate everything except the bot API (bearer-authed), health checks, and
  // Next internals / static assets.
  matcher: ['/((?!api/bot|api/health|_next/static|_next/image|favicon.ico).*)'],
}

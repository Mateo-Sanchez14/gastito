import { NextRequest, NextResponse } from 'next/server'

/**
 * gastito: a simple shared-credential gate over the whole web UI.
 *
 * A friend-group lock — one username/password that everyone shares, set via
 * WEB_BASIC_AUTH_USER / WEB_BASIC_AUTH_PASS in the container environment. If
 * either is unset (e.g. local dev) the gate is disabled, so nothing changes
 * locally and the app stays open until you opt in.
 *
 * Implemented as HTTP Basic Auth: the browser prompts once and then replays the
 * credentials on every same-origin request, so tRPC calls and server actions
 * just work without any extra wiring. The bot ingestion API (`/api/bot/*`,
 * which has its own bearer auth) and health checks are excluded via the matcher
 * below so the bot and uptime probes are never challenged.
 *
 * NOTE: do NOT add WEB_BASIC_AUTH_* to scripts/build.env. Keeping them absent
 * at build time leaves them as live runtime env lookups (read from the running
 * container) instead of being inlined as build-time literals.
 */
export function middleware(req: NextRequest) {
  const expectedUser = process.env.WEB_BASIC_AUTH_USER
  const expectedPass = process.env.WEB_BASIC_AUTH_PASS

  // Not configured -> no gate (keeps local dev frictionless).
  if (!expectedUser || !expectedPass) return NextResponse.next()

  const auth = req.headers.get('authorization') ?? ''
  if (auth.startsWith('Basic ')) {
    try {
      const decoded = atob(auth.slice('Basic '.length))
      const sep = decoded.indexOf(':')
      const user = decoded.slice(0, sep)
      const pass = decoded.slice(sep + 1)
      if (user === expectedUser && pass === expectedPass) {
        return NextResponse.next()
      }
    } catch {
      // Malformed header -> fall through to the challenge below.
    }
  }

  return new NextResponse('Autenticación requerida', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="gastito", charset="UTF-8"',
    },
  })
}

export const config = {
  // Gate everything except the bot API (bearer-authed), health checks, and
  // Next internals / static assets.
  matcher: ['/((?!api/bot|api/health|_next/static|_next/image|favicon.ico).*)'],
}

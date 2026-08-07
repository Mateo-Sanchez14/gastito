import {
  SESSION_COOKIE_NAME,
  clientKeyFromRequest,
  createSessionToken,
  credentialsMatch,
  getGateCredentials,
  isSecureRequest,
  loginThrottle,
  safeNextPath,
  sessionCookieOptions,
} from '@/lib/web-auth'
import { NextRequest, NextResponse } from 'next/server'

/**
 * gastito: the login form's target (see `app/login/page.tsx`).
 *
 * A plain HTML form post — no client JS, no server action — so password
 * managers behave and the flow survives a flaky mobile connection. On success
 * it sets the year-long session cookie; on failure it bounces back to the form
 * with an error code.
 */
export async function POST(req: NextRequest) {
  const creds = getGateCredentials()
  // Gate disabled: nothing to log into.
  if (!creds) return NextResponse.redirect(new URL('/', req.url), 303)

  const form = await req.formData()
  const user = String(form.get('username') ?? '')
  const pass = String(form.get('password') ?? '')
  const next = safeNextPath(String(form.get('next') ?? ''))

  const back = (error: string) => {
    const url = new URL('/login', req.url)
    url.searchParams.set('error', error)
    if (next !== '/') url.searchParams.set('next', next)
    return NextResponse.redirect(url, 303)
  }

  const clientKey = clientKeyFromRequest(req)
  if (!loginThrottle.allows(clientKey)) return back('throttled')

  if (!credentialsMatch(user, pass, creds)) {
    loginThrottle.recordFailure(clientKey)
    return back('invalid')
  }

  loginThrottle.reset(clientKey)
  const res = NextResponse.redirect(new URL(next, req.url), 303)
  res.cookies.set(
    SESSION_COOKIE_NAME,
    await createSessionToken(creds),
    sessionCookieOptions(isSecureRequest(req)),
  )
  return res
}

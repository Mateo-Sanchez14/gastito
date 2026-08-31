import {
  SESSION_COOKIE_NAME,
  isSecureRequest,
  redirectToPath,
  sessionCookieOptions,
} from '@/lib/web-auth'
import { NextRequest } from 'next/server'

/**
 * gastito: drop the session cookie (the header's "log out" button posts here).
 *
 * Note this cannot clear credentials a browser saved back when the gate used
 * Basic Auth — those are cleared from the browser's own settings.
 */
export async function POST(req: NextRequest) {
  const res = redirectToPath(req, '/login', 303)
  res.cookies.set(SESSION_COOKIE_NAME, '', {
    ...sessionCookieOptions(isSecureRequest(req)),
    maxAge: 0,
  })
  return res
}

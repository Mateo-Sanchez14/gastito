/**
 * gastito: the shared-credential gate for the whole web UI (see `middleware.ts`).
 *
 * One username/password for the entire friend group, set with
 * WEB_BASIC_AUTH_USER / WEB_BASIC_AUTH_PASS. This module holds the pieces the
 * gate is built from: the credential check itself, and the signed cookie that
 * keeps a browser logged in so phones stop asking for the password on every
 * visit (Basic Auth kept re-prompting whenever the browsing session ended).
 *
 * Everything here has to run inside middleware (edge runtime), so it uses Web
 * Crypto and no node builtins.
 */

export const SESSION_COOKIE_NAME = 'gastito_session'

/**
 * A year. Long enough that a phone never asks again in practice — Chrome caps
 * cookie lifetimes at 400 days, so this stays comfortably under the ceiling.
 */
export const SESSION_TTL_SECONDS = 365 * 24 * 60 * 60

/**
 * Re-issue the cookie once a session is this old. With a sliding window, anyone
 * who opens the app at least once a month is never logged out; a phone that is
 * left untouched for a full year is.
 */
const SESSION_REFRESH_AFTER_SECONDS = 30 * 24 * 60 * 60

/** Token format: `g1.<expiry-epoch-seconds>.<hmac>`. */
const TOKEN_PREFIX = 'g1'

export type GateCredentials = { user: string; pass: string }

/**
 * The configured shared login, or `null` when the gate is off (both vars unset,
 * which is the local-dev default and keeps the app open).
 */
export function getGateCredentials(): GateCredentials | null {
  const user = process.env.WEB_BASIC_AUTH_USER
  const pass = process.env.WEB_BASIC_AUTH_PASS
  if (!user || !pass) return null
  return { user, pass }
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000)
}

/** Length-independent comparison, so a wrong password leaks nothing by timing. */
function timingSafeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder()
  const left = encoder.encode(a)
  const right = encoder.encode(b)
  let diff = left.length ^ right.length
  const length = Math.max(left.length, right.length)
  for (let i = 0; i < length; i++) diff |= (left[i] ?? 0) ^ (right[i] ?? 0)
  return diff === 0
}

export function credentialsMatch(
  user: string,
  pass: string,
  creds: GateCredentials,
): boolean {
  // Both comparisons always run: `&&` would short-circuit on a wrong username.
  const userOk = timingSafeEqual(user, creds.user)
  const passOk = timingSafeEqual(pass, creds.pass)
  return userOk && passOk
}

/**
 * Accept an `Authorization: Basic` header. The login form replaced the browser's
 * native prompt, but browsers that already remember the credentials keep sending
 * them — and `curl -u` still works — so we keep honouring the header.
 */
export function checkBasicAuthHeader(
  header: string | null,
  creds: GateCredentials,
): boolean {
  if (!header?.startsWith('Basic ')) return false
  let decoded: string
  try {
    const binary = atob(header.slice('Basic '.length).trim())
    // atob yields latin1; the credentials are UTF-8 (we advertise charset=UTF-8).
    decoded = new TextDecoder().decode(
      Uint8Array.from(binary, (char) => char.charCodeAt(0)),
    )
  } catch {
    return false
  }
  const separator = decoded.indexOf(':')
  if (separator < 0) return false
  return credentialsMatch(
    decoded.slice(0, separator),
    decoded.slice(separator + 1),
    creds,
  )
}

/**
 * The HMAC key is derived from the credentials themselves, so there is no extra
 * secret to deploy — and rotating the shared password automatically invalidates
 * every outstanding session, which is exactly what you want from a rotation.
 */
async function signingKey(creds: GateCredentials): Promise<CryptoKey> {
  const material = new TextEncoder().encode(
    `gastito-session|${TOKEN_PREFIX}|${creds.user}|${creds.pass}`,
  )
  return crypto.subtle.importKey(
    'raw',
    material,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
}

async function sign(payload: string, creds: GateCredentials): Promise<string> {
  const signature = await crypto.subtle.sign(
    'HMAC',
    await signingKey(creds),
    new TextEncoder().encode(payload),
  )
  const bytes = new Uint8Array(signature)
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export async function createSessionToken(
  creds: GateCredentials,
  now: number = nowSeconds(),
): Promise<string> {
  const payload = `${TOKEN_PREFIX}.${now + SESSION_TTL_SECONDS}`
  return `${payload}.${await sign(payload, creds)}`
}

/**
 * Returns the session's expiry when the token is valid and unexpired, else null.
 */
export async function verifySessionToken(
  token: string | undefined,
  creds: GateCredentials,
  now: number = nowSeconds(),
): Promise<{ expiresAt: number } | null> {
  if (!token) return null
  const parts = token.split('.')
  if (parts.length !== 3) return null
  const [prefix, expiry, signature] = parts
  if (prefix !== TOKEN_PREFIX) return null
  const expiresAt = Number(expiry)
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= now) return null
  const expected = await sign(`${prefix}.${expiry}`, creds)
  if (!timingSafeEqual(signature, expected)) return null
  return { expiresAt }
}

/** Whether a still-valid session is old enough to be worth re-issuing. */
export function shouldRefreshSession(
  expiresAt: number,
  now: number = nowSeconds(),
): boolean {
  const age = SESSION_TTL_SECONDS - (expiresAt - now)
  return age >= SESSION_REFRESH_AFTER_SECONDS
}

export function sessionCookieOptions(secure: boolean) {
  return {
    httpOnly: true,
    sameSite: 'lax' as const,
    secure,
    // Explicit: middleware defaults a cookie's path to the current request URL.
    path: '/',
    maxAge: SESSION_TTL_SECONDS,
  }
}

/**
 * `Secure` cookies are dropped over plain http, which would lock out local dev
 * on http://localhost. Behind Caddy the app itself speaks http, so trust the
 * proxy's `x-forwarded-proto` before the request URL.
 */
export function isSecureRequest(req: Request): boolean {
  const forwarded = req.headers.get('x-forwarded-proto')
  if (forwarded) return forwarded.split(',')[0].trim() === 'https'
  return new URL(req.url).protocol === 'https:'
}

/**
 * Sanitize the `?next=` we bounce back to after login: same-origin paths only,
 * so a crafted link can't turn the login form into an open redirect.
 */
export function safeNextPath(value: string | null | undefined): string {
  if (!value) return '/'
  // `//evil.com` and `/\evil.com` are protocol-relative URLs, not local paths.
  if (
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.startsWith('/\\')
  )
    return '/'
  if (value.startsWith('/login')) return '/'
  return value
}

/**
 * Fixed-window throttle for failed logins. A shared password now buys a
 * year-long cookie, so guessing it should not be free — but this is a friend
 * group on a single container, so in-memory state (lost on restart) is enough.
 */
export function createLoginThrottle({
  maxFailures = 10,
  windowSeconds = 15 * 60,
}: { maxFailures?: number; windowSeconds?: number } = {}) {
  const buckets = new Map<string, { failures: number; resetAt: number }>()

  const current = (key: string, now: number) => {
    const bucket = buckets.get(key)
    if (!bucket || bucket.resetAt <= now) return null
    return bucket
  }

  return {
    /** True when this client still has attempts left. */
    allows(key: string, now: number = nowSeconds()): boolean {
      return (current(key, now)?.failures ?? 0) < maxFailures
    },
    recordFailure(key: string, now: number = nowSeconds()) {
      const bucket = current(key, now)
      if (bucket) bucket.failures += 1
      else buckets.set(key, { failures: 1, resetAt: now + windowSeconds })
      // Bounded cleanup: drop windows that have already elapsed.
      buckets.forEach((other, otherKey) => {
        if (other.resetAt <= now) buckets.delete(otherKey)
      })
    },
    reset(key: string) {
      buckets.delete(key)
    },
  }
}

export const loginThrottle = createLoginThrottle()

/**
 * Throttle key for a request. Caddy appends the real client to
 * `x-forwarded-for`, so the *last* entry is the one we can trust — earlier ones
 * may have been sent by the client.
 */
export function clientKeyFromRequest(req: Request): string {
  const forwarded = req.headers.get('x-forwarded-for')
  if (!forwarded) return 'unknown'
  const parts = forwarded.split(',')
  return parts[parts.length - 1].trim() || 'unknown'
}

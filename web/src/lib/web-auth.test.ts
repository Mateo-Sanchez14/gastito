/**
 * @jest-environment node
 */
// node, not jsdom: the session tokens are signed with Web Crypto (`crypto.subtle`),
// which jsdom's crypto stub does not implement.
import {
  GateCredentials,
  SESSION_TTL_SECONDS,
  checkBasicAuthHeader,
  createLoginThrottle,
  createSessionToken,
  credentialsMatch,
  isSecureRequest,
  loginPath,
  publicOrigin,
  redirectToPath,
  safeNextPath,
  shouldRefreshSession,
  verifySessionToken,
} from './web-auth'

const creds: GateCredentials = { user: 'amigos', pass: 'clave-compartida' }
const NOW = 1_800_000_000

const basic = (user: string, pass: string) =>
  `Basic ${Buffer.from(`${user}:${pass}`, 'utf8').toString('base64')}`

describe('credentialsMatch', () => {
  it('accepts the configured credentials', () => {
    expect(credentialsMatch('amigos', 'clave-compartida', creds)).toBe(true)
  })

  it('rejects a wrong password, user, or empty input', () => {
    expect(credentialsMatch('amigos', 'clave-compartid', creds)).toBe(false)
    expect(credentialsMatch('amigo', 'clave-compartida', creds)).toBe(false)
    expect(credentialsMatch('', '', creds)).toBe(false)
  })
})

describe('checkBasicAuthHeader', () => {
  it('accepts a valid header', () => {
    expect(
      checkBasicAuthHeader(basic('amigos', 'clave-compartida'), creds),
    ).toBe(true)
  })

  it('handles non-ASCII passwords', () => {
    const utf8 = { user: 'amigos', pass: 'contraseña-ñandú' }
    expect(checkBasicAuthHeader(basic(utf8.user, utf8.pass), utf8)).toBe(true)
  })

  it('keeps working with a password containing a colon', () => {
    const colon = { user: 'amigos', pass: 'a:b:c' }
    expect(checkBasicAuthHeader(basic(colon.user, colon.pass), colon)).toBe(
      true,
    )
  })

  it('rejects wrong, missing, or malformed headers', () => {
    expect(checkBasicAuthHeader(basic('amigos', 'nope'), creds)).toBe(false)
    expect(checkBasicAuthHeader(null, creds)).toBe(false)
    expect(checkBasicAuthHeader('Bearer something', creds)).toBe(false)
    expect(checkBasicAuthHeader('Basic not-base64!!', creds)).toBe(false)
    expect(checkBasicAuthHeader(`Basic ${btoa('no-separator')}`, creds)).toBe(
      false,
    )
  })
})

describe('session tokens', () => {
  it('round-trips a freshly issued token', async () => {
    const token = await createSessionToken(creds, NOW)
    expect(await verifySessionToken(token, creds, NOW)).toEqual({
      expiresAt: NOW + SESSION_TTL_SECONDS,
    })
  })

  it('rejects a token after it expires', async () => {
    const token = await createSessionToken(creds, NOW)
    expect(
      await verifySessionToken(token, creds, NOW + SESSION_TTL_SECONDS - 1),
    ).not.toBeNull()
    expect(
      await verifySessionToken(token, creds, NOW + SESSION_TTL_SECONDS),
    ).toBeNull()
  })

  it('rejects a token signed for other credentials (password rotation logs everyone out)', async () => {
    const token = await createSessionToken(creds, NOW)
    expect(
      await verifySessionToken(token, { ...creds, pass: 'clave-nueva' }, NOW),
    ).toBeNull()
    expect(
      await verifySessionToken(token, { ...creds, user: 'otros' }, NOW),
    ).toBeNull()
  })

  it('rejects a tampered expiry', async () => {
    const token = await createSessionToken(creds, NOW)
    const [prefix, expiry, signature] = token.split('.')
    const forged = `${prefix}.${
      Number(expiry) + 10 * SESSION_TTL_SECONDS
    }.${signature}`
    expect(await verifySessionToken(forged, creds, NOW)).toBeNull()
  })

  it('rejects garbage, empty and missing tokens', async () => {
    expect(await verifySessionToken(undefined, creds, NOW)).toBeNull()
    expect(await verifySessionToken('', creds, NOW)).toBeNull()
    expect(await verifySessionToken('g1.abc.def', creds, NOW)).toBeNull()
    expect(await verifySessionToken('nope', creds, NOW)).toBeNull()
    const token = await createSessionToken(creds, NOW)
    expect(
      await verifySessionToken(token.replace('g1.', 'g2.'), creds, NOW),
    ).toBeNull()
  })
})

describe('shouldRefreshSession', () => {
  const expiresAt = NOW + SESSION_TTL_SECONDS
  const day = 24 * 60 * 60

  it('leaves a young session alone', () => {
    expect(shouldRefreshSession(expiresAt, NOW + 29 * day)).toBe(false)
  })

  it('re-issues once the session is a month old', () => {
    expect(shouldRefreshSession(expiresAt, NOW + 31 * day)).toBe(true)
  })
})

describe('safeNextPath', () => {
  it('keeps same-origin paths', () => {
    expect(safeNextPath('/groups/abc/expenses')).toBe('/groups/abc/expenses')
    expect(safeNextPath('/groups?tab=all')).toBe('/groups?tab=all')
  })

  it('falls back to the root for anything that could leave the site', () => {
    expect(safeNextPath('//evil.com')).toBe('/')
    expect(safeNextPath('/\\evil.com')).toBe('/')
    expect(safeNextPath('https://evil.com')).toBe('/')
    expect(safeNextPath('javascript:alert(1)')).toBe('/')
    expect(safeNextPath(null)).toBe('/')
    expect(safeNextPath('')).toBe('/')
  })

  it('does not bounce back to the login page itself', () => {
    expect(safeNextPath('/login?error=invalid')).toBe('/')
  })
})

describe('isSecureRequest', () => {
  const request = (url: string, headers?: Record<string, string>) =>
    new Request(url, { headers })

  it('trusts the proxy header first', () => {
    expect(
      isSecureRequest(
        request('http://web:3000/groups', { 'x-forwarded-proto': 'https' }),
      ),
    ).toBe(true)
    expect(
      isSecureRequest(
        request('https://gastito.example/groups', {
          'x-forwarded-proto': 'http',
        }),
      ),
    ).toBe(false)
  })

  it('falls back to the request URL', () => {
    expect(isSecureRequest(request('https://gastito.example/groups'))).toBe(
      true,
    )
    expect(isSecureRequest(request('http://localhost:3000/groups'))).toBe(false)
  })
})

describe('login throttle', () => {
  it('blocks after the configured number of failures', () => {
    const throttle = createLoginThrottle({ maxFailures: 3, windowSeconds: 900 })
    for (let i = 0; i < 3; i++) {
      expect(throttle.allows('1.2.3.4', NOW)).toBe(true)
      throttle.recordFailure('1.2.3.4', NOW)
    }
    expect(throttle.allows('1.2.3.4', NOW)).toBe(false)
    // Other clients are unaffected.
    expect(throttle.allows('5.6.7.8', NOW)).toBe(true)
  })

  it('forgets failures once the window elapses', () => {
    const throttle = createLoginThrottle({ maxFailures: 1, windowSeconds: 900 })
    throttle.recordFailure('1.2.3.4', NOW)
    expect(throttle.allows('1.2.3.4', NOW + 899)).toBe(false)
    expect(throttle.allows('1.2.3.4', NOW + 900)).toBe(true)
  })

  it('clears the bucket on a successful login', () => {
    const throttle = createLoginThrottle({ maxFailures: 1, windowSeconds: 900 })
    throttle.recordFailure('1.2.3.4', NOW)
    throttle.reset('1.2.3.4')
    expect(throttle.allows('1.2.3.4', NOW)).toBe(true)
  })
})

describe('loginPath', () => {
  it('is a bare path when there is nothing to carry over', () => {
    expect(loginPath()).toBe('/login')
    expect(loginPath({ next: '/' })).toBe('/login')
  })

  it('carries the target and the error code', () => {
    expect(loginPath({ next: '/groups/abc/expenses' })).toBe(
      '/login?next=%2Fgroups%2Fabc%2Fexpenses',
    )
    expect(loginPath({ error: 'invalid', next: '/groups' })).toBe(
      '/login?error=invalid&next=%2Fgroups',
    )
  })

  it('drops a `next` that is not a local path', () => {
    expect(loginPath({ next: '//evil.example' })).toBe('/login')
    expect(loginPath({ next: 'https://evil.example' })).toBe('/login')
  })
})

describe('publicOrigin', () => {
  const request = (url: string, headers?: Record<string, string>) =>
    new Request(url, { headers })

  it('uses the host the browser asked for, not the container address', () => {
    expect(
      publicOrigin(
        request('http://localhost:3000/groups', {
          host: 'gastito.example',
          'x-forwarded-proto': 'https',
        }),
      ),
    ).toBe('https://gastito.example')
  })

  it('falls back to x-forwarded-host when the proxy rewrote Host', () => {
    expect(
      publicOrigin(
        request('http://localhost:3000/groups', {
          'x-forwarded-host': 'gastito.example',
          'x-forwarded-proto': 'https',
        }),
      ),
    ).toBe('https://gastito.example')
  })

  it('leaves plain local dev alone', () => {
    expect(publicOrigin(request('http://localhost:3000/groups'))).toBe(
      'http://localhost:3000',
    )
  })
})

describe('redirectToPath', () => {
  const request = (headers?: Record<string, string>) =>
    new Request('http://localhost:3000/groups', { headers })

  it('redirects to the public origin, never the container address', () => {
    const res = redirectToPath(
      request({ host: 'gastito.example', 'x-forwarded-proto': 'https' }),
      '/groups',
      303,
    )
    expect(res.status).toBe(303)
    expect(res.headers.get('location')).toBe('https://gastito.example/groups')
  })

  it('keeps the query string and carries cookies', () => {
    const res = redirectToPath(
      request({ host: 'gastito.example', 'x-forwarded-proto': 'https' }),
      '/login?error=invalid',
      303,
    )
    expect(res.headers.get('location')).toBe(
      'https://gastito.example/login?error=invalid',
    )
    res.cookies.set('gastito_session', 'token', { path: '/' })
    expect(res.headers.get('set-cookie')).toContain('gastito_session=token')
  })
})

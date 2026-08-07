import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  SESSION_COOKIE_NAME,
  getGateCredentials,
  safeNextPath,
  verifySessionToken,
} from '@/lib/web-auth'
import { Metadata } from 'next'
import { getTranslations } from 'next-intl/server'
import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'

/**
 * gastito: the shared login for the web UI.
 *
 * Replaces the browser's native Basic Auth prompt: a real form, so password
 * managers can save it, and one that hands out a year-long session cookie
 * instead of asking again every time the phone's browser is restarted.
 */

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations('Login')
  return { title: t('title') }
}

type Props = {
  searchParams: Promise<{ error?: string; next?: string }>
}

export default async function LoginPage({ searchParams }: Props) {
  const { error, next: nextParam } = await searchParams
  const next = safeNextPath(nextParam)

  const creds = getGateCredentials()
  // Gate disabled (local dev), or already logged in: nothing to do here.
  if (!creds) redirect(next)
  const token = (await cookies()).get(SESSION_COOKIE_NAME)?.value
  if (await verifySessionToken(token, creds)) redirect(next)

  const t = await getTranslations('Login')

  return (
    <main className="flex-1 flex items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
          <CardDescription>{t('description')}</CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <Alert variant="destructive" className="mb-4">
              <AlertDescription>
                {error === 'throttled' ? t('throttled') : t('invalid')}
              </AlertDescription>
            </Alert>
          )}
          {/* Plain form post (see api/auth/login): works without JS, and lets
              password managers treat this as a normal login. */}
          <form action="/api/auth/login" method="post" className="space-y-4">
            <input type="hidden" name="next" value={next} />
            <div className="space-y-2">
              <Label htmlFor="username">{t('username')}</Label>
              <Input
                id="username"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">{t('password')}</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </div>
            <Button type="submit" className="w-full">
              {t('submit')}
            </Button>
            <p className="text-muted-foreground text-xs">{t('remembered')}</p>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}

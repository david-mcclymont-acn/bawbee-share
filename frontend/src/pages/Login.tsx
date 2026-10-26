import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useSearchParams } from 'react-router'
import { api, setCsrfToken, useAuthConfig, type Me } from '../api.ts'
import { useAuth } from '../auth.tsx'
import { Band } from '../Layout.tsx'
import { ErrorText, Field, Loading } from '../ui.tsx'

export default function Login() {
  const { me, loading, refresh } = useAuth()
  const config = useAuthConfig()
  const [params] = useSearchParams()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const from = (location.state as { from?: string } | null)?.from ?? '/'
  // Back from a bank sign-in on a browser that wasn't signed in: finish that link now.
  const next = params.get('next')
  if (loading) return <Loading what="sign-in" />
  if (me && next?.startsWith('/api/banks/callback?')) {
    window.location.replace(next)
    return <Loading what="your bank link" />
  }
  if (me) return <Navigate to={from} replace />

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const signedIn = await api.post<Me>('/api/auth/login', { username, password })
      setCsrfToken(signedIn.csrf_token)
      await refresh()
    } catch (e) {
      setError(e)
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  const oidcError = params.get('error')
  const local = config.data?.local ?? true
  const oidc = config.data?.oidc

  return (
    <>
      <Band />
      <main id="main" className="sheet sheet-narrow">
        <section className="signin" aria-labelledby="signin-title">
          <h1 id="signin-title">Sign in</h1>
          <p className="page-lead">Person 1 and Person 2’s budget: where every bawbee goes.</p>

          {oidcError && (
            <p className="error-text" role="alert">
              {oidcError === 'cancelled' ? 'Sign-in was cancelled.' : oidcError}
            </p>
          )}

          {local && (
            <form className="form" onSubmit={(e) => void submit(e)}>
              <Field label="Username">
                {(id) => (
                  <input
                    id={id}
                    name="username"
                    autoComplete="username"
                    autoCapitalize="none"
                    spellCheck={false}
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                )}
              </Field>
              <Field label="Password">
                {(id) => (
                  <input
                    id={id}
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                )}
              </Field>
              <ErrorText error={error} />
              <button className="button button-primary" type="submit" disabled={busy}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
          )}

          {oidc?.enabled && (
            <div className="signin-alt">
              {local && <p className="signin-or">or</p>}
              <a className="button" href="/api/auth/oidc/start">
                Sign in with {oidc.label}
              </a>
            </div>
          )}

          <p className="field-hint signin-help">
            Forgotten your password? Whoever looks after the server can set a new one for you with{' '}
            <code>bawbee user reset-password</code>.
          </p>
        </section>
      </main>
    </>
  )
}

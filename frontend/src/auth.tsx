import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, type ReactNode } from 'react'
import { ApiError, api, keys, setCsrfToken, type Me } from './api.ts'

type AuthState = {
  me: Me | null
  loading: boolean
  refresh: () => Promise<unknown>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

async function fetchMe(): Promise<Me | null> {
  try {
    const me = await api.get<Me>('/api/auth/me')
    setCsrfToken(me.csrf_token)
    return me
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      setCsrfToken(null)
      return null
    }
    throw error
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: keys.me, queryFn: fetchMe, staleTime: 60_000 })

  const state: AuthState = {
    me: query.data ?? null,
    loading: query.isPending,
    refresh: () => query.refetch(),
    signOut: async () => {
      await api.post('/api/auth/logout').catch(() => undefined)
      setCsrfToken(null)
      client.clear()
      await query.refetch()
    },
  }
  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const state = useContext(AuthContext)
  if (!state) throw new Error('useAuth outside AuthProvider')
  return state
}

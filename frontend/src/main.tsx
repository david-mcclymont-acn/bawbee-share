import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
// Apple devices use their own San Francisco; Inter (with optical sizing) everywhere else.
import '@fontsource-variable/inter/opsz.css'
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useLocation } from 'react-router'
import { ApiError } from './api.ts'
import { AuthProvider, useAuth } from './auth.tsx'
import Layout, { Band } from './Layout.tsx'
import Login from './pages/Login.tsx'
import { Loading } from './ui.tsx'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
})

function RequireSignIn() {
  const { me, loading } = useAuth()
  const location = useLocation()
  if (loading) {
    // The band even while loading: installed on an iPhone, it's what sits under the status bar.
    return (
      <>
        <Band />
        <main className="sheet">
          <Loading what="your budget" />
        </main>
      </>
    )
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <Outlet />
}

function page(load: () => Promise<{ default: React.ComponentType }>) {
  return async () => ({ Component: (await load()).default })
}

const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  {
    element: <RequireSignIn />,
    children: [
      {
        element: <Layout />,
        children: [
          // Each screen loads on demand; the dashboard's chart library stays out of the rest.
          { index: true, lazy: page(() => import('./pages/Dashboard.tsx')) },
          { path: 'payday', lazy: page(() => import('./pages/Payday.tsx')) },
          { path: 'inbox', lazy: page(() => import('./pages/Inbox.tsx')) },
          { path: 'budget', lazy: page(() => import('./pages/Budget.tsx')) },
          { path: 'plan/:tab?', lazy: page(() => import('./pages/Plan.tsx')) },
          { path: 'transactions', lazy: page(() => import('./pages/Transactions.tsx')) },
          { path: 'more', lazy: page(() => import('./pages/More.tsx')) },
          { path: 'banks', lazy: page(() => import('./pages/Banks.tsx')) },
          { path: 'regular', lazy: page(() => import('./pages/Regular.tsx')) },
          { path: 'versions', lazy: page(() => import('./pages/Versions.tsx')) },
          { path: 'settings', lazy: page(() => import('./pages/Settings.tsx')) },
          { path: '*', element: <Navigate to="/" replace /> },
        ],
      },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)

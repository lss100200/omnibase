import HomeRedirect from './page-client'

/**
 * Root entry point.
 *
 * Delegates to a client component that reads auth state (localStorage) and
 * redirects to /dashboard or /login accordingly. SSR cannot read localStorage,
 * so the actual routing decision happens client-side after hydration.
 */
export default function RootPage() {
  return <HomeRedirect />
}

import { ThemeToggle } from '@/components/theme-toggle'

/**
 * Layout for auth pages (login/register).
 *
 * Centers content vertically + horizontally with a subtle gradient backdrop.
 * No sidebar / header - this is the unauthenticated shell.
 *
 * Theme toggle floats top-right so users can switch theme before logging in.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen items-center justify-center bg-gradient-to-br from-background via-background to-muted p-4">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-md">{children}</div>
    </div>
  )
}

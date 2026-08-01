'use client'

import { ThemeProvider as NextThemesProvider } from 'next-themes'
import type { ThemeProviderProps } from 'next-themes'

/**
 * Wraps next-themes' ThemeProvider for use in the App Router root layout.
 *
 * - attribute="class": toggles `dark` class on <html> (Tailwind darkMode: 'class')
 * - defaultTheme="dark": honors your eyes by defaulting to dark on first visit
 * - enableSystem: still respects OS preference when user picks "system"
 * - disableTransitionOnChange: avoids the awkward fade when switching
 */
export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  )
}

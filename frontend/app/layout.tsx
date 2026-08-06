import type { Metadata, Viewport } from 'next'
import localFont from 'next/font/local'
import { Toaster } from 'sonner'
import { ThemeProvider } from '@/components/theme-provider'
import { AuthBootstrap } from '@/components/auth-bootstrap'
import { SwrProvider } from '@/components/providers/swr-provider'
import './globals.css'

const inter = localFont({
  src: './fonts/inter-latin-variable.woff2',
  variable: '--font-sans',
  display: 'swap',
  weight: '100 900',
})

const jetbrainsMono = localFont({
  src: './fonts/jetbrains-mono-latin-variable.woff2',
  variable: '--font-mono',
  display: 'swap',
  weight: '100 800',
})

export const metadata: Metadata = {
  title: {
    default: 'OmniBase',
    template: '%s · OmniBase',
  },
  description:
    '自托管、AI 原生的个人知识工作台。数据库为底座，内置 RAG、多智能体编排与 Skill/MCP 扩展生态。',
  applicationName: 'OmniBase',
  authors: [{ name: 'OmniBase Contributors' }],
  keywords: ['RAG', 'knowledge base', 'AI workbench', 'pgvector', 'agents', 'MCP'],
  icons: {
    icon: '/favicon.ico',
  },
}

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#000000' },
  ],
  width: 'device-width',
  initialScale: 1,
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className={`${inter.variable} ${jetbrainsMono.variable} font-sans antialiased`}>
        <ThemeProvider>
          <SwrProvider>
            <AuthBootstrap />
            {children}
            <Toaster position="top-right" richColors closeButton theme="system" />
          </SwrProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}

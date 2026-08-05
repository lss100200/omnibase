'use client'

import { Suspense, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Loader2, LogIn } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '@/lib/hooks/use-auth'
import { getApiErrorMessage } from '@/lib/api'
import { getSafeReturnPath } from '@/lib/auth-session'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const loginSchema = z.object({
  email: z.string().email('请输入有效的邮箱地址'),
  password: z.string().min(1, '请输入密码').max(128, '密码过长'),
})

type LoginForm = z.infer<typeof loginSchema>

function LoginFormCard() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { login, isAuthenticated, bootstrapStatus } = useAuth()
  const [submitting, setSubmitting] = useState(false)

  // If user is already logged in, redirect to dashboard
  useEffect(() => {
    if (bootstrapStatus === 'ready' && isAuthenticated) {
      const from = getSafeReturnPath(searchParams.get('from'))
      router.replace(from)
    }
  }, [router, searchParams, isAuthenticated, bootstrapStatus])

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  })

  const onSubmit = async (values: LoginForm) => {
    setSubmitting(true)
    try {
      await login(values.email, values.password)
      toast.success('登录成功')
      const from = getSafeReturnPath(searchParams.get('from'))
      router.replace(from)
    } catch (err) {
      toast.error('登录失败', { description: getApiErrorMessage(err, '邮箱或密码错误') })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card className="overflow-hidden rounded-none border-stone-300/80 bg-[#f7f3ea] text-stone-950 shadow-[0_28px_80px_-48px_rgba(0,0,0,.55)] dark:border-stone-700 dark:bg-[#211f1b] dark:text-stone-100">
      <div className="h-0.5 w-full bg-amber-400" />
      <CardHeader className="space-y-2 px-6 pb-6 pt-7 sm:px-8 sm:pt-9">
        <div className="flex items-center justify-between gap-3">
          <div className="font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-amber-700 dark:text-amber-300">
            Work session access
          </div>
          <div className="flex items-center gap-1.5 font-mono text-[7px] uppercase tracking-wider text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Browser control plane
          </div>
        </div>
        <CardTitle className="pt-3 text-3xl tracking-[-0.05em]">进入你的工作台</CardTitle>
        <CardDescription className="text-sm leading-6">
          继续管理知识、空间与受控 AI 能力。登录不会自动启动任何 Agent Runtime。
        </CardDescription>
      </CardHeader>
      <form onSubmit={handleSubmit(onSubmit)}>
        <CardContent className="space-y-5 px-6 sm:px-8">
          <div className="space-y-2">
            <Label htmlFor="email">邮箱</Label>
            <Input
              id="email"
              type="email"
              placeholder="you@example.com"
              autoComplete="email"
              autoFocus
              className="h-12 rounded-none border-stone-300 bg-white/50 dark:border-stone-700 dark:bg-black/15"
              {...register('email')}
            />
            {errors.email && <p className="text-sm text-destructive">{errors.email.message}</p>}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              className="h-12 rounded-none border-stone-300 bg-white/50 dark:border-stone-700 dark:bg-black/15"
              {...register('password')}
            />
            {errors.password && (
              <p className="text-sm text-destructive">{errors.password.message}</p>
            )}
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-4 px-6 pb-7 pt-2 sm:px-8 sm:pb-9">
          <Button
            type="submit"
            className="h-12 w-full rounded-none border-0 bg-stone-950 text-stone-50 hover:bg-amber-400 hover:text-stone-950 dark:bg-stone-100 dark:text-stone-950 dark:hover:bg-amber-300"
            disabled={submitting}
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <LogIn className="h-4 w-4" />
                登录
              </>
            )}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            还没有账号？{' '}
            <Link href="/register" className="font-medium text-primary hover:underline">
              立即注册
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  )
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <Card className="rounded-none border-stone-300 bg-[#f7f3ea] shadow-2xl dark:border-stone-700 dark:bg-[#211f1b]">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            正在加载登录页…
          </CardContent>
        </Card>
      }
    >
      <LoginFormCard />
    </Suspense>
  )
}

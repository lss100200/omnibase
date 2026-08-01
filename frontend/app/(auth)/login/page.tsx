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
    <Card className="border-border/60 shadow-lg">
      <CardHeader className="space-y-1">
        <CardTitle className="text-2xl">欢迎回来</CardTitle>
        <CardDescription>输入你的账号信息以继续</CardDescription>
      </CardHeader>
      <form onSubmit={handleSubmit(onSubmit)}>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">邮箱</Label>
            <Input
              id="email"
              type="email"
              placeholder="you@example.com"
              autoComplete="email"
              autoFocus
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
              {...register('password')}
            />
            {errors.password && (
              <p className="text-sm text-destructive">{errors.password.message}</p>
            )}
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={submitting}>
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
        <Card className="border-border/60 shadow-lg">
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

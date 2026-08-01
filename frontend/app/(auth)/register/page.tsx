'use client'

import { Suspense, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Loader2, UserPlus } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '@/lib/hooks/use-auth'
import { getApiErrorMessage } from '@/lib/api'
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

const registerSchema = z
  .object({
    email: z.string().email('请输入有效的邮箱地址'),
    password: z
      .string()
      .min(8, '密码至少 8 个字符')
      .max(128, '密码过长')
      .regex(/[A-Za-z]/, '密码必须包含字母')
      .regex(/\d/, '密码必须包含数字'),
    confirmPassword: z.string(),
    tenantName: z.string().max(100, '工作空间名称过长').optional().or(z.literal('')),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: '两次输入的密码不一致',
    path: ['confirmPassword'],
  })

type RegisterForm = z.infer<typeof registerSchema>

function RegisterFormCard() {
  const router = useRouter()
  const { register: registerUser, isAuthenticated, bootstrapStatus } = useAuth()
  const [submitting, setSubmitting] = useState(false)

  // If user is already logged in, redirect to dashboard
  useEffect(() => {
    if (bootstrapStatus === 'ready' && isAuthenticated) {
      router.replace('/dashboard')
    }
  }, [router, isAuthenticated, bootstrapStatus])

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterForm>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: '', password: '', confirmPassword: '', tenantName: '' },
  })

  const onSubmit = async (values: RegisterForm) => {
    setSubmitting(true)
    try {
      const tenantName = values.tenantName?.trim() || undefined
      await registerUser(values.email, values.password, tenantName)
      toast.success('注册成功', { description: '已自动登录，欢迎使用 OmniBase！' })
      router.replace('/dashboard')
    } catch (err) {
      toast.error('注册失败', { description: getApiErrorMessage(err, '请检查输入或稍后再试') })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card className="border-border/60 shadow-lg">
      <CardHeader className="space-y-1">
        <CardTitle className="text-2xl">创建账号</CardTitle>
        <CardDescription>注册后将自动创建一个专属工作空间，所有数据都只属于你</CardDescription>
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
            <Label htmlFor="tenantName">工作空间名称（可选）</Label>
            <Input
              id="tenantName"
              type="text"
              placeholder="我的知识库"
              {...register('tenantName')}
            />
            <p className="text-xs text-muted-foreground">留空将根据邮箱自动生成一个默认名称</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              {...register('password')}
            />
            {errors.password && (
              <p className="text-sm text-destructive">{errors.password.message}</p>
            )}
            <p className="text-xs text-muted-foreground">至少 8 位，需包含字母和数字</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirmPassword">确认密码</Label>
            <Input
              id="confirmPassword"
              type="password"
              autoComplete="new-password"
              {...register('confirmPassword')}
            />
            {errors.confirmPassword && (
              <p className="text-sm text-destructive">{errors.confirmPassword.message}</p>
            )}
          </div>
        </CardContent>
        <CardFooter className="flex flex-col gap-3">
          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <UserPlus className="h-4 w-4" />
                注册
              </>
            )}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            已经有账号？{' '}
            <Link href="/login" className="font-medium text-primary hover:underline">
              直接登录
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  )
}

export default function RegisterPage() {
  return (
    <Suspense
      fallback={
        <Card className="border-border/60 shadow-lg">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            正在加载注册页…
          </CardContent>
        </Card>
      }
    >
      <RegisterFormCard />
    </Suspense>
  )
}

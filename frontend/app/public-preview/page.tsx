import type { Metadata } from 'next'
import Link from 'next/link'
import {
  ArrowRight,
  BookOpenCheck,
  Box,
  Braces,
  Check,
  ChevronRight,
  CircleDot,
  CloudCog,
  Code2,
  Database,
  ExternalLink,
  FileSearch,
  Fingerprint,
  Github,
  GitPullRequest,
  KeyRound,
  Layers3,
  LockKeyhole,
  Map,
  Network,
  Orbit,
  Radar,
  Route,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Workflow,
  X,
} from 'lucide-react'
import { ThemeToggle } from '@/components/theme-toggle'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import styles from './page.module.css'

const repositoryUrl = 'https://github.com/lss100200/omnibase'

export const metadata: Metadata = {
  title: 'Public Preview',
  description:
    'OmniBase 是面向 AI 工作负载的自托管知识与能力基础设施：数据库、RAG、安全能力网关和 AI 可读维护者地图。',
  openGraph: {
    title: 'OmniBase · AI-first, repairable by design',
    description: '先把边界、数据和恢复路径做对，再让 Agent 进入工作空间。',
    type: 'website',
  },
}

const foundations = [
  {
    icon: Database,
    eyebrow: 'DATABASE + RAG',
    title: '让知识成为可治理的数据',
    description:
      'PostgreSQL 与 pgvector 承载关系数据和向量索引；混合检索、重排与引用回链从同一套数据边界生长。',
    accent: 'from-cyan-500/18 to-blue-500/5',
  },
  {
    icon: ShieldCheck,
    eyebrow: 'CAPABILITY SECURITY',
    title: '默认拒绝，而不是默认相信',
    description:
      '能力按租户、工作空间、运行实例、动作、资源、版本、时限与预算共同绑定；撤销、审计与幂等形成闭环。',
    accent: 'from-blue-500/18 to-violet-500/5',
  },
  {
    icon: Map,
    eyebrow: 'AI MAINTAINABILITY',
    title: '让下一位维护者看得懂',
    description:
      '机器可读的维护者地图标记入口、调用链、安全不变量、验证命令和恢复路径，让更换模型不等于丢失工程记忆。',
    accent: 'from-violet-500/18 to-fuchsia-500/5',
  },
  {
    icon: Layers3,
    eyebrow: 'WORKSPACE CONTROL PLANE',
    title: '工作空间先有治理，再有运行时',
    description:
      'P34.4 已建立 Workspace、Run、Node、lease 与 fencing 的逻辑控制面；真实运行时必须通过后续隔离 Gate。',
    accent: 'from-emerald-500/18 to-cyan-500/5',
  },
]

const delivered = [
  '认证、租户边界与实时 RBAC 复核',
  '生产级 RAG 主链路与引用回链',
  '受控数据能力、审批、审计与幂等状态机',
  '独立 Capability Gateway 与只读 SDK 契约',
  'P34.4 Workspace 元数据控制面与 synthetic harness',
  'AI 维护者地图、CI Gate 与恢复 Runbook',
]

const building = [
  'P34.5 文件、网络、进程、身份与资源隔离 Gate',
  '独立 Sandbox Runtime 与 Network Broker',
  '真实 Overlay adapter 与短期 workload identity',
  '隔离通过后的只读 Tenant / RAG 数据通道',
]

const frozen = [
  '任意敌对代码的生产安全承诺',
  'Sandbox 直接加入成员设备网络',
  '长期宿主凭据或数据库直连',
  '自主 Planner、多 Agent 长循环与任意 SQL',
]

export default function PublicPreviewPage() {
  return (
    <div className={cn(styles.page, 'min-h-screen overflow-hidden text-foreground')}>
      <div className={cn(styles.grid, 'pointer-events-none absolute inset-x-0 top-0 h-[56rem]')} />

      <header className="relative z-40 border-b border-border/50 bg-background/75 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-8">
          <Link
            href="/public-preview"
            className="group flex items-center gap-3"
            aria-label="OmniBase 首页"
          >
            <span className="relative flex h-9 w-9 items-center justify-center overflow-hidden rounded-xl border border-cyan-500/25 bg-gradient-to-br from-cyan-500/15 via-background to-violet-500/15 shadow-sm">
              <Orbit className="h-5 w-5 text-cyan-500 transition-transform duration-500 group-hover:rotate-45" />
            </span>
            <span>
              <span className="block text-sm font-semibold leading-none tracking-tight">
                OmniBase
              </span>
              <span className="mt-1 block font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
                AI Infrastructure
              </span>
            </span>
          </Link>

          <nav
            className="hidden items-center gap-7 text-sm text-muted-foreground md:flex"
            aria-label="页面导航"
          >
            <a className="transition-colors hover:text-foreground" href="#foundation">
              基础设施
            </a>
            <a className="transition-colors hover:text-foreground" href="#architecture">
              架构
            </a>
            <a className="transition-colors hover:text-foreground" href="#maintainability">
              可维护性
            </a>
            <a className="transition-colors hover:text-foreground" href="#status">
              交付边界
            </a>
          </nav>

          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Button asChild size="sm" className="hidden sm:inline-flex">
              <a href={repositoryUrl} target="_blank" rel="noreferrer">
                <Github className="h-4 w-4" />
                GitHub
              </a>
            </Button>
          </div>
        </div>
      </header>

      <main className="relative">
        <section className="mx-auto grid max-w-7xl gap-14 px-5 pb-20 pt-20 sm:px-8 sm:pt-28 lg:grid-cols-[1.04fr_0.96fr] lg:items-center lg:gap-16 lg:pb-28">
          <div>
            <div className="mb-7 flex flex-wrap items-center gap-3">
              <Badge
                variant="outline"
                className="gap-2 border-cyan-500/25 bg-cyan-500/5 px-3 py-1 text-cyan-700 dark:text-cyan-300"
              >
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-cyan-500" />
                </span>
                Public Preview · v0.1 alpha
              </Badge>
              <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                Self-hosted · Open source · Fail-closed
              </span>
            </div>

            <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.045em] sm:text-5xl lg:text-[4.4rem]">
              让 AI 开始工作前，
              <span className="bg-gradient-to-r from-cyan-500 via-blue-500 to-violet-500 bg-clip-text text-transparent">
                先让基础设施值得信任。
              </span>
            </h1>

            <p className="mt-7 max-w-2xl text-pretty text-base leading-8 text-muted-foreground sm:text-lg">
              OmniBase 是一个面向 AI
              工作负载的自托管知识与能力底座。它把数据库、RAG、工作空间治理、能力授权与恢复路径放在同一条安全链上——让用户能够借助
              AI 创造，也不必反复为 AI 的错误买单。
            </p>

            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Button asChild size="lg" className="group h-12 rounded-xl px-6">
                <a href={repositoryUrl} target="_blank" rel="noreferrer">
                  <Github className="h-4 w-4" />
                  查看公开源码
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                </a>
              </Button>
              <Button asChild variant="outline" size="lg" className="h-12 rounded-xl px-6">
                <Link href="/login">
                  进入本地工作台
                  <ChevronRight className="h-4 w-4" />
                </Link>
              </Button>
            </div>

            <div className="mt-9 flex flex-wrap gap-x-6 gap-y-3 text-xs text-muted-foreground">
              {['Apache-2.0', 'No tracking', 'Local-first', 'Source-repairable'].map((item) => (
                <span key={item} className="flex items-center gap-2">
                  <Check className="h-3.5 w-3.5 text-emerald-500" />
                  {item}
                </span>
              ))}
            </div>
          </div>

          <HeroConsole />
        </section>

        <section className="border-y border-border/60 bg-muted/20">
          <div className="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-y divide-border/60 px-5 sm:px-8 lg:grid-cols-4 lg:divide-y-0">
            <Metric value="Fail-closed" label="默认拒绝装配" />
            <Metric value="105 / 0" label="Backend Mypy 封板证据" />
            <Metric value="17 tables" label="P34.4 控制面" />
            <Metric value="AI-readable" label="机器可读维护地图" />
          </div>
        </section>

        <section id="foundation" className="mx-auto max-w-7xl px-5 py-24 sm:px-8 sm:py-32">
          <SectionHeading
            kicker="INFRASTRUCTURE BEFORE ORCHESTRATION"
            title="不是又一个聊天外壳。"
            description="OmniBase 从最难被看见、却最容易在未来失控的部分开始：身份、数据、授权、审计、恢复和可维护性。"
          />

          <div className="mt-12 grid gap-4 md:grid-cols-2">
            {foundations.map((item) => {
              const Icon = item.icon
              return (
                <article
                  key={item.title}
                  className="group relative overflow-hidden rounded-2xl border border-border/70 bg-card/65 p-7 shadow-sm backdrop-blur-sm transition-all duration-300 hover:-translate-y-1 hover:border-cyan-500/25 hover:shadow-xl hover:shadow-cyan-500/5 sm:p-8"
                >
                  <div
                    className={cn(
                      'pointer-events-none absolute inset-0 bg-gradient-to-br opacity-70 transition-opacity group-hover:opacity-100',
                      item.accent,
                    )}
                  />
                  <div className="relative">
                    <div className="flex items-start justify-between">
                      <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-border/60 bg-background/75 shadow-sm">
                        <Icon className="h-5 w-5 text-cyan-600 dark:text-cyan-300" />
                      </span>
                      <span className="font-mono text-[9px] tracking-[0.19em] text-muted-foreground">
                        {item.eyebrow}
                      </span>
                    </div>
                    <h3 className="mt-8 text-xl font-semibold tracking-tight">{item.title}</h3>
                    <p className="mt-3 max-w-xl text-sm leading-7 text-muted-foreground">
                      {item.description}
                    </p>
                  </div>
                </article>
              )
            })}
          </div>
        </section>

        <section id="architecture" className="border-y border-border/60 bg-card/35 py-24 sm:py-32">
          <div className="mx-auto max-w-7xl px-5 sm:px-8">
            <SectionHeading
              kicker="ONE AUTHORIZATION LOOP"
              title="AI 只能在被证明安全的路径上行动。"
              description="浏览器控制面与工作负载能力网关保持独立；逻辑资源标识留在外部，物理数据库定位始终由服务端掌握。"
              centered
            />

            <div className="relative mt-14 grid gap-4 lg:grid-cols-[1fr_auto_1fr_auto_1fr] lg:items-stretch">
              <ArchitectureCard
                number="01"
                icon={Workflow}
                title="Workspace Control Plane"
                items={['成员与角色', 'Run / Node lifecycle', 'Lease + fencing']}
              />
              <FlowArrow />
              <ArchitectureCard
                number="02"
                icon={KeyRound}
                title="Capability Gateway"
                items={['短期 workload identity', '动作与资源绑定', '预算、撤销、审计']}
                highlighted
              />
              <FlowArrow />
              <ArchitectureCard
                number="03"
                icon={Database}
                title="Data + RAG Plane"
                items={['逻辑资源解析', '有界只读适配', '引用与 lineage']}
              />
            </div>

            <div className="mt-6 flex items-start gap-3 rounded-xl border border-amber-500/25 bg-amber-500/5 px-5 py-4 text-sm text-amber-900 dark:text-amber-200">
              <Radar className="mt-0.5 h-4 w-4 shrink-0" />
              <p className="leading-6">
                当前 Public Preview 已交付前置治理与能力契约；真实 Sandbox 接入数据平面必须等待
                P34.5 文件、网络、进程、身份和资源隔离 Gate 全部通过。
              </p>
            </div>
          </div>
        </section>

        <section
          id="maintainability"
          className="mx-auto grid max-w-7xl gap-14 px-5 py-24 sm:px-8 sm:py-32 lg:grid-cols-[0.92fr_1.08fr] lg:items-center"
        >
          <div>
            <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-2xl border border-violet-500/20 bg-violet-500/10">
              <BookOpenCheck className="h-6 w-6 text-violet-500" />
            </div>
            <p className="font-mono text-[10px] font-medium uppercase tracking-[0.23em] text-violet-500">
              REPAIRABLE FROM SOURCE
            </p>
            <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">
              让下一位 AI，也能把它修好。
            </h2>
            <p className="mt-5 text-base leading-8 text-muted-foreground">
              开源不只是把源代码放上 GitHub。OmniBase
              把安全不变量、模块入口、调用链、影响矩阵、验证命令与恢复步骤一同交给维护者。即使原作者不在，即使模型更换，工程上下文仍然留在仓库里。
            </p>
            <a
              href={`${repositoryUrl}/blob/main/AGENTS.md`}
              target="_blank"
              rel="noreferrer"
              className="mt-7 inline-flex items-center gap-2 text-sm font-medium text-violet-600 hover:underline dark:text-violet-300"
            >
              阅读 AI Maintainer Contract
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>

          <MaintainerMap />
        </section>

        <section id="status" className="border-y border-border/60 bg-muted/20 py-24 sm:py-32">
          <div className="mx-auto max-w-7xl px-5 sm:px-8">
            <SectionHeading
              kicker="HONEST PUBLIC PREVIEW"
              title="我们只宣传已经经得起验证的部分。"
              description="基础设施的可信度来自边界清晰，而不是功能列表足够长。下面是当前公开版本真实交付状态。"
            />

            <div className="mt-12 grid gap-5 lg:grid-cols-3">
              <StatusColumn
                tone="ready"
                icon={Check}
                eyebrow="VERIFIED FOUNDATION"
                title="已经交付"
                items={delivered}
              />
              <StatusColumn
                tone="building"
                icon={CloudCog}
                eyebrow="P34.5 IN PROGRESS"
                title="正在建设"
                items={building}
              />
              <StatusColumn
                tone="frozen"
                icon={X}
                eyebrow="NOT YET CLAIMED"
                title="明确未承诺"
                items={frozen}
              />
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-5 py-24 sm:px-8 sm:py-32">
          <div className="relative overflow-hidden rounded-3xl border border-cyan-500/20 bg-zinc-950 px-6 py-14 text-white shadow-2xl sm:px-12 sm:py-16 lg:px-16">
            <div className="pointer-events-none absolute -right-32 -top-48 h-96 w-96 rounded-full bg-cyan-500/20 blur-3xl" />
            <div className="pointer-events-none absolute -bottom-48 left-1/3 h-96 w-96 rounded-full bg-violet-500/20 blur-3xl" />
            <div className="relative grid gap-9 lg:grid-cols-[1fr_auto] lg:items-end">
              <div>
                <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-300">
                  <Sparkles className="h-3.5 w-3.5" />
                  Build with us
                </div>
                <h2 className="mt-5 max-w-3xl text-3xl font-semibold tracking-[-0.035em] sm:text-5xl">
                  不让 AI 的创造力，变成下一笔技术债。
                </h2>
                <p className="mt-5 max-w-2xl text-base leading-7 text-zinc-400">
                  OmniBase 正以 Public Preview
                  持续更新。欢迎阅读源码、复核安全边界、补充测试，或和我们一起把 AI
                  工作空间做成真正可维护的公共基础设施。
                </p>
              </div>
              <div className="flex flex-col gap-3 sm:flex-row lg:flex-col">
                <Button
                  asChild
                  size="lg"
                  className="h-12 rounded-xl bg-white text-zinc-950 hover:bg-zinc-200"
                >
                  <a href={repositoryUrl} target="_blank" rel="noreferrer">
                    <Github className="h-4 w-4" />
                    Star on GitHub
                  </a>
                </Button>
                <Button
                  asChild
                  variant="outline"
                  size="lg"
                  className="h-12 rounded-xl border-zinc-700 bg-transparent text-white hover:bg-zinc-900 hover:text-white"
                >
                  <a href={`${repositoryUrl}/issues`} target="_blank" rel="noreferrer">
                    <GitPullRequest className="h-4 w-4" />
                    参与共建
                  </a>
                </Button>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border/60">
        <div className="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-10 text-sm text-muted-foreground sm:px-8 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg border bg-card">
              <Orbit className="h-4 w-4 text-cyan-500" />
            </span>
            <span>
              <strong className="font-medium text-foreground">OmniBase</strong>
              <span className="ml-2">AI-first, repairable by design.</span>
            </span>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <a className="hover:text-foreground" href={`${repositoryUrl}/blob/main/LICENSE`}>
              Apache-2.0
            </a>
            <a className="hover:text-foreground" href={`${repositoryUrl}/blob/main/SECURITY.md`}>
              Security
            </a>
            <a
              className="hover:text-foreground"
              href={`${repositoryUrl}/blob/main/CONTRIBUTING.md`}
            >
              Contributing
            </a>
          </div>
        </div>
      </footer>
    </div>
  )
}

function HeroConsole() {
  return (
    <div className={cn(styles.floatSlow, 'relative mx-auto w-full max-w-xl lg:mx-0')}>
      <div className="pointer-events-none absolute -inset-8 rounded-[3rem] bg-gradient-to-br from-cyan-500/10 via-transparent to-violet-500/10 blur-2xl" />
      <div
        className={cn(
          styles.heroGlow,
          'relative overflow-hidden rounded-2xl border border-white/10 bg-zinc-950 text-zinc-100',
        )}
      >
        <div className="flex h-11 items-center justify-between border-b border-white/10 px-4">
          <div className="flex gap-1.5" aria-hidden="true">
            <span className="h-2.5 w-2.5 rounded-full bg-rose-400/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-400/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/80" />
          </div>
          <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-zinc-500">
            omnibase / trust-boundary
          </span>
          <LockKeyhole className="h-3.5 w-3.5 text-emerald-400" />
        </div>

        <div className="p-5 sm:p-7">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-zinc-500">
                Runtime request
              </p>
              <p className="mt-1 text-sm font-medium">rag.search · workspace/private</p>
            </div>
            <span className="rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2.5 py-1 font-mono text-[9px] uppercase tracking-wider text-emerald-300">
              verified
            </span>
          </div>

          <div className="space-y-3">
            <ConsoleRow
              icon={Fingerprint}
              label="workload identity"
              value="short-lived · attested"
              tone="cyan"
            />
            <ConsoleRow icon={Box} label="workspace / run" value="bound · fenced" tone="blue" />
            <ConsoleRow
              icon={KeyRound}
              label="capability"
              value="action + resource + version"
              tone="violet"
            />
            <ConsoleRow icon={Route} label="data locator" value="logical ID only" tone="emerald" />
          </div>

          <div className="my-5 h-px bg-white/10" />

          <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-xl border border-white/10 bg-white/[0.035] p-4">
            <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-400/10">
              <ShieldCheck className="h-4 w-4 text-emerald-300" />
            </span>
            <div>
              <p className="text-xs font-medium">Policy decision</p>
              <p className="mt-0.5 font-mono text-[9px] uppercase tracking-wider text-zinc-500">
                audit + budget + revocation checked
              </p>
            </div>
            <span className="font-mono text-xs text-emerald-300">ALLOW</span>
          </div>

          <div className="mt-5 flex items-center gap-2" aria-hidden="true">
            <span
              className={cn(styles.signal, 'h-1 flex-1 origin-left rounded-full bg-cyan-400/60')}
            />
            <span
              className={cn(styles.signal, 'h-1 flex-1 origin-left rounded-full bg-blue-400/60')}
            />
            <span
              className={cn(styles.signal, 'h-1 flex-1 origin-left rounded-full bg-violet-400/60')}
            />
          </div>
        </div>
      </div>

      <div className="absolute -bottom-6 -left-4 hidden items-center gap-3 rounded-xl border border-border/70 bg-background/90 px-4 py-3 shadow-lg backdrop-blur md:flex">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/10">
          <FileSearch className="h-4 w-4 text-cyan-500" />
        </div>
        <div>
          <p className="text-xs font-medium">Evidence-linked</p>
          <p className="font-mono text-[9px] text-muted-foreground">citation · audit · lineage</p>
        </div>
      </div>
    </div>
  )
}

function ConsoleRow({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
  tone: 'cyan' | 'blue' | 'violet' | 'emerald'
}) {
  const colors = {
    cyan: 'text-cyan-300 bg-cyan-400/10',
    blue: 'text-blue-300 bg-blue-400/10',
    violet: 'text-violet-300 bg-violet-400/10',
    emerald: 'text-emerald-300 bg-emerald-400/10',
  }
  return (
    <div className="grid grid-cols-[auto_1fr] gap-3 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-2.5 sm:grid-cols-[auto_1fr_auto] sm:items-center">
      <span className={cn('flex h-7 w-7 items-center justify-center rounded-md', colors[tone])}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <span className="font-mono text-[10px] text-zinc-400">{label}</span>
      <span className="col-start-2 font-mono text-[10px] text-zinc-200 sm:col-auto">{value}</span>
    </div>
  )
}

function Metric({ value, label }: { value: string; label: string }) {
  return (
    <div className="px-4 py-7 sm:px-7">
      <p className="font-mono text-sm font-semibold tracking-tight text-foreground sm:text-base">
        {value}
      </p>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{label}</p>
    </div>
  )
}

function SectionHeading({
  kicker,
  title,
  description,
  centered = false,
}: {
  kicker: string
  title: string
  description: string
  centered?: boolean
}) {
  return (
    <div className={cn('max-w-2xl', centered && 'mx-auto text-center')}>
      <p className="font-mono text-[10px] font-medium uppercase tracking-[0.23em] text-cyan-600 dark:text-cyan-300">
        {kicker}
      </p>
      <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">{title}</h2>
      <p className="mt-4 text-base leading-7 text-muted-foreground">{description}</p>
    </div>
  )
}

function ArchitectureCard({
  number,
  icon: Icon,
  title,
  items,
  highlighted = false,
}: {
  number: string
  icon: React.ComponentType<{ className?: string }>
  title: string
  items: string[]
  highlighted?: boolean
}) {
  return (
    <article
      className={cn(
        'relative rounded-2xl border bg-background/75 p-6 shadow-sm',
        highlighted && 'border-cyan-500/30 shadow-lg shadow-cyan-500/5',
      )}
    >
      <div className="flex items-center justify-between">
        <span
          className={cn(
            'flex h-10 w-10 items-center justify-center rounded-xl border bg-card',
            highlighted && 'border-cyan-500/20 bg-cyan-500/10 text-cyan-600 dark:text-cyan-300',
          )}
        >
          <Icon className="h-5 w-5" />
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">{number}</span>
      </div>
      <h3 className="mt-7 text-base font-semibold">{title}</h3>
      <ul className="mt-4 space-y-2.5">
        {items.map((item) => (
          <li key={item} className="flex items-center gap-2 text-xs text-muted-foreground">
            <CircleDot className="h-3 w-3 text-cyan-500" />
            {item}
          </li>
        ))}
      </ul>
    </article>
  )
}

function FlowArrow() {
  return (
    <div
      className="flex items-center justify-center py-1 text-muted-foreground lg:px-1 lg:py-0"
      aria-hidden="true"
    >
      <ChevronRight className="hidden h-5 w-5 lg:block" />
      <div className="h-5 w-px bg-border lg:hidden" />
    </div>
  )
}

function MaintainerMap() {
  const rows = [
    { icon: TerminalSquare, key: 'entrypoints', value: 'API · Gateway · CLI' },
    { icon: LockKeyhole, key: 'invariants', value: 'auth · tenancy · audit' },
    { icon: Network, key: 'dependencies', value: 'source → contract → tests' },
    { icon: Braces, key: 'verification', value: 'typed, focused, repeatable' },
    { icon: GitPullRequest, key: 'recovery', value: 'forward-fix · restore-new' },
  ]

  return (
    <div className="relative">
      <div className="absolute -inset-5 rounded-[2rem] bg-gradient-to-br from-violet-500/10 to-cyan-500/10 blur-xl" />
      <div className="relative overflow-hidden rounded-2xl border border-border/70 bg-card shadow-xl">
        <div className="flex items-center justify-between border-b bg-muted/40 px-5 py-4">
          <div className="flex items-center gap-2">
            <Code2 className="h-4 w-4 text-violet-500" />
            <span className="font-mono text-xs font-medium">maintenance-map.json</span>
          </div>
          <span className="rounded-full border bg-background px-2 py-0.5 font-mono text-[9px] text-emerald-600 dark:text-emerald-400">
            CI VALIDATED
          </span>
        </div>
        <div className="space-y-1 p-3 sm:p-5">
          {rows.map((row, index) => {
            const Icon = row.icon
            return (
              <div
                key={row.key}
                className="grid grid-cols-[auto_1fr] items-center gap-3 rounded-xl px-3 py-3 transition-colors hover:bg-muted/60 sm:grid-cols-[auto_0.72fr_1.28fr]"
              >
                <span className="font-mono text-[9px] text-muted-foreground">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span className="flex items-center gap-2 text-xs font-medium">
                  <Icon className="h-3.5 w-3.5 text-violet-500" />
                  {row.key}
                </span>
                <span className="col-start-2 font-mono text-[10px] text-muted-foreground sm:col-start-auto">
                  {row.value}
                </span>
              </div>
            )
          })}
        </div>
        <div className="border-t bg-zinc-950 px-5 py-4 font-mono text-[10px] text-zinc-400">
          <span className="text-emerald-400">$</span> validate --repo-root .{' '}
          <span className="text-emerald-300">PASS</span>
          <span className="ml-2 text-zinc-600">{'// no hidden maintainer context'}</span>
        </div>
      </div>
    </div>
  )
}

function StatusColumn({
  tone,
  icon: Icon,
  eyebrow,
  title,
  items,
}: {
  tone: 'ready' | 'building' | 'frozen'
  icon: React.ComponentType<{ className?: string }>
  eyebrow: string
  title: string
  items: string[]
}) {
  const color = {
    ready: {
      border: 'border-emerald-500/25',
      icon: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300',
      text: 'text-emerald-600 dark:text-emerald-300',
      dot: 'text-emerald-500',
    },
    building: {
      border: 'border-cyan-500/25',
      icon: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-300',
      text: 'text-cyan-600 dark:text-cyan-300',
      dot: 'text-cyan-500',
    },
    frozen: {
      border: 'border-amber-500/25',
      icon: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
      text: 'text-amber-600 dark:text-amber-300',
      dot: 'text-amber-500',
    },
  }[tone]

  return (
    <article className={cn('rounded-2xl border bg-card/75 p-6 shadow-sm', color.border)}>
      <div className="flex items-center justify-between">
        <span className={cn('flex h-10 w-10 items-center justify-center rounded-xl', color.icon)}>
          <Icon className="h-5 w-5" />
        </span>
        <span className={cn('font-mono text-[9px] tracking-[0.17em]', color.text)}>{eyebrow}</span>
      </div>
      <h3 className="mt-6 text-xl font-semibold">{title}</h3>
      <ul className="mt-5 space-y-3.5">
        {items.map((item) => (
          <li key={item} className="flex items-start gap-3 text-sm leading-6 text-muted-foreground">
            <CircleDot className={cn('mt-1.5 h-3 w-3 shrink-0', color.dot)} />
            {item}
          </li>
        ))}
      </ul>
    </article>
  )
}

'use client'

import { useState } from 'react'
import {
  ArrowRight,
  BadgeCheck,
  BookOpenCheck,
  Bot,
  Boxes,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleDot,
  Code2,
  Database,
  Github,
  GitPullRequest,
  Languages,
  Layers3,
  Map,
  Orbit,
  Play,
  Rocket,
  ShieldCheck,
  Sparkles,
  Users,
  Workflow,
  Wrench,
} from 'lucide-react'
import { ThemeToggle } from '@/components/theme-toggle'
import { BrandMark } from '@/components/layout/brand-mark'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import styles from './page.module.css'

type Locale = 'zh' | 'en'

const repositoryUrl = 'https://github.com/lss100200/omnibase'

const copy = {
  zh: {
    nav: ['产品底座', '真实闭环', '角色设计', '路线图'],
    badge: 'Public Preview · 可运行的 Agent 工作台',
    meta: '开源 · 自托管 · 用户模型 · 版本化 Agent',
    heroLead: '从一个工作空间开始，创造并运行你的',
    heroAccent: '第一个 AI 员工。',
    heroBody:
      '连接自己的 OpenAI-compatible 模型，创建 Workspace，定义员工职责和版本，然后发起真实运行。OmniBase 把模型、知识、任务、运行记录和安全边界放进同一个可维护的工作台。',
    github: '查看公开源码',
    explore: '查看真实产品闭环',
    principles: ['Apache-2.0', 'Self-hosted', 'Personal model', 'Source-repairable'],
    metrics: [
      ['Personal provider', '管理并测试自己的模型 API'],
      ['Workspace', '为每个项目隔离上下文'],
      ['Agent Builder', '创建版本化的数字员工'],
      ['Task → Run', '让一次工作留下可审计记录'],
    ],
    foundationKicker: 'A WORKBENCH, NOT A CHAT WRAPPER',
    foundationTitle: '它首先是 AI 工作台，然后才是数据库与自动化平台。',
    foundationBody:
      '用户从模型、Workspace 和 Agent 开始工作；数据库、RAG、任务账本、租约、审计和维护者地图在背后提供可持续的工程底座。',
    studioKicker: 'A REAL PRODUCT LOOP',
    studioTitle: '今天就能完成从模型连接到第一次 Agent 运行。',
    studioBody:
      '当前公开主线已经具备用户 Provider、连接测试、Workspace 创建、Agent Builder、真实模型调用、持久化 Task/Run 记录和工作空间只读知识能力。',
    teamKicker: 'ONE RELIABLE AGENT FIRST',
    teamTitle: '先让一个 Agent 可靠工作，再按任务需要扩展角色。',
    teamBody:
      'OmniBase 已经定义清晰的角色语言，但生产多 Agent Runtime 仍然关闭。当前重点是让单 Agent 的职责、模型、Workspace、知识、预算和运行记录真正闭环。',
    roadmapKicker: 'HONEST ROADMAP',
    roadmapTitle: '我们只展示真实完成和真实正在建设的部分。',
    roadmapBody:
      '用户可用产品能力已经进入主线；Planner、Typed Executor、只读 Capability Gateway 和 Desktop 正在统一整合。生产 Runtime、自我改造和多 Agent 长循环仍保持关闭。',
    delivered: '已经具备',
    building: '正在建设',
    later: '后续阶段',
    ctaKicker: 'BUILD WITH US',
    ctaTitle: '从第一个 Workspace 和第一个 AI 员工开始。',
    ctaBody:
      'OmniBase 正在把 Agent 从一次性聊天人格变成有职位、有版本、有项目上下文和可审计运行记录的开源数字员工。欢迎试用、阅读源码或参与验证。',
    contribute: '参与共建',
    footer: 'AI workers, repairable by design.',
  },
  en: {
    nav: ['Product', 'Real workflow', 'Role design', 'Roadmap'],
    badge: 'Public Preview · A working Agent workbench',
    meta: 'Open source · Self-hosted · Personal models · Versioned agents',
    heroLead: 'Start with a workspace. Create and run your',
    heroAccent: 'first AI worker.',
    heroBody:
      'Connect your own OpenAI-compatible provider, create a workspace, define a worker and its version, then start a real run. OmniBase keeps models, knowledge, tasks, run records and safety boundaries in one maintainable workbench.',
    github: 'View source on GitHub',
    explore: 'See the real product loop',
    principles: ['Apache-2.0', 'Self-hosted', 'Personal model', 'Source-repairable'],
    metrics: [
      ['Personal provider', 'Manage and test your own model API'],
      ['Workspace', 'Keep project context isolated'],
      ['Agent Builder', 'Create versioned digital workers'],
      ['Task → Run', 'Keep every shift observable and auditable'],
    ],
    foundationKicker: 'A WORKBENCH, NOT A CHAT WRAPPER',
    foundationTitle: 'An AI workbench first. A data and automation platform underneath.',
    foundationBody:
      'Users begin with models, workspaces and agents. Databases, RAG, task ledgers, leases, audits and an AI-readable maintainer map provide the durable foundation underneath.',
    studioKicker: 'A REAL PRODUCT LOOP',
    studioTitle: 'Go from connecting a model to your first Agent run today.',
    studioBody:
      'The public main line already includes personal providers, connection tests, workspace creation, Agent Builder, real model invocation, durable Task/Run records and workspace-scoped read-only knowledge.',
    teamKicker: 'ONE RELIABLE AGENT FIRST',
    teamTitle: 'Make one Agent reliable before expanding into a team.',
    teamBody:
      'OmniBase has a clear role language, but production multi-agent runtime remains disabled. The current priority is closing the loop around one Agent: role, model, workspace, knowledge, budget and durable run records.',
    roadmapKicker: 'HONEST ROADMAP',
    roadmapTitle: 'We show what is real, what is being built and what is not ready yet.',
    roadmapBody:
      'The usable product slice is already on main. Planner, typed execution, read-only Capability Gateway composition and Desktop are being consolidated. Production runtime, self-modification and long-running multi-agent loops remain disabled.',
    delivered: 'Available now',
    building: 'In development',
    later: 'Later phases',
    ctaKicker: 'BUILD WITH US',
    ctaTitle: 'Start with your first workspace and your first AI worker.',
    ctaBody:
      'OmniBase is turning disposable chat personas into open-source digital workers with roles, versions, project context and auditable run records. Try it, read the source or help us verify the boundaries.',
    contribute: 'Contribute',
    footer: 'AI workers, repairable by design.',
  },
} as const

const foundationCards = {
  zh: [
    [
      'PERSONAL MODEL GATEWAY',
      '使用并测试你自己的模型',
      '用户可以配置 OpenAI-compatible Provider、管理 API Key 并在保存前后进行连接测试。',
    ],
    [
      'WORKSPACE',
      '每个项目拥有自己的上下文',
      '成员、Agent、知识、任务和运行记录都绑定到 Workspace，而不是继续散落在聊天历史中。',
    ],
    [
      'AGENT REGISTRY + RUNS',
      '职位、版本与每次工作相互分离',
      'AgentDefinition、AgentVersion、Workspace 绑定和 Task/Run 账本让数字员工能够被创建、运行和审计。',
    ],
    [
      'GOVERNED KNOWLEDGE',
      '知识检索停留在 Workspace 边界内',
      'RAG、引用回链和工程预览中的只读 knowledge_search 服从租户、Workspace、预算、审计与能力边界。',
    ],
  ],
  en: [
    [
      'PERSONAL MODEL GATEWAY',
      'Bring and test your own model',
      'Configure an OpenAI-compatible provider, manage the API key and test connectivity before relying on it.',
    ],
    [
      'WORKSPACE',
      'Give every project its own context',
      'Members, agents, knowledge, tasks and run records belong to a workspace instead of disappearing into chat history.',
    ],
    [
      'AGENT REGISTRY + RUNS',
      'Separate a role, its version and each shift of work',
      'AgentDefinition, AgentVersion, workspace bindings and the Task/Run ledger make workers creatable, runnable and auditable.',
    ],
    [
      'GOVERNED KNOWLEDGE',
      'Keep retrieval inside workspace boundaries',
      'RAG, citations and engineering-preview read-only knowledge_search remain tenant-, workspace-, budget-, audit- and capability-bound.',
    ],
  ],
} as const

const roleCards = {
  zh: [
    ['Workspace Steward', '理解目标、决定是否组队、分配任务并汇总结果。'],
    ['Explorer', '定位入口、研究方案、提供证据和未知清单。'],
    ['Builder', '实现代码、产出 Commit 与结构化交接。'],
    ['Verifier', '独立检查实现与证据，给出明确验收决定。'],
    ['Knowledge Curator', '把任务经验蒸馏成用户、项目和角色记忆。'],
    ['Operator', '负责部署、恢复与真实运行世界的变化。'],
  ],
  en: [
    [
      'Workspace Steward',
      'Understands goals, forms teams only when needed and integrates results.',
    ],
    [
      'Explorer',
      'Finds entrypoints, researches options and returns evidence with explicit unknowns.',
    ],
    ['Builder', 'Implements changes and hands off commits and structured artifacts.'],
    ['Verifier', 'Independently reviews implementation and evidence before acceptance.'],
    ['Knowledge Curator', 'Distills task experience into user, workspace and role memory.'],
    ['Operator', 'Owns deployment, recovery and changes to the running world.'],
  ],
} as const

const roadmap = {
  zh: {
    delivered: [
      '用户 Profile、个人模型 Provider、API Key 管理与连接测试',
      'Workspace 创建、Agent Builder、AgentDefinition 与版本化员工',
      '真实无工具单 Agent 模型调用与持久化 Task / Run 记录',
      '自托管 RAG、引用回链、恢复 Runbook 与 AI 可读维护者地图',
    ],
    building: [
      'Planner Proposal、Typed Executor 与正式 Builder 主线收口',
      'Capability Gateway 只读 knowledge_search 工程组合',
      'Lite / Local Desktop 启动、诊断与端口检查',
      '原生 Skill 合同、版本边界与工程验证',
    ],
    later: [
      '可安装的 Skill 持久化与 Runtime',
      '隔离 worktree 中的 Self-Development Alpha',
      '按任务与预算展开的多 Agent Runtime',
      'P34.7 通过后的 Hardened Production Runtime',
    ],
  },
  en: {
    delivered: [
      'Profiles, personal model providers, API-key management and connection tests',
      'Workspace creation, Agent Builder, AgentDefinition and versioned workers',
      'Real tool-free single-Agent model calls with durable Task/Run records',
      'Self-hosted RAG, citations, recovery runbooks and an AI-readable maintainer map',
    ],
    building: [
      'Planner Proposal, typed execution and formal Builder consolidation',
      'Read-only knowledge_search through the Capability Gateway',
      'Lite / Local Desktop startup, diagnostics and port checks',
      'Native Skill contracts, version boundaries and engineering verification',
    ],
    later: [
      'Installable Skill persistence and runtime',
      'Self-Development Alpha inside isolated worktrees',
      'Task- and budget-aware multi-agent runtime',
      'Hardened production runtime after P34.7 admission',
    ],
  },
} as const

export function PublicPreviewClient() {
  const [locale, setLocale] = useState<Locale>('zh')
  const t = copy[locale]

  return (
    <div className={cn(styles.page, 'min-h-screen overflow-hidden text-foreground')}>
      <div className={cn(styles.grid, 'pointer-events-none absolute inset-x-0 top-0 h-[62rem]')} />

      <header className="relative z-40 border-b border-border/50 bg-background/75 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-8">
          <a href="#top" className="group flex items-center gap-3" aria-label="OmniBase home">
            <BrandMark className="h-9 w-9 transition-transform duration-500 group-hover:scale-105" />
            <span>
              <span className="block text-sm font-semibold leading-none tracking-tight">
                OmniBase
              </span>
              <span className="mt-1 block font-mono text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
                AI Workbench
              </span>
            </span>
          </a>

          <nav
            className="hidden items-center gap-7 text-sm text-muted-foreground lg:flex"
            aria-label="Page navigation"
          >
            {['foundation', 'studio', 'team', 'roadmap'].map((id, index) => (
              <a key={id} className="transition-colors hover:text-foreground" href={`#${id}`}>
                {t.nav[index]}
              </a>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setLocale(locale === 'zh' ? 'en' : 'zh')}
              aria-label="Switch language"
            >
              <Languages className="h-4 w-4" />
              {locale === 'zh' ? 'EN' : '中文'}
            </Button>
            <ThemeToggle />
            <Button asChild size="sm" className="hidden sm:inline-flex">
              <a href={repositoryUrl} target="_blank" rel="noreferrer">
                <Github className="h-4 w-4" /> GitHub
              </a>
            </Button>
          </div>
        </div>
      </header>

      <main id="top" className="relative">
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
                {t.badge}
              </Badge>
              <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
                {t.meta}
              </span>
            </div>

            <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.045em] sm:text-5xl lg:text-[4.2rem]">
              {t.heroLead}{' '}
              <span className="bg-gradient-to-r from-cyan-500 via-blue-500 to-violet-500 bg-clip-text text-transparent">
                {t.heroAccent}
              </span>
            </h1>
            <p className="mt-7 max-w-2xl text-pretty text-base leading-8 text-muted-foreground sm:text-lg">
              {t.heroBody}
            </p>

            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Button asChild size="lg" className="group h-12 rounded-xl px-6">
                <a href={repositoryUrl} target="_blank" rel="noreferrer">
                  <Github className="h-4 w-4" /> {t.github}
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                </a>
              </Button>
              <Button asChild variant="outline" size="lg" className="h-12 rounded-xl px-6">
                <a href="#studio">
                  {t.explore}
                  <ChevronRight className="h-4 w-4" />
                </a>
              </Button>
            </div>

            <div className="mt-9 flex flex-wrap gap-x-6 gap-y-3 text-xs text-muted-foreground">
              {t.principles.map((item) => (
                <span key={item} className="flex items-center gap-2">
                  <Check className="h-3.5 w-3.5 text-emerald-500" />
                  {item}
                </span>
              ))}
            </div>
          </div>
          <AgentBlueprint locale={locale} />
        </section>

        <section className="border-y border-border/60 bg-muted/20">
          <div className="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-y divide-border/60 px-5 sm:px-8 lg:grid-cols-4 lg:divide-y-0">
            {t.metrics.map(([value, label]) => (
              <Metric key={value} value={value} label={label} />
            ))}
          </div>
        </section>

        <section id="foundation" className="mx-auto max-w-7xl px-5 py-24 sm:px-8 sm:py-32">
          <SectionHeading
            kicker={t.foundationKicker}
            title={t.foundationTitle}
            description={t.foundationBody}
          />
          <div className="mt-12 grid gap-4 md:grid-cols-2">
            {foundationCards[locale].map(([eyebrow, title, description], index) => {
              const icons = [Database, Boxes, ShieldCheck, Map]
              const Icon = icons[index] ?? Database
              return (
                <article
                  key={title}
                  className="group relative overflow-hidden rounded-2xl border border-border/70 bg-card/70 p-7 shadow-sm backdrop-blur-sm transition-all duration-300 hover:-translate-y-1 hover:border-cyan-500/25 hover:shadow-xl sm:p-8"
                >
                  <div className="relative">
                    <div className="flex items-start justify-between">
                      <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-border/60 bg-background/75">
                        <Icon className="h-5 w-5 text-cyan-600 dark:text-cyan-300" />
                      </span>
                      <span className="font-mono text-[9px] tracking-[0.18em] text-muted-foreground">
                        {eyebrow}
                      </span>
                    </div>
                    <h3 className="mt-8 text-xl font-semibold tracking-tight">{title}</h3>
                    <p className="mt-3 max-w-xl text-sm leading-7 text-muted-foreground">
                      {description}
                    </p>
                  </div>
                </article>
              )
            })}
          </div>
        </section>

        <section id="studio" className="border-y border-border/60 bg-card/35 py-24 sm:py-32">
          <div className="mx-auto grid max-w-7xl gap-14 px-5 sm:px-8 lg:grid-cols-[.9fr_1.1fr] lg:items-center">
            <div>
              <SectionHeading
                kicker={t.studioKicker}
                title={t.studioTitle}
                description={t.studioBody}
              />
              <div className="mt-8 space-y-3">
                <StudioStep
                  icon={Sparkles}
                  title={locale === 'zh' ? '连接模型' : 'Connect a model'}
                  text={
                    locale === 'zh'
                      ? '添加自己的 OpenAI-compatible Provider，保存 API Key 并测试真实连接。'
                      : 'Add your OpenAI-compatible provider, save the API key and test the real connection.'
                  }
                />
                <StudioStep
                  icon={Wrench}
                  title={locale === 'zh' ? '创建 Workspace' : 'Create a workspace'}
                  text={
                    locale === 'zh'
                      ? '为项目建立独立成员、知识、Agent、任务与运行上下文。'
                      : 'Create isolated project context for members, knowledge, agents, tasks and runs.'
                  }
                />
                <StudioStep
                  icon={Play}
                  title={locale === 'zh' ? '创建 AI 员工' : 'Build an AI worker'}
                  text={
                    locale === 'zh'
                      ? '定义职位、系统指令、模型和版本，让 Agent 成为可管理的项目成员。'
                      : 'Define the role, system instructions, model and version so the Agent becomes a managed project member.'
                  }
                />
                <StudioStep
                  icon={Rocket}
                  title={locale === 'zh' ? '运行并检查' : 'Run and inspect'}
                  text={
                    locale === 'zh'
                      ? '发起真实模型调用，并在 Task、Run、预算、知识结果和审计记录中检查这次工作。'
                      : 'Start a real model call and inspect the shift through Task, Run, budget, knowledge results and audit records.'
                  }
                />
              </div>
            </div>
            <AgentLifecycle locale={locale} />
          </div>
        </section>

        <section id="team" className="mx-auto max-w-7xl px-5 py-24 sm:px-8 sm:py-32">
          <SectionHeading
            kicker={t.teamKicker}
            title={t.teamTitle}
            description={t.teamBody}
            centered
          />
          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {roleCards[locale].map(([title, description], index) => {
              const icons = [Orbit, BookOpenCheck, Code2, BadgeCheck, BrainCircuit, Wrench]
              const Icon = icons[index] ?? Orbit
              return (
                <article
                  key={title}
                  className="rounded-2xl border border-border/70 bg-card/70 p-6 shadow-sm"
                >
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-500">
                    <Icon className="h-5 w-5" />
                  </span>
                  <h3 className="mt-5 font-semibold">{title}</h3>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
                </article>
              )
            })}
          </div>
        </section>

        <section id="roadmap" className="border-y border-border/60 bg-muted/20 py-24 sm:py-32">
          <div className="mx-auto max-w-7xl px-5 sm:px-8">
            <SectionHeading
              kicker={t.roadmapKicker}
              title={t.roadmapTitle}
              description={t.roadmapBody}
            />
            <div className="mt-12 grid gap-5 lg:grid-cols-3">
              <StatusColumn
                tone="ready"
                icon={Check}
                title={t.delivered}
                items={roadmap[locale].delivered}
              />
              <StatusColumn
                tone="building"
                icon={Workflow}
                title={t.building}
                items={roadmap[locale].building}
              />
              <StatusColumn
                tone="frozen"
                icon={Layers3}
                title={t.later}
                items={roadmap[locale].later}
              />
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-5 py-24 sm:px-8 sm:py-32">
          <div className="relative overflow-hidden rounded-3xl border border-cyan-500/20 bg-slate-950 px-6 py-14 text-white shadow-2xl sm:px-12 lg:px-16">
            <div className="pointer-events-none absolute -right-32 -top-48 h-96 w-96 rounded-full bg-cyan-500/20 blur-3xl" />
            <div className="relative grid gap-9 lg:grid-cols-[1fr_auto] lg:items-end">
              <div>
                <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-300">
                  {t.ctaKicker}
                </div>
                <h2 className="mt-5 max-w-3xl text-3xl font-semibold tracking-[-0.035em] sm:text-5xl">
                  {t.ctaTitle}
                </h2>
                <p className="mt-5 max-w-2xl text-base leading-7 text-slate-400">{t.ctaBody}</p>
              </div>
              <div className="flex flex-col gap-3">
                <Button
                  asChild
                  size="lg"
                  className="h-12 rounded-xl bg-white text-slate-950 hover:bg-slate-200"
                >
                  <a href={repositoryUrl} target="_blank" rel="noreferrer">
                    <Github className="h-4 w-4" />
                    GitHub
                  </a>
                </Button>
                <Button
                  asChild
                  variant="outline"
                  size="lg"
                  className="h-12 rounded-xl border-slate-700 bg-transparent text-white hover:bg-slate-900 hover:text-white"
                >
                  <a href={`${repositoryUrl}/issues`} target="_blank" rel="noreferrer">
                    <GitPullRequest className="h-4 w-4" />
                    {t.contribute}
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
            <BrandMark className="h-8 w-8" glow={false} />
            <span>
              <strong className="font-medium text-foreground">OmniBase</strong>
              <span className="ml-2">{t.footer}</span>
            </span>
          </div>
          <div className="flex gap-6">
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

function AgentBlueprint({ locale }: { locale: Locale }) {
  const rows =
    locale === 'zh'
      ? [
          ['AgentDefinition', '职位与职责'],
          ['AgentVersion', '工作手册与 Skills'],
          ['WorkspaceBinding', '项目任命与记忆'],
          ['AgentRun', '一次具体工作班次'],
        ]
      : [
          ['AgentDefinition', 'Role and responsibilities'],
          ['AgentVersion', 'Handbook and skills'],
          ['WorkspaceBinding', 'Project appointment and memory'],
          ['AgentRun', 'One concrete shift of work'],
        ]
  return (
    <div className={cn(styles.floatSlow, 'relative mx-auto w-full max-w-xl')}>
      <div className="absolute -inset-8 rounded-[3rem] bg-gradient-to-br from-cyan-500/10 via-transparent to-violet-500/10 blur-2xl" />
      <div
        className={cn(
          styles.heroGlow,
          'relative overflow-hidden rounded-2xl border border-white/10 bg-slate-950 text-slate-100',
        )}
      >
        <div className="flex h-11 items-center justify-between border-b border-white/10 px-4">
          <div className="flex gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-rose-400/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-400/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/80" />
          </div>
          <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-slate-500">
            agent / blueprint
          </span>
          <Bot className="h-4 w-4 text-cyan-300" />
        </div>
        <div className="p-6 sm:p-7">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-slate-500">
                {locale === 'zh' ? '用户想法' : 'User intent'}
              </p>
              <p className="mt-1 text-sm font-medium">
                {locale === 'zh'
                  ? '“创建一个前端审美检查员”'
                  : '“Create a frontend visual reviewer”'}
              </p>
            </div>
            <span className="rounded-full border border-cyan-400/20 bg-cyan-400/10 px-2.5 py-1 font-mono text-[9px] uppercase tracking-wider text-cyan-300">
              compiled
            </span>
          </div>
          <div className="space-y-3">
            {rows.map(([key, value], index) => (
              <div
                key={key}
                className="grid grid-cols-[auto_1fr] gap-3 rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-3 sm:grid-cols-[auto_1fr_auto] sm:items-center"
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-400/10 font-mono text-[9px] text-indigo-300">
                  0{index + 1}
                </span>
                <span className="font-mono text-[10px] text-slate-300">{key}</span>
                <span className="col-start-2 text-[10px] text-slate-500 sm:col-auto">{value}</span>
              </div>
            ))}
          </div>
          <div className="mt-5 flex items-center gap-3 rounded-xl border border-emerald-400/15 bg-emerald-400/[0.06] p-4">
            <BadgeCheck className="h-5 w-5 text-emerald-300" />
            <div>
              <p className="text-xs font-medium">
                {locale === 'zh' ? '可读、可测试、可回滚' : 'Readable, testable, reversible'}
              </p>
              <p className="mt-1 text-[10px] text-slate-500">draft → trial → sealed → appointed</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function AgentLifecycle({ locale }: { locale: Locale }) {
  const labels =
    locale === 'zh'
      ? [
          ['职位', '它负责什么'],
          ['版本', '它怎样工作'],
          ['任命', '它属于哪个项目'],
          ['班次', '这一次做什么'],
        ]
      : [
          ['Role', 'What it owns'],
          ['Version', 'How it works'],
          ['Appointment', 'Which project it serves'],
          ['Run', 'What it does now'],
        ]
  return (
    <div className="rounded-3xl border border-border/70 bg-card/75 p-6 shadow-xl sm:p-8">
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-500/10 text-violet-500">
          <Users className="h-5 w-5" />
        </span>
        <div>
          <p className="font-semibold">{locale === 'zh' ? 'Agent 生命周期' : 'Agent lifecycle'}</p>
          <p className="text-xs text-muted-foreground">Definition → Version → Binding → Run</p>
        </div>
      </div>
      <div className="mt-8 space-y-1">
        {labels.map(([title, body], index) => (
          <div key={title} className="relative flex gap-4 pb-7 last:pb-0">
            <div className="absolute bottom-0 left-[17px] top-9 w-px bg-border last:hidden" />
            <span className="relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary font-mono text-[10px] font-bold text-primary-foreground">
              0{index + 1}
            </span>
            <div className="pt-1">
              <p className="text-sm font-semibold">{title}</p>
              <p className="mt-1 text-xs text-muted-foreground">{body}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function StudioStep({
  icon: Icon,
  title,
  text,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  text: string
}) {
  return (
    <div className="flex gap-3 rounded-xl border border-border/65 bg-background/60 p-4">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-cyan-500/10 text-cyan-600 dark:text-cyan-300">
        <Icon className="h-4 w-4" />
      </span>
      <div>
        <p className="text-sm font-semibold">{title}</p>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">{text}</p>
      </div>
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
    <div className={cn('max-w-3xl', centered && 'mx-auto text-center')}>
      <p className="font-mono text-[10px] font-medium uppercase tracking-[0.23em] text-cyan-600 dark:text-cyan-300">
        {kicker}
      </p>
      <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">{title}</h2>
      <p className="mt-4 text-base leading-7 text-muted-foreground">{description}</p>
    </div>
  )
}

function StatusColumn({
  tone,
  icon: Icon,
  title,
  items,
}: {
  tone: 'ready' | 'building' | 'frozen'
  icon: React.ComponentType<{ className?: string }>
  title: string
  items: readonly string[]
}) {
  const color = {
    ready: ['border-emerald-500/25', 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300'],
    building: ['border-cyan-500/25', 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-300'],
    frozen: ['border-amber-500/25', 'bg-amber-500/10 text-amber-600 dark:text-amber-300'],
  }[tone]
  return (
    <article className={cn('rounded-2xl border bg-card/75 p-6 shadow-sm', color[0])}>
      <span className={cn('flex h-10 w-10 items-center justify-center rounded-xl', color[1])}>
        <Icon className="h-5 w-5" />
      </span>
      <h3 className="mt-6 text-xl font-semibold">{title}</h3>
      <ul className="mt-5 space-y-3.5">
        {items.map((item) => (
          <li key={item} className="flex items-start gap-3 text-sm leading-6 text-muted-foreground">
            <CircleDot className="mt-1.5 h-3 w-3 shrink-0 text-cyan-500" />
            {item}
          </li>
        ))}
      </ul>
    </article>
  )
}

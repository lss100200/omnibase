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
    nav: ['基础底座', 'Agent Studio', '团队协作', '路线图'],
    badge: 'Public Preview · Agent-native workbench',
    meta: '开源 · 自托管 · 可版本化 · AI 可维护',
    heroLead: '让每个人都能创造、训练并长期培养自己的',
    heroAccent: 'AI 员工。',
    heroBody:
      'OmniBase 把知识、RAG、工作空间、沙箱与 Agent 组织在同一张工作台上。用户创造的不是一段一次性 Prompt，而是一个可以试用、评测、发布、安装、协作和持续成长的数字职位。',
    github: '查看公开源码',
    explore: '了解 Agent Studio',
    principles: ['Apache-2.0', 'Local-first', 'Versioned agents', 'Source-repairable'],
    metrics: [
      ['Natural language', '用一句话创建 Agent'],
      ['Role → Version', '职位与工作手册分离'],
      ['Workspace-bound', '按项目任命与积累记忆'],
      ['Human-readable', '所有行为都有可读合同'],
    ],
    foundationKicker: 'A WORKBENCH, NOT A CHAT WRAPPER',
    foundationTitle: '先有可持续的工作空间，再有真正的 Agent。',
    foundationBody:
      'OmniBase 不把多模型聊天包装成 Agent 平台。数据库、知识、项目上下文、验证证据和维护者地图共同构成 Agent 可以长期工作的底座。',
    studioKicker: 'CREATE YOUR OWN AI WORKER',
    studioTitle: '用自然语言创建一个职位，用版本管理它的成长。',
    studioBody:
      '描述它负责什么、如何完成、与谁合作以及应该交付什么。OmniBase 把自然语言编译成结构化 AgentDefinition 与 AgentVersion，再通过试用任务验证它。',
    teamKicker: 'LOGICAL TEAM · ADAPTIVE COST',
    teamTitle: '角色可以很多，真正启动的模型应该尽量少。',
    teamBody:
      '简单任务由主 Agent 一人完成；复杂任务才展开 Explorer、Builder、Verifier、Curator 与 Operator。逻辑分工清晰，但不会为了“多 Agent”而浪费上下文和余额。',
    roadmapKicker: 'HONEST ROADMAP',
    roadmapTitle: '我们只展示真实完成和真实正在建设的部分。',
    roadmapBody:
      '基础设施已经进入可公开维护阶段；Agent 角色、版本和工作空间任命合同正在落地。自主 Runtime 与多 Agent 长循环不会被提前宣传成可用能力。',
    delivered: '已经具备',
    building: '正在建设',
    later: '后续阶段',
    ctaKicker: 'BUILD WITH US',
    ctaTitle: '让 AI 帮人创造，而不是让人不断为 AI 的错误买单。',
    ctaBody:
      'OmniBase 正在把 Agent 从聊天人格变成可理解、可测试、可维护、可成长的开源数字员工。欢迎阅读源码、提出角色设计，或者带着你的第一个 Agent 想法加入。',
    contribute: '参与共建',
    footer: 'AI workers, repairable by design.',
  },
  en: {
    nav: ['Foundation', 'Agent Studio', 'Teamwork', 'Roadmap'],
    badge: 'Public Preview · Agent-native workbench',
    meta: 'Open source · Self-hosted · Versioned · AI-maintainable',
    heroLead: 'Create, train and grow AI workers that remain',
    heroAccent: 'understandable.',
    heroBody:
      'OmniBase brings knowledge, RAG, workspaces, sandboxes and agent organization into one workbench. You are not creating a disposable prompt. You are creating a digital role that can be tested, evaluated, published, installed, collaborated with and improved over time.',
    github: 'View source on GitHub',
    explore: 'Explore Agent Studio',
    principles: ['Apache-2.0', 'Local-first', 'Versioned agents', 'Source-repairable'],
    metrics: [
      ['Natural language', 'Create an agent in one sentence'],
      ['Role → Version', 'Separate jobs from handbooks'],
      ['Workspace-bound', 'Appointments and memory per project'],
      ['Human-readable', 'Every behavior has a visible contract'],
    ],
    foundationKicker: 'A WORKBENCH, NOT A CHAT WRAPPER',
    foundationTitle: 'Sustainable workspaces come before autonomous agents.',
    foundationBody:
      'OmniBase does not rebrand multi-model chat as an agent platform. Databases, knowledge, project context, verification evidence and an AI-readable maintainer map form the foundation where agents can work for the long term.',
    studioKicker: 'CREATE YOUR OWN AI WORKER',
    studioTitle: 'Describe a role in natural language. Manage its growth as software.',
    studioBody:
      'Define what it owns, how it finishes work, who it collaborates with and what it must deliver. OmniBase compiles that intent into a structured AgentDefinition and AgentVersion, then validates it with trial tasks.',
    teamKicker: 'LOGICAL TEAM · ADAPTIVE COST',
    teamTitle: 'Many roles can exist without launching many models.',
    teamBody:
      'A primary agent handles simple work alone. Explorer, Builder, Verifier, Curator and Operator roles unfold only when the task benefits from specialization or independent review. Clear responsibilities without agent theatre.',
    roadmapKicker: 'HONEST ROADMAP',
    roadmapTitle: 'We show what is real, what is being built and what is not ready yet.',
    roadmapBody:
      'The infrastructure is becoming publicly maintainable. Agent roles, versions and workspace appointments are being formalized. Autonomous runtime and long-running multi-agent loops are not advertised before they exist.',
    delivered: 'Available now',
    building: 'In development',
    later: 'Later phases',
    ctaKicker: 'BUILD WITH US',
    ctaTitle: 'Let AI help people create without making people endlessly pay for AI mistakes.',
    ctaBody:
      'OmniBase is turning agents from chat personas into open-source digital workers that can be understood, tested, maintained and improved. Read the source, challenge the role design or bring your first agent idea.',
    contribute: 'Contribute',
    footer: 'AI workers, repairable by design.',
  },
} as const

const foundationCards = {
  zh: [
    [
      'DATABASE + RAG',
      '让知识成为 Agent 的长期工作资产',
      '关系数据、向量索引、混合检索、引用回链与用户知识库共同组成可查询的项目记忆。',
    ],
    [
      'WORKSPACE',
      '每个项目都有独立上下文',
      '角色、知识、成员、任务、产物与长期记忆都绑定到 Workspace，而不是散落在聊天记录中。',
    ],
    [
      'CAPABILITY GATEWAY',
      '能力由工作合同明确表达',
      'Agent 使用的是逻辑能力、Skills 与工具合同；运行结果可以被记录、验证和复现。',
    ],
    [
      'AI MAINTAINABILITY',
      '让下一位 AI 也能继续维护',
      '机器可读维护者地图保留模块入口、依赖、验证命令和恢复路径，让更换模型不等于失去工程记忆。',
    ],
  ],
  en: [
    [
      'DATABASE + RAG',
      'Turn knowledge into a long-lived working asset',
      'Relational data, vector indexes, hybrid retrieval, citations and user knowledge form queryable project memory.',
    ],
    [
      'WORKSPACE',
      'Give every project its own context',
      'Roles, knowledge, members, tasks, artifacts and long-term memory belong to a workspace instead of disappearing into chat history.',
    ],
    [
      'CAPABILITY GATEWAY',
      'Express abilities as explicit work contracts',
      'Agents use logical capabilities, skills and tool contracts. Work can be recorded, verified and reproduced.',
    ],
    [
      'AI MAINTAINABILITY',
      'Make the project repairable by the next AI',
      'An AI-readable maintainer map preserves entrypoints, dependencies, verification commands and recovery paths across model changes.',
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
      '自托管知识库与生产 RAG 主链路',
      'Workspace 控制面与沙箱工程基础',
      '受控数据能力、SDK 合同与恢复 Runbook',
      'AI 可读维护者地图与公开源码',
    ],
    building: [
      'AgentDefinition / AgentVersion / WorkspaceBinding',
      '用户创建 Agent 的对话式 Agent Studio',
      '角色分工、结构化交接与团队模板',
      '分层长期记忆与原生 Skills',
    ],
    later: [
      'Agent Run 与任务图',
      '单 Agent Planner / Executor',
      'Model / Tool / Memory / Skill Runtime',
      '按预算展开的多 Agent 编排',
    ],
  },
  en: {
    delivered: [
      'Self-hosted knowledge and production RAG path',
      'Workspace control plane and sandbox engineering foundation',
      'Controlled data capabilities, SDK contracts and recovery runbooks',
      'AI-readable maintainer map and public source',
    ],
    building: [
      'AgentDefinition / AgentVersion / WorkspaceBinding',
      'Conversational Agent Studio for user-created workers',
      'Role division, structured handoffs and team templates',
      'Layered long-term memory and native skills',
    ],
    later: [
      'Agent runs and task graphs',
      'Single-agent planner and executor',
      'Model / tool / memory / skill runtime',
      'Budget-aware multi-agent orchestration',
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
                  title={locale === 'zh' ? '描述' : 'Describe'}
                  text={
                    locale === 'zh'
                      ? '用一句自然语言说明你想创造什么样的数字员工。'
                      : 'Explain the digital worker you want in one natural-language request.'
                  }
                />
                <StudioStep
                  icon={Wrench}
                  title={locale === 'zh' ? '编译' : 'Compile'}
                  text={
                    locale === 'zh'
                      ? '生成可读的职责、输入输出、Skills、模型与协作合同。'
                      : 'Generate visible responsibilities, I/O, skills, model and collaboration contracts.'
                  }
                />
                <StudioStep
                  icon={Play}
                  title={locale === 'zh' ? '试用' : 'Trial'}
                  text={
                    locale === 'zh'
                      ? '在测试任务中观察行为、成本、工具使用和交付质量。'
                      : 'Observe behavior, cost, tool use and delivery quality on trial tasks.'
                  }
                />
                <StudioStep
                  icon={Rocket}
                  title={locale === 'zh' ? '发布' : 'Publish'}
                  text={
                    locale === 'zh'
                      ? '封存版本并安装到 Workspace；后续成长产生新版本而不覆盖历史。'
                      : 'Seal a version and appoint it to a workspace; improvements become new versions.'
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

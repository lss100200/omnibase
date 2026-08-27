'use client'

import { useState } from 'react'
import {
  ArrowRight,
  BadgeCheck,
  BookOpenCheck,
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
    nav: ['个人工作台', '工程闭环', '十角色团队', '路线图'],
    badge: 'P6.9 R0 · 工程验收通过',
    meta: '开源 · 自托管 · 父 Agent 指挥 · 宿主验证',
    heroLead: 'OmniBase',
    heroAccent: '个人 AI 员工团队。',
    heroBody:
      'Owner 开启团队模式后，父 Agent 可以面向九名固定专业员工提出受限 Proposal。宿主验证身份、预算、依赖和并发，再执行串行、并行或混合 wave；协作、取消、恢复与最终结果都留在一个可审计的本地边界内。',
    github: '查看公开源码',
    explore: '查看团队运行闭环',
    principles: ['Apache-2.0', 'Self-hosted', 'Parent-directed', 'Host-validated'],
    metrics: [
      ['P6.9 R0', '确定性 loopback 范围已通过工程验收'],
      ['1 + 9 roles', '父 Agent 动态编制，员工不能启动同伴'],
      ['Bounded waves', '宿主验证串行、并行与混合执行'],
      ['SQLite v9', '调用预留、父调用证明与成功闭包'],
    ],
    foundationKicker: 'A WORKBENCH, NOT A CHAT WRAPPER',
    foundationTitle: '它首先是个人工程工作台，然后才是数据库与自动化平台。',
    foundationBody:
      '用户从会话、文件树、模型和 Workspace 开始工作；RAG、任务账本、ChangeSet、审计和维护者地图在背后提供可持续的工程底座。',
    studioKicker: 'A REAL PRODUCT LOOP',
    studioTitle: '从模型连接、文件上下文到一次可审计任务。',
    studioBody:
      '当前源码包含个人 Provider、Workspace、文件树、会话连续性、Agent Builder、无工具 Provider 调用、持久 Task/Run、ChangeSet 审计与恢复日志。',
    teamKicker: 'PARENT-DIRECTED, HOST-VALIDATED',
    teamTitle: '父 Agent 组织工作，宿主掌握执行权。',
    teamBody:
      '父 Agent 从九个源码固定专业角色中选择员工，经结构化 Proposal 提出串行、并行或混合 wave。员工通过 Personal Team Blackboard 回报，不能直接启动同伴；企业 Planner、后台自治与无限 fan-out 仍然关闭。',
    roadmapKicker: 'HONEST ROADMAP',
    roadmapTitle: '我们只展示真实完成和真实正在建设的部分。',
    roadmapBody:
      '个人团队 R0 已通过工程验收，但工程证据不等于正式发布。付费 Provider、真人 Electron 窗口、Authenticode、EXE/MSI 与企业 Multi-Agent 仍未开放或未证明。',
    delivered: 'P6.9 R0 · 工程验收',
    building: 'Engineering Preview · 工程预览',
    later: 'Deferred · 延期能力',
    ctaKicker: 'BUILD WITH US',
    ctaTitle: '从一个 Workspace 和一项有边界的团队任务开始。',
    ctaBody:
      'OmniBase 把父 Agent 的团队判断转换为可验证的 Proposal、预算、调用证明、协作记录和运行结果。欢迎阅读源码、复核门禁或参与下一阶段规划。',
    contribute: '参与共建',
    footer: 'AI workers, repairable by design.',
  },
  en: {
    nav: ['Product', 'Real workflow', 'Role design', 'Roadmap'],
    badge: 'P6.9 R0 · Engineering accepted',
    meta: 'Open source · Self-hosted · Parent-directed · Host-validated',
    heroLead: 'OmniBase',
    heroAccent: 'Personal AI team.',
    heroBody:
      'After the Owner enables team mode, the parent Agent can propose work for nine fixed specialists. The host validates identity, budget, dependencies and concurrency before running serial, parallel or mixed waves; collaboration, cancellation, recovery and final results stay inside one auditable local boundary.',
    github: 'View source on GitHub',
    explore: 'See the team execution loop',
    principles: ['Apache-2.0', 'Self-hosted', 'Parent-directed', 'Host-validated'],
    metrics: [
      ['P6.9 R0', 'Engineering-accepted for the deterministic loopback scope'],
      ['1 + 9 roles', 'Parent staffs dynamically; specialists cannot launch peers'],
      ['Bounded waves', 'Host-validated serial, parallel and mixed execution'],
      ['SQLite v9', 'Call reservations, parent proof and success closure'],
    ],
    foundationKicker: 'A WORKBENCH, NOT A CHAT WRAPPER',
    foundationTitle: 'A personal engineering workbench first. A data platform underneath.',
    foundationBody:
      'Users begin with conversations, files, models and workspaces. RAG, task ledgers, ChangeSets, audits and an AI-readable maintainer map provide the durable foundation underneath.',
    studioKicker: 'A REAL PRODUCT LOOP',
    studioTitle: 'Go from model connection and file context to an auditable task.',
    studioBody:
      'The source includes personal providers, workspaces, a file tree, conversation continuity, Agent Builder, tool-free Provider calls, durable Task/Run records, ChangeSet review and recovery journals.',
    teamKicker: 'PARENT-DIRECTED, HOST-VALIDATED',
    teamTitle: 'The parent Agent organizes work. The host owns execution.',
    teamBody:
      'The parent selects from nine source-owned specialist roles and proposes serial, parallel or mixed waves through a strict contract. Specialists report through the Personal Team Blackboard and cannot launch peers; enterprise Planner, background autonomy and unbounded fan-out remain disabled.',
    roadmapKicker: 'HONEST ROADMAP',
    roadmapTitle: 'We show what is real, what is being built and what is not ready yet.',
    roadmapBody:
      'Personal-team R0 passed engineering acceptance, but engineering evidence is not a release. Paid providers, a live-human Electron window, Authenticode, EXE/MSI packaging and enterprise Multi-Agent remain unproven or disabled.',
    delivered: 'P6.9 R0 · Engineering accepted',
    building: 'Engineering preview',
    later: 'Deferred',
    ctaKicker: 'BUILD WITH US',
    ctaTitle: 'Start with one workspace and one bounded team task.',
    ctaBody:
      'OmniBase turns the parent Agent’s team decisions into validated Proposals, budgets, call proofs, collaboration records and durable outcomes. Read the source, inspect the gates or help shape the next phase.',
    contribute: 'Contribute',
    footer: 'AI workers, repairable by design.',
  },
} as const

const foundationCards = {
  zh: [
    [
      'MODEL-NAME-FIRST ROUTING',
      '根据用户填写的模型名称采用保守档案',
      'DeepSeek、GPT、GLM、Claude 与 Kimi 使用各自的提示和上下文策略；中转站地址不会覆盖明确模型名，也不会伪装未验证的原生能力。',
    ],
    [
      'FILES + CONTINUITY',
      '把文件、会话和任务留在同一个 Workspace',
      'Owner 授权的文件树、有限会话历史、持久 Task/Run 和浏览器本地恢复日志共同维护项目连续性。',
    ],
    [
      'CHANGESET REVIEW',
      '每次文件修改都留下 Before / After',
      '文本 ChangeSet 绑定 Workspace、Task 与 invocation；写入前检查快照，写入后验证摘要，漂移或冲突时拒绝回滚。',
    ],
    [
      'NATIVE SKILLS + MCP PREVIEW',
      '能力扩展保持有界并且诚实',
      '15 个第一方 instruction-only Skills 可安装/停用；6 个本地只读 MCP 工具需独立手工启动，尚未接入 Agent Alpha。',
    ],
  ],
  en: [
    [
      'MODEL-NAME-FIRST ROUTING',
      'Apply a conservative profile from the user-entered model name',
      'DeepSeek, GPT, GLM, Claude and Kimi receive family-aware prompt and context guidance. A relay URL cannot override a recognized name or prove native features.',
    ],
    [
      'FILES + CONTINUITY',
      'Keep files, conversations and work in one workspace',
      'Owner-authorized file trees, bounded conversation history, durable Task/Run records and a browser-local recovery journal preserve project continuity.',
    ],
    [
      'CHANGESET REVIEW',
      'Keep Before / After evidence for file changes',
      'Text ChangeSets bind workspace, task and invocation. Writes use snapshot checks and post-write digests; drift or overlap blocks rollback.',
    ],
    [
      'NATIVE SKILLS + MCP PREVIEW',
      'Extend capability without overstating authority',
      'Fifteen first-party instruction-only Skills can be installed or disabled. Six local read-only MCP tools launch separately and are not connected to Agent Alpha.',
    ],
  ],
} as const

const roleCards = {
  zh: [
    ['父 Agent', '默认活动；理解目标、保持会话连续性并向唯一 Owner 汇报。'],
    ['产品经理', '澄清需求、范围、优先级、验收标准与用户路径。'],
    ['UI/UX 设计师', '负责信息架构、交互、视觉系统与可访问性。'],
    ['前端工程师', '负责 Web 工作台、状态管理、客户端契约与交互性能。'],
    ['后端应用工程师', '负责 API、服务生命周期、幂等和事务边界。'],
    ['数据与存储工程师', '负责数据模型、SQL、迁移、索引、备份与恢复边界。'],
    ['安全架构师', '负责威胁模型、权限、秘密、审计、隔离与 fail-closed 设计。'],
    ['测试工程师', '负责验收路径、回归、攻击用例与可复现证据。'],
    ['运维与发布工程师', '负责构建、CI、发布、监控、备份和恢复演练。'],
    ['技术文档工程师', '负责架构、交接、运行手册、用户说明与证据索引。'],
  ],
  en: [
    ['Parent Agent', 'Active by default; maintains continuity and reports to the sole Owner.'],
    ['Product Manager', 'Clarifies scope, priority, acceptance criteria and user journeys.'],
    [
      'UI/UX Designer',
      'Owns information architecture, interaction, visual systems and accessibility.',
    ],
    ['Frontend Engineer', 'Owns the web workbench, client contracts, state and interaction.'],
    [
      'Backend Application Engineer',
      'Owns APIs, service lifecycles, idempotency and transactions.',
    ],
    [
      'Data and Storage Engineer',
      'Owns data models, SQL, migrations, indexes, backup and recovery bounds.',
    ],
    [
      'Security Architect',
      'Owns threat models, authority, secrets, audits and fail-closed design.',
    ],
    ['QA Engineer', 'Owns acceptance paths, regressions, attack cases and reproducible evidence.'],
    [
      'Operations and Release Engineer',
      'Owns builds, CI, release, monitoring, backup and recovery drills.',
    ],
    [
      'Technical Documentation Engineer',
      'Owns architecture, handover, runbooks and evidence indexes.',
    ],
  ],
} as const

const roadmap = {
  zh: {
    delivered: [
      'P6.9 个人团队 R0：确定性 loopback 范围通过工程验收',
      '父 Agent Proposal、宿主验证、受控 wave、黑板协作与 Stop / 恢复',
      '用户 Profile、个人模型 Provider、API Key 管理与连接测试',
      'Workspace、文件树、会话连续性、Agent Builder 与 1+9 固定角色',
      '无工具 Provider 调用、持久 Task / Run、引用与 ChangeSet 审计',
    ],
    building: [
      'DeepSeek、GPT、GLM、Claude、Kimi 的模型名优先适配档案',
      '6 个独立启动的只读 MCP 工具；未接入 Agent Alpha',
      'Windows Companion 的离线验证、安装规划与诊断体验',
      '源码级公网展示；实际 omnibase.chat 重新部署仍依赖预览主机',
    ],
    later: [
      '付费 Provider 流程与真人 Electron 窗口交互尚未证明',
      'Authenticode 未证明；EXE/MSI 重打包未批准',
      '企业 Planner / Multi-Agent 仍关闭，生产 MULTI_AGENT_ENABLED=false',
      '第三方 Skill 导入、Marketplace 与可执行 workflow/script Skill',
      'MCP 接入 Agent Runtime、写工具、网络与任意 shell/SQL',
    ],
  },
  en: {
    delivered: [
      'P6.9 personal-team R0 engineering acceptance for deterministic loopback',
      'Parent Proposals, host validation, bounded waves, blackboard collaboration and Stop/recovery',
      'Profiles, personal model providers, API-key management and connection tests',
      'Workspaces, file trees, conversation continuity, Agent Builder and 1+9 fixed roles',
      'Tool-free Provider calls, durable Task/Run records, citations and ChangeSet review',
    ],
    building: [
      'Model-name-first profiles for DeepSeek, GPT, GLM, Claude and Kimi',
      'Six separately launched read-only MCP tools, not connected to Agent Alpha',
      'Offline verification, install planning and diagnostics in the Windows Companion',
      'Public-preview source updates; live omnibase.chat still depends on the preview host',
    ],
    later: [
      'Paid Provider journeys and a live-human Electron window remain unproven',
      'Authenticode is unproven; EXE/MSI repackaging is not approved',
      'Enterprise Planner/Multi-Agent stays disabled; production MULTI_AGENT_ENABLED=false',
      'Third-party Skill import, Marketplace and executable workflow/script Skills',
      'MCP inside Agent Runtime, write tools, network access and arbitrary shell/SQL',
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

            <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-[1.08] sm:text-5xl lg:text-[4.2rem]">
              {t.heroLead} <span className="text-foreground">{t.heroAccent}</span>
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
          <TeamRunConsole locale={locale} />
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
                      ? '添加自己的 OpenAI-compatible Provider，保存 API Key 并运行有界连接测试。'
                      : 'Add your OpenAI-compatible provider, save the API key and run a bounded connection test.'
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
                  icon={Users}
                  title={locale === 'zh' ? '开启团队模式' : 'Enable team mode'}
                  text={
                    locale === 'zh'
                      ? '由父 Agent 选择专业员工并提出计划，宿主校验后才会执行。'
                      : 'Let the parent select specialists and propose a plan; execution begins only after host validation.'
                  }
                />
                <StudioStep
                  icon={Rocket}
                  title={locale === 'zh' ? '运行并检查' : 'Run and inspect'}
                  text={
                    locale === 'zh'
                      ? '发起无工具 Provider 调用，并在 Task、Run、预算、知识结果和审计记录中检查这次工作。'
                      : 'Start a tool-free Provider call and inspect the work through Task, Run, budget, knowledge results and audit records.'
                  }
                />
              </div>
            </div>
            <TeamExecutionLoop locale={locale} />
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
          <div className="relative overflow-hidden rounded-lg border border-cyan-500/20 bg-slate-950 px-6 py-14 text-white shadow-2xl sm:px-12 lg:px-16">
            <div className="relative grid gap-9 lg:grid-cols-[1fr_auto] lg:items-end">
              <div>
                <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-300">
                  {t.ctaKicker}
                </div>
                <h2 className="mt-5 max-w-3xl text-3xl font-semibold sm:text-5xl">{t.ctaTitle}</h2>
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
          <div className="flex flex-wrap gap-x-6 gap-y-3">
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
            <a className="hover:text-foreground" href={`${repositoryUrl}/blob/main/COMMUNITY.md`}>
              Community
            </a>
          </div>
        </div>
      </footer>
    </div>
  )
}

function TeamRunConsole({ locale }: { locale: Locale }) {
  const rows =
    locale === 'zh'
      ? [
          ['01 · OWNER', '开启团队模式并设定可见预算'],
          ['02 · PARENT', '输出受限结构化 Proposal'],
          ['03 · HOST', '校验身份、预算、依赖与并发'],
          ['04 · WAVES', '运行串行、并行或混合专业任务'],
          ['05 · PROOF', '结算调用证明并完成成功闭包'],
        ]
      : [
          ['01 · OWNER', 'Enable team mode and set a visible budget'],
          ['02 · PARENT', 'Emit a restricted structured Proposal'],
          ['03 · HOST', 'Validate identity, budget, dependencies and concurrency'],
          ['04 · WAVES', 'Run serial, parallel or mixed specialist work'],
          ['05 · PROOF', 'Settle call proofs and close success atomically'],
        ]
  return (
    <div className="relative mx-auto w-full max-w-xl">
      <div
        className={cn(
          styles.heroGlow,
          'relative overflow-hidden rounded-lg border border-white/10 bg-slate-950 text-slate-100',
        )}
      >
        <div className="flex h-11 items-center justify-between border-b border-white/10 px-4">
          <span className="font-mono text-[9px] text-slate-500">TEAM RUN / R0</span>
          <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-slate-500">
            loopback proven
          </span>
          <Users className="h-4 w-4 text-cyan-300" />
        </div>
        <div className="p-6 sm:p-7">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-slate-500">
                {locale === 'zh' ? '当前状态' : 'Current state'}
              </p>
              <p className="mt-1 text-sm font-medium">
                {locale === 'zh'
                  ? '父 Agent 正在协调三名专业员工'
                  : 'Parent Agent coordinating three specialists'}
              </p>
            </div>
            <span className="rounded border border-cyan-400/20 bg-cyan-400/10 px-2.5 py-1 font-mono text-[9px] uppercase tracking-wider text-cyan-300">
              host validated
            </span>
          </div>
          <div className="space-y-3">
            {rows.map(([key, value]) => (
              <div
                key={key}
                className="grid grid-cols-[7.5rem_1fr] gap-3 rounded border border-white/[0.07] bg-white/[0.025] px-3 py-3 sm:items-center"
              >
                <span className="font-mono text-[9px] text-slate-300">{key}</span>
                <span className="text-[10px] leading-4 text-slate-500">{value}</span>
              </div>
            ))}
          </div>
          <div className="mt-5 flex items-center gap-3 rounded border border-emerald-400/15 bg-emerald-400/[0.06] p-4">
            <BadgeCheck className="h-5 w-5 text-emerald-300" />
            <div>
              <p className="text-xs font-medium">
                {locale === 'zh'
                  ? '调用有预留，结果有证明'
                  : 'Calls are reserved. Outcomes are proven.'}
              </p>
              <p className="mt-1 text-[10px] text-slate-500">
                reservation → execution → settlement → synthesis
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function TeamExecutionLoop({ locale }: { locale: Locale }) {
  const labels =
    locale === 'zh'
      ? [
          ['Owner 授权', '显式开启本次团队模式'],
          ['父 Agent 提案', '选择员工、依赖与 wave 形状'],
          ['宿主验证', '身份、预算、并发与调用预留'],
          ['员工协作', '通过黑板回报，不直接启动同伴'],
          ['合成与关闭', '证明绑定、成功闭包或安静终态'],
        ]
      : [
          ['Owner approval', 'Explicitly enable team mode for this task'],
          ['Parent proposal', 'Choose specialists, dependencies and wave shape'],
          ['Host validation', 'Bind identity, budget, concurrency and reservations'],
          ['Specialist work', 'Report through the blackboard; never launch peers'],
          ['Synthesis and close', 'Bind proof, success closure or a quiet terminal'],
        ]
  return (
    <div className="rounded-lg border border-border/70 bg-card/75 p-6 shadow-xl sm:p-8">
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded bg-violet-500/10 text-violet-500">
          <Users className="h-5 w-5" />
        </span>
        <div>
          <p className="font-semibold">
            {locale === 'zh' ? '团队运行闭环' : 'Team execution loop'}
          </p>
          <p className="text-xs text-muted-foreground">
            Owner → Parent → Host → Specialists → Synthesis
          </p>
        </div>
      </div>
      <div className="mt-8 space-y-1">
        {labels.map(([title, body], index) => (
          <div key={title} className="relative flex gap-4 pb-7 last:pb-0">
            <div className="absolute bottom-0 left-[17px] top-9 w-px bg-border last:hidden" />
            <span className="relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded bg-primary font-mono text-[10px] font-bold text-primary-foreground">
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
      <h2 className="mt-4 text-3xl font-semibold sm:text-4xl">{title}</h2>
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

import { BookOpen, Boxes, BrainCircuit, LockKeyhole, ShieldCheck } from 'lucide-react'
import { ThemeToggle } from '@/components/theme-toggle'
import { BrandLockup, BrandMark } from '@/components/layout/brand-mark'

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative grid min-h-screen overflow-hidden bg-background lg:grid-cols-[minmax(36rem,1.12fr)_minmax(28rem,.88fr)]">
      <div className="absolute right-4 top-4 z-30 sm:right-6 sm:top-6">
        <ThemeToggle />
      </div>

      <section className="relative hidden overflow-hidden border-r border-stone-800 bg-[#11100e] px-10 py-8 text-stone-100 lg:flex lg:flex-col xl:px-14 xl:py-10">
        <div className="workspace-grid pointer-events-none absolute inset-0 opacity-35" />
        <div className="editorial-rule absolute left-0 top-0 h-0.5 w-80" />

        <div className="relative flex items-center justify-between gap-4">
          <BrandLockup />
          <span className="border border-stone-800 px-2.5 py-1 font-mono text-[8px] uppercase tracking-[0.16em] text-stone-600">
            Self-hosted / Open source
          </span>
        </div>

        <div className="relative my-auto grid max-w-4xl gap-10 py-10 xl:grid-cols-[minmax(0,1fr)_13rem] xl:items-end">
          <div>
            <div className="font-mono text-[9px] font-semibold uppercase tracking-[0.22em] text-amber-300">
              Knowledge · Workspace · Agents
            </div>
            <h1 className="mt-4 max-w-2xl text-4xl font-semibold leading-[1.05] tracking-[-0.055em] xl:text-5xl">
              不是另一个聊天框。
              <span className="mt-1 block text-stone-500">是 AI 工作真正发生的地方。</span>
            </h1>
            <p className="mt-5 max-w-xl text-sm leading-6 text-stone-500">
              OmniBase 把知识、检索、工作空间和智能体治理组织为可维护、可追溯、可自托管的工作系统。
            </p>

            <div className="mt-9 border-y border-stone-800">
              <WorkbenchLine index="01" icon={BookOpen} title="Knowledge" state="Available" />
              <WorkbenchLine index="02" icon={Boxes} title="Workspace" state="Controlled" />
              <WorkbenchLine
                index="03"
                icon={BrainCircuit}
                title="Agent Registry"
                state="Preview"
              />
            </div>
          </div>

          <div className="border-l border-stone-800 pl-5">
            <BrandMark className="h-12 w-12" />
            <p className="mt-5 font-mono text-[8px] uppercase tracking-[0.18em] text-stone-600">
              System posture
            </p>
            <div className="mt-3 flex items-center gap-2 text-xs text-stone-300">
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
              Browser boundary live
            </div>
            <div className="mt-2 flex items-center gap-2 text-xs text-stone-500">
              <LockKeyhole className="h-4 w-4 text-amber-300" />
              Runtime locked
            </div>
          </div>
        </div>

        <div className="relative flex items-center justify-between gap-4 border-t border-stone-800 pt-4 font-mono text-[8px] uppercase tracking-[0.14em] text-stone-600">
          <span>Build with evidence</span>
          <span>AI maintainable · 2026</span>
        </div>
      </section>

      <section className="relative flex min-h-screen items-center justify-center bg-[#ebe6dc] px-4 py-20 text-stone-950 dark:bg-[#1a1815] dark:text-stone-100 sm:px-8">
        <div className="absolute inset-x-0 top-0 h-0.5 bg-amber-400 lg:hidden" />
        <div className="absolute left-5 top-5 z-10 lg:hidden">
          <BrandLockup />
        </div>
        <div className="relative z-10 w-full max-w-md">{children}</div>
      </section>
    </div>
  )
}

function WorkbenchLine({
  index,
  icon: Icon,
  title,
  state,
}: {
  index: string
  icon: React.ComponentType<{ className?: string }>
  title: string
  state: string
}) {
  return (
    <div className="grid grid-cols-[2.25rem_2rem_minmax(0,1fr)_auto] items-center gap-3 border-b border-stone-800 py-3.5 last:border-b-0">
      <span className="font-mono text-[8px] text-stone-700">{index}</span>
      <Icon className="h-4 w-4 text-amber-300" />
      <span className="text-xs font-medium text-stone-300">{title}</span>
      <span className="font-mono text-[7px] uppercase tracking-[0.14em] text-stone-600">
        {state}
      </span>
    </div>
  )
}

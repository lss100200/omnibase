import { cn } from '@/lib/utils'

export function BrandMark({ className, glow = false }: { className?: string; glow?: boolean }) {
  return (
    <div
      className={cn(
        'relative flex h-10 w-10 shrink-0 items-center justify-center text-current',
        glow && 'drop-shadow-[0_0_10px_currentColor]',
        className,
      )}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 64 64"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="h-full w-full"
        focusable="false"
      >
        <path
          d="M32 4.75 55 18v28L32 59.25 9 46V18L32 4.75Z"
          stroke="currentColor"
          strokeWidth="4.5"
          strokeLinejoin="round"
        />
        <path
          d="m13.75 20.75 18.25-10.5 18.25 10.5-11.5 6.65M13.75 25v18.25l13.75 7.9V39.3M50.25 25v18.25l-13.75 7.9V39.3M32 39.5v14.75"
          stroke="currentColor"
          strokeWidth="4.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="32" cy="32" r="7.75" fill="none" stroke="currentColor" strokeWidth="4.5" />
      </svg>
    </div>
  )
}

export function BrandLockup({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <BrandMark />
      {!compact && (
        <div className="min-w-0">
          <div className="text-[17px] font-bold tracking-[-0.035em]">OmniBase</div>
          <div className="font-mono text-[9px] uppercase tracking-[0.22em] text-muted-foreground">
            AI Workbench
          </div>
        </div>
      )}
    </div>
  )
}

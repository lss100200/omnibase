import * as React from 'react'
import { cn } from '@/lib/utils'

// Lightweight skeleton loader (no animation lib needed; uses Tailwind pulse)
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} {...props} />
}

export { Skeleton }

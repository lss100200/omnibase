export interface ScrollMetrics {
  readonly scrollTop: number
  readonly scrollHeight: number
  readonly clientHeight: number
}

export function isNearBottom(metrics: ScrollMetrics, thresholdPx = 96): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= thresholdPx
}

export interface TrailingThrottle<T> {
  push(value: T): void
  flush(): void
  cancel(): void
}

export function createTrailingThrottle<T>(
  waitMs: number,
  onFlush: (value: T) => void,
): TrailingThrottle<T> {
  let pending: T | undefined
  let hasPending = false
  let timeoutId: ReturnType<typeof setTimeout> | null = null

  const clearScheduledFlush = () => {
    if (timeoutId === null) return
    clearTimeout(timeoutId)
    timeoutId = null
  }

  const flush = () => {
    clearScheduledFlush()
    if (!hasPending) return

    const value = pending as T
    pending = undefined
    hasPending = false
    onFlush(value)
  }

  return {
    push(value) {
      pending = value
      hasPending = true
      if (timeoutId !== null) return
      timeoutId = setTimeout(flush, waitMs)
    },
    flush,
    cancel() {
      clearScheduledFlush()
      pending = undefined
      hasPending = false
    },
  }
}

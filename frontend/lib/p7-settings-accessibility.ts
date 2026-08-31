export type P7SettingsNavigationKey = 'ArrowDown' | 'ArrowUp' | 'Home' | 'End'

export function p7SettingsNavigationTargetIndex(
  currentIndex: number,
  itemCount: number,
  key: P7SettingsNavigationKey,
): number | null {
  if (!Number.isInteger(currentIndex) || !Number.isInteger(itemCount) || itemCount <= 0) {
    return null
  }
  const current = Math.min(Math.max(currentIndex, 0), itemCount - 1)
  if (key === 'Home') return 0
  if (key === 'End') return itemCount - 1
  if (key === 'ArrowDown') return (current + 1) % itemCount
  if (key === 'ArrowUp') return (current - 1 + itemCount) % itemCount
  return null
}

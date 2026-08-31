'use client'

import type { LucideIcon } from 'lucide-react'

export function P7SettingsToggle({
  checked,
  disabled = false,
  describedBy,
  id,
  invalid = false,
  label,
  onChange,
}: {
  readonly checked: boolean
  readonly disabled?: boolean
  readonly describedBy?: string
  readonly id?: string
  readonly invalid?: boolean
  readonly label: string
  readonly onChange: (checked: boolean) => void
}) {
  return (
    <label
      className={['p7-settings-toggle', disabled ? 'p7-disabled' : null].filter(Boolean).join(' ')}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span aria-hidden="true" />
      <strong>{label}</strong>
    </label>
  )
}

export function P7SettingRow({
  label,
  labelFor,
  meta,
  metaId,
  invalid = false,
  children,
}: {
  readonly label: string
  readonly labelFor?: string
  readonly meta?: string
  readonly metaId?: string
  readonly invalid?: boolean
  readonly children: React.ReactNode
}) {
  return (
    <div className="p7-settings-row">
      <div className="p7-settings-row-copy">
        {labelFor === undefined ? (
          <strong>{label}</strong>
        ) : (
          <label htmlFor={labelFor}>{label}</label>
        )}
        {meta !== undefined && (
          <span id={metaId} role={invalid ? 'alert' : undefined}>
            {meta}
          </span>
        )}
      </div>
      <div className="p7-settings-row-control">{children}</div>
    </div>
  )
}

export function P7Segmented<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
}: {
  readonly value: T
  readonly options: readonly {
    readonly value: T
    readonly label: string
    readonly disabled?: boolean
  }[]
  readonly onChange: (value: T) => void
  readonly disabled?: boolean
}) {
  return (
    <div className="p7-segmented">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          disabled={disabled || option.disabled === true}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function P7SettingsSection({
  title,
  scope,
  busy = false,
  children,
}: {
  readonly title: string
  readonly scope: string
  readonly busy?: boolean
  readonly children: React.ReactNode
}) {
  return (
    <section className="p7-settings-section" aria-busy={busy}>
      <header>
        <h1>{title}</h1>
        <span>{scope}</span>
      </header>
      {children}
    </section>
  )
}

export function P7SettingsEmpty({
  children,
  state = 'empty',
}: {
  readonly children: React.ReactNode
  readonly state?: 'empty' | 'loading' | 'error'
}) {
  return (
    <div
      className="p7-settings-empty"
      role={state === 'error' ? 'alert' : state === 'loading' ? 'status' : undefined}
      aria-live={state === 'loading' ? 'polite' : undefined}
    >
      {children}
    </div>
  )
}

export function P7SettingsNavItem({
  current,
  icon: Icon,
  label,
  count,
  onClick,
}: {
  readonly current: boolean
  readonly icon: LucideIcon
  readonly label: string
  readonly count?: number
  readonly onClick: () => void
}) {
  return (
    <button
      type="button"
      className="p7-settings-nav-item"
      aria-current={current ? 'page' : undefined}
      onClick={onClick}
    >
      <Icon size={15} />
      <span>{label}</span>
      {count !== undefined && count > 0 && <span className="p7-settings-count">{count}</span>}
    </button>
  )
}

export function P7SettingsStatus({
  tone,
  children,
}: {
  readonly tone: 'ready' | 'warning' | 'error' | 'muted'
  readonly children: React.ReactNode
}) {
  return <span className={`p7-component-status p7-component-status-${tone}`}>{children}</span>
}

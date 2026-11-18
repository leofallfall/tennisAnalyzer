import type { AnalysisStatus, Observability, Rating } from '@tpa/contracts'
import { AlertTriangle, FlaskConical, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { humanize } from '@/lib/format'
import { cn } from '@/lib/utils'

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </div>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  className,
}: {
  icon: LucideIcon
  title: string
  children?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center rounded-lg border border-dashed px-6 py-10 text-center',
        className,
      )}
    >
      <Icon className="mb-3 size-6 text-muted-foreground" aria-hidden />
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-muted-foreground">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/**
 * Marks UI that shows example data because the underlying analysis does not exist yet.
 * Every placeholder section MUST use this so example values are never mistaken for results.
 */
export function PlaceholderNotice({ children }: { children?: ReactNode }) {
  return (
    <div
      role="note"
      className="mb-3 flex items-start gap-2 rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-[oklch(0.4_0.09_70)]"
    >
      <FlaskConical className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <div>
        <strong className="font-semibold uppercase tracking-wide">
          Example layout — not a real analysis.
        </strong>{' '}
        {children ?? 'This capability is not implemented yet. Values shown are illustrative only.'}
      </div>
    </div>
  )
}

export function PlaceholderSection({ children }: { children: ReactNode }) {
  return <div className="placeholder-hatch rounded-md">{children}</div>
}

const statusVariant: Record<AnalysisStatus, 'neutral' | 'info' | 'primary' | 'success' | 'danger'> =
  {
    created: 'neutral',
    queued: 'info',
    running: 'primary',
    completed: 'success',
    failed: 'danger',
  }

export function StatusBadge({ status }: { status: AnalysisStatus }) {
  return <Badge variant={statusVariant[status]}>{humanize(status)}</Badge>
}

const ratingVariant: Record<Rating, 'success' | 'warning' | 'danger' | 'neutral'> = {
  good: 'success',
  acceptable: 'warning',
  poor: 'danger',
  unknown: 'neutral',
}

export function RatingBadge({ rating }: { rating: Rating }) {
  return <Badge variant={ratingVariant[rating]}>{humanize(rating)}</Badge>
}

const obsVariant: Record<Observability, 'success' | 'warning' | 'danger' | 'neutral'> = {
  observable: 'success',
  partially_observable: 'warning',
  not_observable: 'danger',
  not_evaluated: 'neutral',
}

export function ObservabilityBadge({ value }: { value: Observability }) {
  return <Badge variant={obsVariant[value]}>{humanize(value)}</Badge>
}

export function ErrorAlert({
  title,
  message,
  code,
}: {
  title: string
  message: string
  code?: string
}) {
  return (
    <div
      role="alert"
      className="flex gap-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
      <div className="min-w-0">
        <p className="font-medium text-danger">{title}</p>
        <p className="text-muted-foreground">{message}</p>
        {code && <p className="mt-1 font-mono text-[11px] text-muted-foreground">code: {code}</p>}
      </div>
    </div>
  )
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 truncate text-right font-mono text-[13px]">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

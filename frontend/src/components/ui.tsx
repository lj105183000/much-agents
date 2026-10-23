import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  ReactNode,
} from 'react'
import { cn } from '../lib/cn'
import { Icon, type IconName } from './icons'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-cyan-400 text-slate-950 shadow-lg shadow-cyan-500/10 hover:bg-cyan-300 disabled:bg-slate-700 disabled:text-slate-400 disabled:shadow-none',
  secondary:
    'border border-slate-700 bg-slate-900/70 text-slate-100 hover:border-slate-500 hover:bg-slate-800 disabled:text-slate-500',
  ghost:
    'text-slate-300 hover:bg-slate-800/80 hover:text-white disabled:text-slate-600',
  danger:
    'border border-red-500/30 bg-red-500/10 text-red-200 hover:bg-red-500/20 disabled:text-red-300/50',
}

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 rounded-lg px-3 text-xs',
  md: 'h-10 rounded-xl px-4 text-sm',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        'inline-flex items-center justify-center gap-2 font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60 disabled:cursor-not-allowed',
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <Spinner className="h-4 w-4" /> : null}
      {children}
    </button>
  )
}

export function Card({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded-2xl border border-slate-800/90 bg-slate-900/55 shadow-xl shadow-black/20 backdrop-blur',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

type BadgeTone = 'neutral' | 'cyan' | 'green' | 'amber' | 'red'

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: 'border-slate-700 bg-slate-800/70 text-slate-300',
  cyan: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',
  green: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  amber: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  red: 'border-red-500/30 bg-red-500/10 text-red-200',
}

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
}

export function Badge({
  tone = 'neutral',
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium',
        BADGE_TONES[tone],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  )
}

interface ProgressBarProps {
  percent: number
  className?: string
  tone?: 'cyan' | 'green' | 'red'
}

export function ProgressBar({
  percent,
  className,
  tone = 'cyan',
}: ProgressBarProps) {
  const value = Math.max(0, Math.min(100, percent))
  const toneClass =
    tone === 'green'
      ? 'bg-emerald-400'
      : tone === 'red'
        ? 'bg-red-400'
        : 'bg-cyan-400'
  return (
    <div
      className={cn('h-2 overflow-hidden rounded-full bg-slate-800', className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
    >
      <div
        className={cn(
          'h-full rounded-full transition-all duration-500',
          toneClass,
          value === 0 && 'animate-pulse',
        )}
        style={{ width: value === 0 ? '8%' : `${value}%` }}
      />
    </div>
  )
}

interface FieldProps {
  label: string
  htmlFor?: string
  hint?: string
  error?: string
  children: ReactNode
  className?: string
}

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
  className,
}: FieldProps) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label
        htmlFor={htmlFor}
        className="block text-xs font-medium text-slate-300"
      >
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-[11px] text-red-300">{error}</p>
      ) : hint ? (
        <p className="text-[11px] text-slate-500">{hint}</p>
      ) : null}
    </div>
  )
}

export const inputClass =
  'h-10 w-full rounded-xl border border-slate-700 bg-slate-950/50 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-400/60 focus:outline-none focus:ring-2 focus:ring-cyan-400/10'

export const textareaClass =
  'w-full rounded-xl border border-slate-700 bg-slate-950/60 px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-400/60 focus:outline-none focus:ring-2 focus:ring-cyan-400/10'

export const selectClass =
  'h-10 w-full appearance-none rounded-xl border border-slate-700 bg-slate-950/50 px-3 text-sm text-slate-100 focus:border-cyan-400/60 focus:outline-none focus:ring-2 focus:ring-cyan-400/10'

interface ToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  hint?: string
  disabled?: boolean
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled = false,
}: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-4 rounded-xl border border-slate-800 bg-slate-950/30 px-3 py-2.5 text-left transition hover:border-slate-700 disabled:cursor-not-allowed disabled:opacity-60"
    >
      <span>
        <span className="block text-sm text-slate-200">{label}</span>
        {hint ? (
          <span className="mt-0.5 block text-[11px] text-slate-500">
            {hint}
          </span>
        ) : null}
      </span>
      <span
        className={cn(
          'relative h-5 w-9 shrink-0 rounded-full transition',
          checked ? 'bg-cyan-400' : 'bg-slate-700',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition',
            checked ? 'left-[18px]' : 'left-0.5',
          )}
        />
      </span>
    </button>
  )
}

interface EmptyStateProps {
  icon: IconName
  title: string
  description: string
  action?: ReactNode
  className?: string
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-900/30 px-6 py-12 text-center',
        className,
      )}
    >
      <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-700 bg-slate-800/70 text-cyan-300">
        <Icon name={icon} />
      </span>
      <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
      <p className="mt-1 max-w-md text-xs leading-6 text-slate-500">
        {description}
      </p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  )
}

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <span
      className={cn(
        'inline-block animate-spin rounded-full border-2 border-current border-t-transparent',
        className,
      )}
      aria-label="加载中"
    />
  )
}

interface StatProps {
  label: string
  value: ReactNode
  hint?: string
  className?: string
}

export function Stat({ label, value, hint, className }: StatProps) {
  return (
    <div
      className={cn(
        'rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5',
        className,
      )}
    >
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold tracking-tight text-slate-100">
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p> : null}
    </div>
  )
}

export function SectionHeader({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-base font-semibold text-slate-100">{title}</h2>
        {description ? (
          <p className="mt-1 text-xs leading-6 text-slate-500">
            {description}
          </p>
        ) : null}
      </div>
      {action}
    </div>
  )
}

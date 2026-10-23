import type { ViewKey } from '../api/types'
import type { HealthState } from '../hooks/useHealth'
import { cn } from '../lib/cn'
import { NAV_ITEMS } from '../lib/navigation'
import { Icon } from './icons'

interface HeaderProps {
  title: string
  subtitle: string
  active: ViewKey
  onNavigate: (view: ViewKey) => void
  health: HealthState
  onOpenSettings: () => void
}

const HEALTH_PILL = {
  loading: 'border-slate-700 bg-slate-800/60 text-slate-300',
  online: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  degraded: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  offline: 'border-red-500/30 bg-red-500/10 text-red-200',
} as const

export function Header({
  title,
  subtitle,
  active,
  onNavigate,
  health,
  onOpenSettings,
}: HeaderProps) {
  return (
    <header className="border-b border-slate-800/70 bg-slate-950/50 px-4 py-4 backdrop-blur md:px-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-[0.25em] text-cyan-400/80">
              Much Agents
            </span>
            <span className="h-1 w-1 rounded-full bg-slate-600" />
            <span className="text-[11px] text-slate-500">工作台</span>
          </div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight text-slate-100">
            {title}
          </h1>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">
            {subtitle}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[11px] font-medium',
              HEALTH_PILL[health.state],
            )}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                health.state === 'online'
                  ? 'bg-emerald-400'
                  : health.state === 'degraded'
                    ? 'bg-amber-400'
                    : health.state === 'offline'
                      ? 'bg-red-400'
                      : 'bg-slate-400 animate-pulse',
              )}
            />
            {health.state === 'online'
              ? 'API 正常'
              : health.state === 'degraded'
                ? 'API 降级'
                : health.state === 'offline'
                  ? 'API 离线'
                  : '连接中'}
          </span>
          <button
            type="button"
            onClick={onOpenSettings}
            className="rounded-xl border border-slate-800 bg-slate-900/60 p-2.5 text-slate-400 transition hover:border-slate-700 hover:text-slate-100"
            aria-label="API 设置"
          >
            <Icon name="settings" className="h-4 w-4" />
          </button>
        </div>
      </div>

      <nav
        className="mt-4 flex gap-1 overflow-x-auto pb-0.5 lg:hidden"
        aria-label="移动端导航"
      >
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => onNavigate(item.key)}
            className={cn(
              'flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-xs transition',
              item.key === active
                ? 'bg-cyan-400/10 text-cyan-200'
                : 'text-slate-500 hover:bg-slate-900 hover:text-slate-200',
            )}
          >
            <Icon name={item.icon} className="h-4 w-4" />
            {item.label}
          </button>
        ))}
      </nav>
    </header>
  )
}

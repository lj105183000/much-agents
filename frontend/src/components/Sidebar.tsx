import type { ViewKey } from '../api/types'
import type { HealthState } from '../hooks/useHealth'
import { cn } from '../lib/cn'
import { healthDependencySummary } from '../lib/health'
import { NAV_ITEMS } from '../lib/navigation'
import { Icon } from './icons'

interface SidebarProps {
  active: ViewKey
  onNavigate: (view: ViewKey) => void
  health: HealthState
  apiBase: string
  onOpenSettings: () => void
}

const HEALTH_STYLES = {
  loading: {
    dot: 'bg-slate-500',
    label: '连接中',
    text: 'text-slate-400',
  },
  online: {
    dot: 'bg-emerald-400',
    label: '后端在线',
    text: 'text-emerald-300',
  },
  degraded: {
    dot: 'bg-amber-400',
    label: '后端降级',
    text: 'text-amber-300',
  },
  offline: {
    dot: 'bg-red-400',
    label: '后端离线',
    text: 'text-red-300',
  },
} as const

export function Sidebar({
  active,
  onNavigate,
  health,
  apiBase,
  onOpenSettings,
}: SidebarProps) {
  const healthStyle = HEALTH_STYLES[health.state]

  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-800/80 bg-slate-950/70 px-4 py-5 lg:flex">
      <div className="flex items-center gap-3 px-2">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-cyan-400/30 bg-cyan-400/10 text-lg font-bold text-cyan-300">
          M
        </div>
        <div>
          <p className="text-sm font-semibold tracking-wide text-slate-100">
            Much Agents
          </p>
          <p className="text-[11px] text-slate-500">多 Agent 标书工作台</p>
        </div>
      </div>

      <nav className="mt-8 space-y-1.5" aria-label="主导航">
        {NAV_ITEMS.map((item) => {
          const selected = item.key === active
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onNavigate(item.key)}
              className={cn(
                'group flex w-full items-start gap-3 rounded-xl px-3 py-3 text-left transition',
                selected
                  ? 'bg-cyan-400/10 text-cyan-100 ring-1 ring-inset ring-cyan-400/20'
                  : 'text-slate-400 hover:bg-slate-900 hover:text-slate-100',
              )}
            >
              <span
                className={cn(
                  'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border',
                  selected
                    ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-300'
                    : 'border-slate-800 bg-slate-900 text-slate-500 group-hover:text-slate-300',
                )}
              >
                <Icon name={item.icon} className="h-4 w-4" />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium">{item.label}</span>
                <span className="mt-0.5 block text-[11px] leading-5 text-slate-500">
                  {item.description}
                </span>
              </span>
            </button>
          )
        })}
      </nav>

      <div className="mt-auto space-y-3 pt-6">
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-xs text-slate-300">
              <span
                className={cn(
                  'h-2 w-2 rounded-full',
                  healthStyle.dot,
                  health.state === 'loading' && 'animate-pulse',
                )}
              />
              <span className={healthStyle.text}>{healthStyle.label}</span>
            </span>
            <button
              type="button"
              onClick={health.refresh}
              className="rounded-md p-1 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
              aria-label="刷新后端状态"
            >
              <Icon name="refresh" className="h-3.5 w-3.5" />
            </button>
          </div>
          <p className="mt-2 break-all text-[10px] leading-4 text-slate-600">
            {apiBase || '同源 /api'}
          </p>
          {health.health ? (
            <p
              className={cn(
                'mt-1 text-[10px]',
                health.state === 'degraded'
                  ? 'text-amber-300/80'
                  : 'text-slate-500',
              )}
            >
              {healthDependencySummary(health.health)}
            </p>
          ) : health.error ? (
            <p className="mt-1 line-clamp-2 text-[10px] text-red-300/80">
              {health.error}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onOpenSettings}
          className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-xs text-slate-500 transition hover:bg-slate-900 hover:text-slate-300"
        >
          <Icon name="settings" className="h-4 w-4" />
          API 连接设置
        </button>
      </div>
    </aside>
  )
}

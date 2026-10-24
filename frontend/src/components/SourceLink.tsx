import { SOURCE_URL } from '../lib/source'
import { Icon } from './icons'

export function SourceLink({ className = '' }: { className?: string }) {
  return (
    <a
      href={SOURCE_URL}
      target="_blank"
      rel="noreferrer"
      className={`inline-flex items-center gap-1.5 text-slate-400 transition hover:text-slate-200 ${className}`}
      title="GNU Affero General Public License v3.0 or later — 获取对应源代码"
    >
      源代码 · AGPL-3.0
      <Icon name="external" className="h-3.5 w-3.5" />
    </a>
  )
}

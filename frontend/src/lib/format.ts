import type { JobKind, JobStatus } from '../api/types'

const STAGE_LABELS: Record<string, string> = {
  starting: '准备任务',
  mineru: '云端解析',
  parse: '解析文档',
  extract: '抽取信息',
  chunk: '文本分块',
  ingest: '向量入库',
  context: '加载招标文件',
  outline: '生成文档骨架',
  outline_ready: '大纲就绪',
  plan: '规划编写',
  planning: '规划章节',
  research: '检索依据',
  write: '编写章节',
  review: '合规审查',
  revise: '修订章节',
  generated: '正文已生成',
  edited: '正文已修改，待重新组装',
  assemble: '组装标书',
  assembling: '组装检查',
  assembled: '已组装',
  paused: '已暂停',
  queued: '排队中',
  done: '完成',
  failed: '失败',
}

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: '排队中',
  running: '进行中',
  succeeded: '已完成',
  failed: '失败',
}

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return '等待开始'
  return STAGE_LABELS[stage] ?? stage
}

export function statusLabel(status: JobStatus): string {
  return STATUS_LABELS[status] ?? status
}

export function kindLabel(kind: JobKind): string {
  return kind === 'bid' ? '投标文件' : '招标解析'
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return '—'
  }
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分`
}

export function elapsedLabel(
  startedAt: string | null,
  finishedAt: string | null,
): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  if (Number.isNaN(start) || Number.isNaN(end)) return '—'
  return formatDuration(Math.max(0, (end - start) / 1000))
}

export function percentLabel(value: number): string {
  return `${Math.max(0, Math.min(100, Math.round(value)))}%`
}

export function truncate(value: string, max = 80): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

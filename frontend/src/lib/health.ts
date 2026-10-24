import type { HealthResponse } from '../api/types'

export function degradedHealthDetail(health: HealthResponse | null): string {
  if (!health) return '部分依赖服务不可用。'

  const issues: string[] = []
  const impacts: string[] = []

  if (health.redis_ok === false) {
    issues.push('Redis 不可用')
    if (health.task_backend === 'celery') {
      impacts.push('任务可能停在排队中')
    }
  }
  if (health.database_ok === false) {
    issues.push('PostgreSQL 不可用')
    impacts.push('公司库与投标 run 功能可能不可用')
  }
  if (health.storage_ok === false) {
    issues.push(
      health.storage_backend === 'minio'
        ? '对象存储 MinIO 不可用'
        : '对象存储不可用',
    )
    impacts.push('上传、文档库、搜索与投标产物可能失败')
  }

  if (issues.length === 0) {
    return health.task_backend === 'celery'
      ? 'Redis 或 worker 可能不可用，任务可能停在排队中。'
      : '部分依赖服务不可用。'
  }
  return impacts.length > 0
    ? `${issues.join('、')}；${impacts.join('；')}。`
    : `${issues.join('、')}。`
}

export function healthDependencySummary(health: HealthResponse): string {
  const parts: string[] = [
    health.task_backend === 'celery'
      ? `Celery · Redis ${health.redis_ok ? '正常' : '异常'}`
      : 'Local 内存模式',
  ]
  if (health.storage_backend === 'minio') {
    parts.push(`MinIO ${health.storage_ok ? '正常' : '异常'}`)
  }
  if (health.database_ok === false) {
    parts.push('PostgreSQL 异常')
  }
  return parts.join(' · ')
}

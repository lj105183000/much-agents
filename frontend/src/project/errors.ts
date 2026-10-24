import { ApiError } from '../api/client'

const FALLBACK = '项目列表读取失败，请稍后重试。'

export function projectContextErrorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) return FALLBACK
  if (caught.status === 404) {
    return (
      '后端未加载项目接口（HTTP 404），可能仍在运行旧版本。' +
      '请重启 API 后重试；本地开发可先运行 stop.bat，再运行 start.bat。'
    )
  }
  return caught.message
}

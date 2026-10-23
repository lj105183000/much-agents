import { useState } from 'react'
import { apiUrl, normalizeApiBase } from '../api/client'
import { Button, Field, inputClass } from './ui'
import { Modal } from './Modal'
import { Icon } from './icons'

interface SettingsDialogProps {
  open: boolean
  apiBase: string
  onClose: () => void
  onSave: (value: string) => void
}

export function SettingsDialog({
  open,
  apiBase,
  onClose,
  onSave,
}: SettingsDialogProps) {
  const [value, setValue] = useState(apiBase)
  const [error, setError] = useState<string | null>(null)

  const save = () => {
    try {
      onSave(normalizeApiBase(value))
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'API 地址无效')
    }
  }

  return (
    <Modal
      open={open}
      title="API 连接设置"
      onClose={onClose}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={save}>保存并重连</Button>
        </>
      }
    >
      <div className="space-y-5">
        <Field
          label="后端 API 地址"
          htmlFor="api-base"
          hint="默认 http://127.0.0.1:8001；Electron 生产环境必须使用完整 http 地址。"
          error={error ?? undefined}
        >
          <input
            id="api-base"
            className={inputClass}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="http://127.0.0.1:8001"
            spellCheck={false}
            onKeyDown={(event) => {
              if (event.key === 'Enter') save()
            }}
          />
        </Field>

        <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-xs leading-6 text-slate-400">
          <p className="font-medium text-slate-200">启动后端</p>
          <code className="mt-1 block break-all text-[11px] text-cyan-200">
            uv run uvicorn api.main:app --host 0.0.0.0 --port 8001
          </code>
          <p className="mt-2 text-slate-500">
            Celery 模式还需要 Redis 与 worker；没有 worker 时任务会停在排队中。
            也可以使用 TASK_BACKEND=local 进行单进程调试。
          </p>
          <a
            href={apiUrl('/docs')}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-flex items-center gap-1.5 text-cyan-300 hover:text-cyan-200"
          >
            打开 API 文档
            <Icon name="external" className="h-3.5 w-3.5" />
          </a>
        </div>
      </div>
    </Modal>
  )
}

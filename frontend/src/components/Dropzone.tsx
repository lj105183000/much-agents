import { useRef, useState, type DragEvent } from 'react'
import { cn } from '../lib/cn'
import { formatBytes } from '../lib/format'
import { Icon } from './icons'

interface DropzoneProps {
  file: File | null
  onFile: (file: File | null) => void
  disabled?: boolean
}

export function Dropzone({ file, onFile, disabled = false }: DropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const choose = (candidate: File | undefined) => {
    if (!candidate) return
    if (!candidate.name.toLowerCase().endsWith('.pdf')) {
      setError('只支持 PDF 文件')
      return
    }
    setError(null)
    onFile(candidate)
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (disabled) return
    choose(event.dataTransfer.files[0])
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="选择或拖入 PDF 文件"
        onClick={() => {
          if (!disabled) inputRef.current?.click()
        }}
        onKeyDown={(event) => {
          if ((event.key === 'Enter' || event.key === ' ') && !disabled) {
            event.preventDefault()
            inputRef.current?.click()
          }
        }}
        onDragOver={(event) => {
          event.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed px-6 py-10 text-center transition',
          dragging
            ? 'border-cyan-400/70 bg-cyan-400/5'
            : 'border-slate-700 bg-slate-950/30 hover:border-slate-500 hover:bg-slate-900/50',
          disabled && 'cursor-not-allowed opacity-60',
        )}
      >
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
          <Icon name="upload" />
        </span>
        <p className="mt-4 text-sm font-medium text-slate-200">
          拖入招标 PDF，或点击选择文件
        </p>
        <p className="mt-1 text-xs text-slate-500">
          支持单文件 PDF，默认上限 200MB
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          disabled={disabled}
          onChange={(event) => choose(event.target.files?.[0])}
        />
      </div>

      {file ? (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5">
          <span className="flex min-w-0 items-center gap-2">
            <Icon name="file" className="h-4 w-4 shrink-0 text-cyan-300" />
            <span className="truncate text-xs text-slate-200">{file.name}</span>
            <span className="shrink-0 text-[11px] text-slate-500">
              {formatBytes(file.size)}
            </span>
          </span>
          <button
            type="button"
            onClick={() => {
              onFile(null)
              if (inputRef.current) inputRef.current.value = ''
            }}
            className="rounded-md p-1 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
            aria-label="移除文件"
          >
            <Icon name="x" className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}

      {error ? <p className="mt-2 text-xs text-red-300">{error}</p> : null}
    </div>
  )
}

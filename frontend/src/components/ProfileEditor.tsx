import { useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { formatBytes } from '../lib/format'
import { cn } from '../lib/cn'
import {
  PROFILE_GROUPS,
  REQUIRED_FIELDS,
  getPathValue,
  missingRequiredFields,
  parseProfileText,
  prettyProfile,
  profileSizeBytes,
  setPathValue,
} from '../lib/profile'
import type { ProfileObject } from '../lib/profile'
import {
  Badge,
  Button,
  Card,
  Field,
  SectionHeader,
  inputClass,
  textareaClass,
} from './ui'
import { Icon } from './icons'

interface ProfileEditorProps {
  profile: ProfileObject
  onChange: (profile: ProfileObject) => void
}

type Tab = 'form' | 'json'

export function ProfileEditor({ profile, onChange }: ProfileEditorProps) {
  const [tab, setTab] = useState<Tab>('form')
  const [jsonDraft, setJsonDraft] = useState(prettyProfile(profile))
  const [jsonError, setJsonError] = useState<string | null>(null)
  const [jsonNotice, setJsonNotice] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const missing = missingRequiredFields(profile)
  const size = profileSizeBytes(profile)

  const applyJson = () => {
    try {
      const parsed = parseProfileText(jsonDraft)
      onChange(parsed)
      setJsonError(null)
      setJsonNotice('已应用 JSON 配置')
      setTab('form')
    } catch (caught) {
      setJsonNotice(null)
      setJsonError(caught instanceof Error ? caught.message : 'JSON 无法解析')
    }
  }

  const openJson = () => {
    setJsonDraft(prettyProfile(profile))
    setJsonError(null)
    setJsonNotice(null)
    setTab('json')
  }

  const importJson = async (file: File | undefined) => {
    if (!file) return
    try {
      const text = await file.text()
      setJsonDraft(text)
      const parsed = parseProfileText(text)
      onChange(parsed)
      setJsonDraft(prettyProfile(parsed))
      setJsonError(null)
      setJsonNotice(`已导入 ${file.name}`)
      setTab('form')
    } catch (caught) {
      setJsonNotice(null)
      setJsonError(caught instanceof Error ? caught.message : '导入失败')
      setTab('json')
    }
  }

  const downloadTemplate = async () => {
    try {
      const template = await api.profileTemplate()
      const blob = new Blob([JSON.stringify(template, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'bidder_profile.example.json'
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (caught) {
      setJsonNotice(null)
      setJsonError(
        caught instanceof ApiError ? caught.message : '模板下载失败',
      )
    }
  }

  const clear = () => {
    onChange({})
    setJsonDraft('{}')
    setJsonError(null)
    setJsonNotice('已清空资料')
  }

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-800 px-4 py-3">
        <SectionHeader
          title="投标人资料"
          description="缺失字段不会阻断生成，但会以【待填写】占位；严禁填写虚假信息。"
          action={
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-950/40 p-1">
              <button
                type="button"
                onClick={() => setTab('form')}
                className={cn(
                  'rounded-md px-3 py-1.5 text-xs transition',
                  tab === 'form'
                    ? 'bg-cyan-400/15 text-cyan-200'
                    : 'text-slate-500 hover:text-slate-200',
                )}
              >
                基础信息
              </button>
              <button
                type="button"
                onClick={openJson}
                className={cn(
                  'rounded-md px-3 py-1.5 text-xs transition',
                  tab === 'json'
                    ? 'bg-cyan-400/15 text-cyan-200'
                    : 'text-slate-500 hover:text-slate-200',
                )}
              >
                完整 JSON
              </button>
            </div>
          }
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Badge tone={missing.length ? 'amber' : 'green'}>
            {missing.length ? `还缺 ${missing.length} 项` : '必需字段已填写'}
          </Badge>
          <span className="text-[11px] text-slate-500">
            约 {formatBytes(size)} · 上限 5MB
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(event) => {
                void importJson(event.target.files?.[0])
                event.target.value = ''
              }}
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => fileRef.current?.click()}
            >
              <Icon name="upload" className="h-3.5 w-3.5" />
              导入 JSON
            </Button>
            <Button variant="ghost" size="sm" onClick={() => void downloadTemplate()}>
              <Icon name="download" className="h-3.5 w-3.5" />
              下载模板
            </Button>
            <Button variant="ghost" size="sm" onClick={clear}>
              <Icon name="trash" className="h-3.5 w-3.5" />
              清空
            </Button>
          </span>
        </div>
        {missing.length ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {missing.map((field) => (
              <span
                key={field.label}
                className="rounded-md border border-amber-500/20 bg-amber-500/5 px-2 py-1 text-[10px] text-amber-200/80"
              >
                {field.label}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {tab === 'form' ? (
        <div className="space-y-5 p-4">
          {PROFILE_GROUPS.map((group) => {
            const fields = REQUIRED_FIELDS.filter(
              (field) => field.group === group,
            )
            return (
              <div key={group}>
                <h3 className="mb-2 text-xs font-semibold text-slate-300">
                  {group}
                </h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  {fields.map((field) => (
                    <Field
                      key={field.label}
                      label={field.label}
                      htmlFor={`profile-${field.path.join('-')}`}
                      hint={field.placeholder}
                    >
                      <input
                        id={`profile-${field.path.join('-')}`}
                        className={inputClass}
                        value={getPathValue(profile, field.path)}
                        onChange={(event) =>
                          onChange(
                            setPathValue(
                              profile,
                              field.path,
                              event.target.value,
                            ),
                          )
                        }
                        placeholder={field.placeholder}
                      />
                    </Field>
                  ))}
                </div>
              </div>
            )
          })}
          <p className="text-[11px] leading-5 text-slate-500">
            这里只列报告和表单必需的 14 个字段；业绩、人员、财务、证书、诉讼等高级字段请切换到「完整 JSON」编辑或导入已有文件。
          </p>
        </div>
      ) : (
        <div className="space-y-3 p-4">
          <textarea
            className={`${textareaClass} min-h-[22rem] font-mono text-[11px] leading-6`}
            value={jsonDraft}
            onChange={(event) => {
              setJsonDraft(event.target.value)
              setJsonNotice(null)
            }}
            spellCheck={false}
            placeholder="粘贴完整 bidder_profile JSON…"
          />
          {jsonError ? (
            <p className="rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
              {jsonError}
            </p>
          ) : jsonNotice ? (
            <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-200">
              {jsonNotice}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={applyJson}>
              <Icon name="check" className="h-3.5 w-3.5" />
              应用 JSON
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setJsonDraft(prettyProfile(profile))
                setJsonError(null)
                setJsonNotice('已恢复为当前资料')
              }}
            >
              <Icon name="refresh" className="h-3.5 w-3.5" />
              还原
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}

import { useState } from 'react'
import { api, ApiError } from '../api/client'
import type { CompanySummary, ViewKey } from '../api/types'
import { Icon } from '../components/icons'
import { Modal } from '../components/Modal'
import { ProfileEditor } from '../components/ProfileEditor'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  Spinner,
  inputClass,
} from '../components/ui'
import { useCompanies } from '../hooks/useCompanies'
import { usePermissions } from '../hooks/usePermissions'
import { formatDate } from '../lib/format'
import { getPathValue } from '../lib/profile'
import type { ProfileObject } from '../lib/profile'

interface CompaniesViewProps {
  apiBase: string
  onSelectCompany: (companyId: string) => void
  onNavigate: (view: ViewKey) => void
}

export function CompaniesView({
  apiBase,
  onSelectCompany,
  onNavigate,
}: CompaniesViewProps) {
  const { companies, loading, error, refresh } = useCompanies(apiBase)
  const { canManageCompanies: canManage, isAdmin } = usePermissions()
  const [query, setQuery] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [profile, setProfile] = useState<ProfileObject>({})
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)

  const filtered = companies.filter((company) => {
    const haystack = [
      company.company_name,
      company.credit_code,
      company.address,
      company.phone,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return haystack.includes(query.trim().toLowerCase())
  })

  const openCreate = () => {
    setEditingId(null)
    setProfile({})
    setFormError(null)
    setPageError(null)
    setModalOpen(true)
  }

  const openEdit = async (company: CompanySummary) => {
    setPageError(null)
    try {
      const detail = await api.getCompany(company.id)
      setEditingId(company.id)
      setProfile(detail.profile)
      setFormError(null)
      setModalOpen(true)
    } catch (caught) {
      setPageError(
        caught instanceof ApiError ? caught.message : '公司资料读取失败',
      )
    }
  }

  const save = async () => {
    const name = getPathValue(profile, ['company', 'name']).trim()
    if (!name) {
      setFormError('公司名称不能为空')
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      if (editingId) {
        await api.updateCompany(editingId, profile)
      } else {
        await api.createCompany(profile)
      }
      setModalOpen(false)
      refresh()
    } catch (caught) {
      setFormError(
        caught instanceof ApiError ? caught.message : '公司资料保存失败',
      )
    } finally {
      setSaving(false)
    }
  }

  const remove = async (company: CompanySummary) => {
    if (
      !window.confirm(
        `确定删除「${company.company_name}」吗？历史投标文件不受影响。`,
      )
    ) {
      return
    }
    setPageError(null)
    try {
      await api.deleteCompany(company.id)
      refresh()
    } catch (caught) {
      setPageError(
        caught instanceof ApiError ? caught.message : '删除公司失败',
      )
    }
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionHeader
          title="公司库"
          description="完整投标人资料存储在 PostgreSQL；编写标书时可直接载入，修改后可另存或覆盖公司资料。"
          action={
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" onClick={refresh} loading={loading}>
                <Icon name="refresh" className="h-3.5 w-3.5" />
                刷新
              </Button>
              {canManage ? (
                <Button size="sm" onClick={openCreate}>
                  <Icon name="building" className="h-3.5 w-3.5" />
                  新建公司
                </Button>
              ) : null}
            </div>
          }
        />
        {!canManage ? (
          <p className="mt-3 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2 text-[11px] leading-5 text-slate-500">
            当前角色只能查看公司摘要；电话、开户行与完整投标人资料仅对投标经理和管理员可见。
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <div className="relative min-w-[15rem] flex-1">
            <Icon
              name="building"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600"
            />
            <input
              className={`${inputClass} pl-9`}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按公司名称、信用代码、地址或电话搜索"
            />
          </div>
          <span className="text-[11px] text-slate-500">
            {filtered.length} / {companies.length} 家公司
          </span>
        </div>
      </Card>

      {error ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
          {error}
        </div>
      ) : null}
      {pageError ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
          {pageError}
        </div>
      ) : null}

      {loading && companies.length === 0 ? (
        <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取公司库…
        </Card>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon="building"
          title={companies.length ? '没有匹配的公司' : '公司库为空'}
          description={
            companies.length
              ? '换一个公司名称、信用代码、地址或电话试试。'
              : canManage
                ? '新建一家公司并保存完整投标人资料，之后编写标书时可以直接载入。'
                : '公司库为空，请联系投标经理或管理员添加公司资料。'
          }
          action={
            companies.length || !canManage ? undefined : (
              <Button onClick={openCreate}>
                <Icon name="building" className="h-4 w-4" />
                新建公司
              </Button>
            )
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {filtered.map((company) => (
            <Card key={company.id} className="flex flex-col p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-sm font-semibold text-slate-100">
                      {company.company_name}
                    </h3>
                    <Badge tone="cyan">已入库</Badge>
                  </div>
                  <p className="mt-1 truncate text-[11px] text-slate-500">
                    {company.credit_code || '未填写统一社会信用代码'}
                  </p>
                </div>
                <Button
                  size="sm"
                  onClick={() => {
                    onSelectCompany(company.id)
                    onNavigate('bid')
                  }}
                >
                  <Icon name="sparkles" className="h-3.5 w-3.5" />
                  去写标书
                </Button>
              </div>

              <dl className="mt-4 grid gap-2 text-[11px] text-slate-400 sm:grid-cols-2">
                <div>
                  <dt className="text-slate-600">地址</dt>
                  <dd className="mt-0.5 truncate">{company.address || '—'}</dd>
                </div>
                <div>
                  <dt className="text-slate-600">电话</dt>
                  <dd className="mt-0.5 truncate">{company.phone || '—'}</dd>
                </div>
                <div>
                  <dt className="text-slate-600">开户银行</dt>
                  <dd className="mt-0.5 truncate">{company.bank_name || '—'}</dd>
                </div>
                <div>
                  <dt className="text-slate-600">银行账号</dt>
                  <dd className="mt-0.5 truncate">
                    {company.bank_account || '—'}
                  </dd>
                </div>
              </dl>

              <div className="mt-auto flex items-center justify-between gap-2 border-t border-slate-800/80 pt-3">
                <span className="text-[10px] text-slate-600">
                  更新于 {formatDate(company.updated_at)}
                </span>
                <div className="flex items-center gap-1">
                  {canManage ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void openEdit(company)}
                    >
                      <Icon name="file" className="h-3.5 w-3.5" />
                      编辑
                    </Button>
                  ) : null}
                  {isAdmin ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void remove(company)}
                    >
                      <Icon name="trash" className="h-3.5 w-3.5" />
                      删除
                    </Button>
                  ) : null}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={modalOpen}
        title={editingId ? '编辑公司资料' : '新建公司资料'}
        onClose={() => setModalOpen(false)}
        size="xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setModalOpen(false)}>
              取消
            </Button>
            <Button onClick={() => void save()} loading={saving}>
              <Icon name="check" className="h-4 w-4" />
              保存到公司库
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {formError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
              {formError}
            </p>
          ) : null}
          <ProfileEditor profile={profile} onChange={setProfile} />
        </div>
      </Modal>
    </div>
  )
}

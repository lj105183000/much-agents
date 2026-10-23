import { useState } from 'react'
import { getApiBase, setApiBase as persistApiBase } from './api/client'
import type { ViewKey } from './api/types'
import { Header } from './components/Header'
import { SettingsDialog } from './components/SettingsDialog'
import { Sidebar } from './components/Sidebar'
import { Card } from './components/ui'
import { Icon } from './components/icons'
import { useDocuments } from './hooks/useDocuments'
import { useHealth } from './hooks/useHealth'
import { useHistory, useHistoryPoller } from './hooks/useHistory'
import { VIEW_META } from './lib/navigation'
import { BidView } from './views/BidView'
import { WorkbenchView } from './views/WorkbenchView'

function App() {
  const [view, setView] = useState<ViewKey>('workbench')
  const [apiBase, setApiBase] = useState(getApiBase)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const health = useHealth(apiBase)
  const history = useHistory()
  const documents = useDocuments(apiBase)

  useHistoryPoller(
    history.entries,
    history.updateFromJob,
    history.markExpired,
    activeJobId,
  )

  const meta = VIEW_META[view]

  const navigate = (target: ViewKey) => {
    setView(target)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const saveBase = (value: string) => {
    setApiBase(persistApiBase(value))
  }

  return (
    <div className="app-bg flex min-h-screen text-slate-100">
      <Sidebar
        active={view}
        onNavigate={navigate}
        health={health}
        apiBase={apiBase}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          title={meta.title}
          subtitle={meta.subtitle}
          active={view}
          onNavigate={navigate}
          health={health}
          onOpenSettings={() => setSettingsOpen(true)}
        />
        <main className="flex-1 overflow-y-auto px-4 py-6 md:px-7">
          <div className="mx-auto w-full max-w-6xl space-y-5">
            {health.state === 'offline' ? (
              <div className="flex flex-wrap items-start gap-3 rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
                <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <p className="font-medium">无法连接后端 API</p>
                  <p className="text-red-200/70">
                    请在项目根目录启动
                    <code className="mx-1 rounded bg-red-950/50 px-1.5 py-0.5 text-red-100">
                      uv run uvicorn api.main:app --port 8001
                    </code>
                    ，或点击右上角设置 API 地址。
                  </p>
                </div>
              </div>
            ) : null}
            {health.state === 'degraded' ? (
              <div className="flex flex-wrap items-start gap-3 rounded-2xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs leading-6 text-amber-200">
                <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <p className="font-medium">后端已连接，但状态降级</p>
                  <p className="text-amber-200/70">
                    {health.health?.task_backend === 'celery'
                      ? 'Redis 或 worker 可能不可用，任务可能停在排队中。'
                      : '部分依赖服务不可用。'}
                  </p>
                </div>
              </div>
            ) : null}

            {view === 'workbench' ? (
              <WorkbenchView
                onSelectDoc={(docId) => setSelectedDocId(docId)}
                onNavigate={navigate}
                onJobCreated={history.addEntry}
                onJobUpdate={history.updateFromJob}
                onActiveJobChange={setActiveJobId}
                onDocumentsRefresh={documents.refresh}
              />
            ) : null}
            {view === 'bid' ? (
              <BidView
                documents={documents.documents}
                documentsLoading={documents.loading}
                initialDocId={selectedDocId}
                onNavigate={navigate}
                onJobCreated={history.addEntry}
                onJobUpdate={history.updateFromJob}
                onActiveJobChange={setActiveJobId}
                onDocumentsRefresh={documents.refresh}
              />
            ) : null}
            {view === 'library' ? (
              <Card className="p-5">
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-slate-700 bg-slate-800 text-cyan-300">
                    <Icon name="folder" />
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-slate-100">
                      文档库
                    </p>
                    <p className="text-xs text-slate-500">
                      已发现 {documents.documents.length} 个解析文档
                    </p>
                  </div>
                </div>
              </Card>
            ) : null}
            {view === 'history' ? (
              <Card className="p-5">
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-slate-700 bg-slate-800 text-cyan-300">
                    <Icon name="history" />
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-slate-100">
                      任务历史
                    </p>
                    <p className="text-xs text-slate-500">
                      本机记录了 {history.entries.length} 个任务
                    </p>
                  </div>
                </div>
              </Card>
            ) : null}
          </div>
        </main>
      </div>

      <SettingsDialog
        open={settingsOpen}
        apiBase={apiBase}
        onClose={() => setSettingsOpen(false)}
        onSave={saveBase}
      />
    </div>
  )
}

export default App

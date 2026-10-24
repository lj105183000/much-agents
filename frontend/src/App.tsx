import { useEffect, useState } from 'react'
import { getApiBase } from './api/client'
import type { AuthUser, BidRunSummary, ViewKey } from './api/types'
import { useAuth } from './auth/useAuth'
import { ChangePasswordDialog } from './components/ChangePasswordDialog'
import { Header } from './components/Header'
import { SettingsDialog } from './components/SettingsDialog'
import { Sidebar } from './components/Sidebar'
import { Icon } from './components/icons'
import { Button, Card, EmptyState, Spinner } from './components/ui'
import { useDocuments } from './hooks/useDocuments'
import { useHealth } from './hooks/useHealth'
import { useHistory, useHistoryPoller } from './hooks/useHistory'
import { useTheme } from './hooks/useTheme'
import { saveApiBase } from './lib/apiBase'
import { degradedHealthDetail } from './lib/health'
import { VIEW_META } from './lib/navigation'
import { ProjectProvider } from './project/ProjectProvider'
import { useProject } from './project/useProject'
import { AuditView } from './views/AuditView'
import { BidView } from './views/BidView'
import { CompaniesView } from './views/CompaniesView'
import { HistoryView } from './views/HistoryView'
import { LibraryView } from './views/LibraryView'
import { LoginView } from './views/LoginView'
import { ProjectsView } from './views/ProjectsView'
import { SearchView } from './views/SearchView'
import { UsersView } from './views/UsersView'
import { WorkbenchView } from './views/WorkbenchView'

function AuthenticatedApp({ user }: { user: AuthUser }) {
  const { authEnabled, logout } = useAuth()
  const {
    projects,
    projectId,
    loading: projectsLoading,
    error: projectsError,
    selectProject,
  } = useProject()
  const [view, setView] = useState<ViewKey>('workbench')
  const [apiBase, setApiBase] = useState(getApiBase)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)
  const [selectedCompanyId, setSelectedCompanyId] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const health = useHealth(apiBase)
  const history = useHistory(user.id)
  const documents = useDocuments(apiBase, 0, projectId)
  const { theme, toggle: toggleTheme } = useTheme()

  useHistoryPoller(
    history.entries,
    history.updateFromJob,
    history.markExpired,
    activeJobId,
  )

  useEffect(() => {
    // Business selections never carry across projects.
    setSelectedDocId(null)
    setSelectedCompanyId(null)
    setSelectedRunId(null)
    setActiveJobId(null)
  }, [projectId])

  const meta = VIEW_META[view]
  const needsProject =
    view !== 'projects' &&
    view !== 'audit' &&
    view !== 'users' &&
    view !== 'companies'

  const navigate = (target: ViewKey) => {
    setView(target)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const selectDocument = (docId: string) => {
    setSelectedDocId(docId)
    setSelectedRunId(null)
  }

  const openRun = (run: BidRunSummary) => {
    setSelectedDocId(run.doc_id)
    setSelectedRunId(run.run_id)
    navigate('bid')
  }

  const saveBase = (value: string) => {
    setApiBase(saveApiBase(value, apiBase))
  }

  return (
    <div className="app-bg flex min-h-screen text-slate-100">
      <Sidebar
        active={view}
        onNavigate={navigate}
        health={health}
        apiBase={apiBase}
        onOpenSettings={() => setSettingsOpen(true)}
        user={user}
        authEnabled={authEnabled}
        onLogout={() => void logout()}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          title={meta.title}
          subtitle={meta.subtitle}
          active={view}
          onNavigate={navigate}
          health={health}
          theme={theme}
          onToggleTheme={toggleTheme}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenPassword={() => setPasswordOpen(true)}
          projects={projects}
          currentProjectId={projectId}
          projectsLoading={projectsLoading}
          onSelectProject={selectProject}
          user={user}
          authEnabled={authEnabled}
          onLogout={() => void logout()}
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
                    {degradedHealthDetail(health.health)}
                  </p>
                </div>
              </div>
            ) : null}

            {projectsError ? (
              <div className="flex flex-wrap items-start gap-3 rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
                <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <p className="font-medium">项目上下文不可用</p>
                  <p className="text-red-200/70">{projectsError}</p>
                </div>
              </div>
            ) : null}

            {needsProject && projectsLoading ? (
              <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
                <Spinner className="h-4 w-4" />
                正在加载项目上下文…
              </Card>
            ) : needsProject && !projectId ? (
              <EmptyState
                icon="layers"
                title="尚未选择项目"
                description="文档、任务与产物均按项目隔离。请先在项目管理中选择或创建一个项目，再开始上传解析或编写标书。"
                action={
                  <Button onClick={() => navigate('projects')}>
                    前往项目管理
                  </Button>
                }
              />
            ) : (
              <>
                {view === 'workbench' ? (
                  <WorkbenchView
                    key={projectId ?? 'no-project'}
                    onSelectDoc={selectDocument}
                    onNavigate={navigate}
                    onJobCreated={history.addEntry}
                    onJobUpdate={history.updateFromJob}
                    onActiveJobChange={setActiveJobId}
                    onDocumentsRefresh={documents.refresh}
                  />
                ) : null}
                {view === 'bid' ? (
                  <BidView
                    key={`${projectId ?? 'none'}:${selectedDocId ?? 'bid-new'}:${selectedCompanyId ?? 'no-company'}`}
                    apiBase={apiBase}
                    documents={documents.documents}
                    documentsLoading={documents.loading}
                    initialDocId={selectedDocId}
                    initialCompanyId={selectedCompanyId}
                    initialRunId={selectedRunId}
                    onRunChange={setSelectedRunId}
                    onNavigate={navigate}
                    onActiveJobChange={setActiveJobId}
                    onDocumentsRefresh={documents.refresh}
                  />
                ) : null}
                {view === 'companies' ? (
                  <CompaniesView
                    apiBase={apiBase}
                    onSelectCompany={(companyId) => {
                      setSelectedCompanyId(companyId)
                      setSelectedRunId(null)
                    }}
                    onNavigate={navigate}
                  />
                ) : null}
                {view === 'search' ? (
                  <SearchView
                    key={`${projectId ?? 'none'}:${selectedDocId ?? 'search'}`}
                    documents={documents.documents}
                    documentsLoading={documents.loading}
                    initialDocId={selectedDocId}
                    onSelectDoc={selectDocument}
                    onNavigate={navigate}
                  />
                ) : null}
                {view === 'library' ? (
                  <LibraryView
                    key={projectId ?? 'no-project'}
                    documents={documents.documents}
                    loading={documents.loading}
                    error={documents.error}
                    onRefresh={documents.refresh}
                    onSelectDoc={selectDocument}
                    onNavigate={navigate}
                  />
                ) : null}
                {view === 'history' ? (
                  <HistoryView
                    key={projectId ?? 'no-project'}
                    apiBase={apiBase}
                    entries={history.entries}
                    onRemove={history.removeEntry}
                    onClear={history.clear}
                    onSelectDoc={selectDocument}
                    onOpenRun={openRun}
                    onNavigate={navigate}
                  />
                ) : null}
                {view === 'projects' ? <ProjectsView /> : null}
                {view === 'audit' ? (
                  user.role === 'admin' ? (
                    <AuditView />
                  ) : (
                    <div className="rounded-2xl border border-slate-800 bg-slate-900/50 px-4 py-6 text-xs text-slate-400">
                      审计日志仅对管理员开放。
                    </div>
                  )
                ) : null}
                {view === 'users' ? (
                  authEnabled && user.role === 'admin' ? (
                    <UsersView />
                  ) : (
                    <div className="rounded-2xl border border-slate-800 bg-slate-900/50 px-4 py-6 text-xs text-slate-400">
                      用户管理仅对管理员开放。
                    </div>
                  )
                ) : null}
              </>
            )}
          </div>
        </main>
      </div>

      {settingsOpen ? (
        <SettingsDialog
          open
          apiBase={apiBase}
          onClose={() => setSettingsOpen(false)}
          onSave={saveBase}
        />
      ) : null}
      {passwordOpen ? (
        <ChangePasswordDialog
          open
          onClose={() => setPasswordOpen(false)}
          onChanged={() => {
            setPasswordOpen(false)
            void logout()
          }}
        />
      ) : null}
    </div>
  )
}

function App() {
  const { status, user } = useAuth()

  if (status === 'loading') {
    return (
      <div className="app-bg flex min-h-screen items-center justify-center gap-2 text-sm text-slate-400">
        <Spinner className="h-5 w-5" />
        正在验证登录状态…
      </div>
    )
  }

  if (status === 'anonymous' || !user) {
    return <LoginView />
  }

  return (
    <ProjectProvider key={user.id} user={user}>
      <AuthenticatedApp key={user.id} user={user} />
    </ProjectProvider>
  )
}

export default App

import type { IconName } from '../components/icons'
import type { ViewKey } from '../api/types'

export interface NavItem {
  key: ViewKey
  label: string
  description: string
  icon: IconName
}

export const NAV_ITEMS: NavItem[] = [
  {
    key: 'workbench',
    label: '上传解析',
    description: '上传招标 PDF，解析并抽取',
    icon: 'upload',
  },
  {
    key: 'bid',
    label: '编写标书',
    description: '多 Agent 生成投标文件',
    icon: 'sparkles',
  },
  {
    key: 'companies',
    label: '公司库',
    description: '保存并复用投标人资料',
    icon: 'building',
  },
  {
    key: 'search',
    label: '文档检索',
    description: '语义查找招标原文与页码',
    icon: 'search',
  },
  {
    key: 'library',
    label: '文档库',
    description: '已解析文档与历史投标',
    icon: 'folder',
  },
  {
    key: 'history',
    label: '任务历史',
    description: '查看本次会话的任务',
    icon: 'history',
  },
]

export const VIEW_META: Record<
  ViewKey,
  { title: string; subtitle: string }
> = {
  workbench: {
    title: '上传解析',
    subtitle: '上传招标文件，完成解析、抽取与向量入库',
  },
  bid: {
    title: '编写标书',
    subtitle: '选择已解析文档，填写投标人资料并启动多 Agent 编写',
  },
  companies: {
    title: '公司库',
    subtitle: '在 PostgreSQL 中保存、维护并复用投标人资料',
  },
  search: {
    title: '文档检索',
    subtitle: '在已解析招标文件中检索原文片段，语义优先，失败自动回退关键词',
  },
  library: {
    title: '文档库',
    subtitle: '浏览已解析文档，查看解析产物和历史投标文件',
  },
  history: {
    title: '任务历史',
    subtitle: '追踪本机提交的解析与投标任务',
  },
}

import type { AuthRole, UserRole } from '../api/types'

export const ROLE_LABELS: Record<AuthRole, string> = {
  admin: '管理员',
  bid_manager: '投标经理',
  writer: '编写员',
  reviewer: '审核员',
  viewer: '查看者',
  service: '服务账号',
}

export const ASSIGNABLE_ROLES: UserRole[] = [
  'viewer',
  'writer',
  'reviewer',
  'bid_manager',
  'admin',
]

export const ROLE_HINTS: Record<UserRole, string> = {
  viewer: '只读：查看文档、run、公司摘要与产物，不能发起或修改任何任务',
  writer: '可上传解析、创建/续跑/编辑标书章节并重新检查合规，不能管理公司或导出',
  reviewer: '只读 + 重新执行合规检查与生成修复方案，不能发起或编辑标书',
  bid_manager: '编写员权限 + 维护公司库、常规导出与评审操作',
  admin: '全部权限：用户管理、删除公司、强制导出',
}

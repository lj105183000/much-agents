export type ProfileObject = Record<string, unknown>

export interface RequiredField {
  path: string[]
  label: string
  placeholder: string
  group: string
}

export const PROFILE_GROUPS = [
  '公司信息',
  '法定代表人',
  '委托代理人',
  '项目经理',
  '投标信息',
] as const

export const REQUIRED_FIELDS: RequiredField[] = [
  {
    path: ['company', 'name'],
    label: '公司名称',
    placeholder: '与营业执照一致',
    group: '公司信息',
  },
  {
    path: ['company', 'address'],
    label: '公司地址',
    placeholder: '注册地址或通讯地址',
    group: '公司信息',
  },
  {
    path: ['company', 'phone'],
    label: '联系电话',
    placeholder: '固定电话或手机',
    group: '公司信息',
  },
  {
    path: ['company', 'bank_name'],
    label: '开户银行',
    placeholder: '银行名称',
    group: '公司信息',
  },
  {
    path: ['company', 'bank_account'],
    label: '银行账号',
    placeholder: '基本户账号',
    group: '公司信息',
  },
  {
    path: ['legal_representative', 'name'],
    label: '法定代表人姓名',
    placeholder: '姓名',
    group: '法定代表人',
  },
  {
    path: ['legal_representative', 'id_number'],
    label: '法定代表人身份证号',
    placeholder: '身份证号',
    group: '法定代表人',
  },
  {
    path: ['authorized_agent', 'name'],
    label: '委托代理人姓名',
    placeholder: '姓名',
    group: '委托代理人',
  },
  {
    path: ['authorized_agent', 'id_number'],
    label: '委托代理人身份证号',
    placeholder: '身份证号',
    group: '委托代理人',
  },
  {
    path: ['project_manager', 'name'],
    label: '项目经理姓名',
    placeholder: '姓名',
    group: '项目经理',
  },
  {
    path: ['project_manager', 'id_number'],
    label: '项目经理身份证号',
    placeholder: '身份证号',
    group: '项目经理',
  },
  {
    path: ['project_manager', 'certificate_number'],
    label: '项目经理证书编号',
    placeholder: '建造师注册证书编号',
    group: '项目经理',
  },
  {
    path: ['bid', 'bid_bond_amount'],
    label: '投标保证金金额',
    placeholder: '例如：人民币 5 万元',
    group: '投标信息',
  },
  {
    path: ['bid', 'bid_validity_days'],
    label: '投标有效期',
    placeholder: '例如：90 天',
    group: '投标信息',
  },
]

function cloneProfile(profile: ProfileObject): ProfileObject {
  try {
    return JSON.parse(JSON.stringify(profile)) as ProfileObject
  } catch {
    return {}
  }
}

export function getPathValue(profile: ProfileObject, path: string[]): string {
  let current: unknown = profile
  for (const key of path) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) {
      return ''
    }
    current = (current as Record<string, unknown>)[key]
  }
  if (current === null || current === undefined) return ''
  if (typeof current === 'string') return current
  if (typeof current === 'number' || typeof current === 'boolean') {
    return String(current)
  }
  return ''
}

export function setPathValue(
  profile: ProfileObject,
  path: string[],
  rawValue: string,
): ProfileObject {
  const next = cloneProfile(profile)
  let cursor: Record<string, unknown> = next
  for (let index = 0; index < path.length - 1; index += 1) {
    const key = path[index]
    const child = cursor[key]
    if (!child || typeof child !== 'object' || Array.isArray(child)) {
      cursor[key] = {}
    }
    cursor = cursor[key] as Record<string, unknown>
  }
  const last = path[path.length - 1]
  const value = rawValue.trim()
  if (value) {
    cursor[last] = value
  } else {
    delete cursor[last]
  }
  return next
}

export function missingRequiredFields(profile: ProfileObject): RequiredField[] {
  return REQUIRED_FIELDS.filter(
    (field) => !getPathValue(profile, field.path).trim(),
  )
}

export function parseProfileText(text: string): ProfileObject {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (error) {
    throw new Error(
      `JSON 格式错误：${error instanceof Error ? error.message : '无法解析'}`,
    )
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('profile 必须是 JSON 对象，不能是数组或基本类型')
  }
  return parsed as ProfileObject
}

export function prettyProfile(profile: ProfileObject): string {
  return JSON.stringify(profile, null, 2)
}

export function profileSizeBytes(profile: ProfileObject): number {
  return new TextEncoder().encode(JSON.stringify(profile)).length
}

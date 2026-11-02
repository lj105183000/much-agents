import { expect, test, type Page } from '@playwright/test'

const ADMIN_USERNAME = process.env.E2E_USERNAME ?? 'e2e-admin'
const ADMIN_PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-admin-password-123'
const VIEWER_USERNAME = process.env.E2E_VIEWER_USERNAME ?? 'e2e-viewer'
const VIEWER_PASSWORD =
  process.env.E2E_VIEWER_PASSWORD ?? 'e2e-viewer-password-123'
const DOC_ID = process.env.E2E_DOC_ID ?? 'e2e-doc'
const SAMPLE_PDF = process.env.E2E_SAMPLE_PDF ?? ''

async function login(page: Page, username: string, password: string) {
  await page.goto('/')
  await expect(
    page.getByRole('heading', { name: '登录工作台' }),
  ).toBeVisible()
  await page.locator('#login-username').fill(username)
  await page.locator('#login-password').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(
    page.getByRole('navigation', { name: '主导航' }),
  ).toBeVisible()
}

async function navigate(page: Page, label: RegExp) {
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('button', { name: label })
    .click()
}

async function logout(page: Page) {
  await page.getByRole('button', { name: '退出登录' }).first().click()
  await expect(
    page.getByRole('heading', { name: '登录工作台' }),
  ).toBeVisible()
}

test('rejects invalid credentials', async ({ page }) => {
  await page.goto('/')
  await page.locator('#login-username').fill(ADMIN_USERNAME)
  await page.locator('#login-password').fill('definitely-wrong-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByText('用户名或密码错误')).toBeVisible()
  await expect(
    page.getByRole('navigation', { name: '主导航' }),
  ).toHaveCount(0)
})

test('admin creates a bid run and resolves a review comment', async ({
  page,
}) => {
  await login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
  await navigate(page, /编写标书/)
  await expect(
    page.getByRole('heading', { name: '选择解析文档' }),
  ).toBeVisible()

  await page.locator('#bid-doc-id').fill(DOC_ID)
  await expect(page.getByText('可编写标书')).toBeVisible()
  await page.getByRole('button', { name: '创建大纲' }).click()

  await expect(
    page.getByRole('heading', { name: '生成与检查' }),
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: '评审评论与指派', exact: true }),
  ).toBeVisible()

  const body = `E2E 评审意见 ${Date.now()}`
  await page.getByTestId('comment-body').fill(body)
  await page.getByTestId('comment-submit').click()

  const card = page.getByTestId('comment-card').filter({ hasText: body })
  await expect(card).toBeVisible()
  await expect(card.getByText('待处理')).toBeVisible()
  await card.getByRole('button', { name: '标记解决' }).click()
  await expect(card.getByText('已解决')).toBeVisible()

  await logout(page)
})

test('viewer is read-only', async ({ page }) => {
  await login(page, VIEWER_USERNAME, VIEWER_PASSWORD)
  await navigate(page, /编写标书/)
  await expect(
    page.getByRole('heading', { name: '选择解析文档' }),
  ).toBeVisible()
  await expect(page.getByText('当前账号没有编写权限')).toBeVisible()
  await expect(
    page.getByRole('button', { name: '创建大纲' }),
  ).toBeDisabled()
  await logout(page)
})

test('uploads and parses a PDF with PyMuPDF', async ({ page }) => {
  test.skip(!SAMPLE_PDF, 'E2E_SAMPLE_PDF is not configured')
  await login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
  await expect(
    page.getByRole('heading', { name: '上传招标文件', exact: true }),
  ).toBeVisible()

  await page.locator('input[type="file"]').setInputFiles(SAMPLE_PDF)
  await page.locator('#parser').selectOption('pymupdf')

  const extractSwitch = page.getByRole('switch', { name: /信息抽取/ })
  const ingestSwitch = page.getByRole('switch', { name: /向量入库/ })
  if ((await extractSwitch.getAttribute('aria-checked')) === 'true') {
    await extractSwitch.click()
  }
  if ((await ingestSwitch.getAttribute('aria-checked')) === 'true') {
    await ingestSwitch.click()
  }

  await page.getByRole('button', { name: '开始解析' }).click()
  await expect(page.getByText('解析完成')).toBeVisible({ timeout: 120_000 })
})

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const WORKBENCH_COMPONENTS = [
  'app/(dashboard)/layout.tsx',
  'components/workbench/personal-engineering-workbench.tsx',
  'components/workbench/workspace-file-panel.tsx',
  'components/layout/brand-mark.tsx',
  'components/layout/sidebar.tsx',
] as const

test('P6 workbench never renders text below the 12px readability floor', () => {
  const undersizedArbitraryFont = /text-\[(?:[0-9]|1[01])px\]/g

  for (const relativePath of WORKBENCH_COMPONENTS) {
    const source = readFileSync(path.join(process.cwd(), relativePath), 'utf8')
    assert.deepEqual(
      source.match(undersizedArbitraryFont) ?? [],
      [],
      `${relativePath} must keep all rendered text at 12px or larger`,
    )
  }
})

test('P6 AI employee workbench presents its product shell in Chinese', () => {
  const workbench = readFileSync(path.join(process.cwd(), 'app/(dashboard)/agents/page.tsx'), 'utf8')
  const dashboardLayout = readFileSync(path.join(process.cwd(), 'app/(dashboard)/layout.tsx'), 'utf8')
  const sidebar = readFileSync(path.join(process.cwd(), 'components/layout/sidebar.tsx'), 'utf8')

  for (const englishCopy of [
    'AI Employee Workbench',
    'Select a Workspace to begin',
    'Invocation target',
    'Workspace surfaces',
    'Runtime posture',
    'Create an AI employee',
    'Ask your Agent to research',
  ]) {
    assert.equal(workbench.includes(englishCopy), false, `workbench must localize: ${englishCopy}`)
  }

  assert.match(workbench, /AI 员工工作台/)
  assert.match(workbench, /调用目标/)
  assert.match(dashboardLayout, /OmniBase \/ 工作会话/)
  assert.match(sidebar, /本地自托管空间/)
})

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

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'

test('native Skill surface is Chinese and discloses the non-escalating boundary', () => {
  const source = readFileSync(
    path.join(process.cwd(), 'app', '(dashboard)', 'skills', 'page.tsx'),
    'utf8',
  )
  assert.match(source, /原生技能/)
  assert.match(source, /无工具/)
  assert.match(source, /无网络/)
  assert.match(source, /无密钥/)
  assert.match(source, /不会获得工具、网络、密钥、MCP、规划器或多 Agent\s+权限/)
  assert.doesNotMatch(source, /coming soon|MCP enabled/i)
})

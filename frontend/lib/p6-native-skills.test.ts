import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'

test('personal capability center is Chinese and discloses the non-escalating boundary', () => {
  const source = readFileSync(
    path.join(process.cwd(), 'app', '(dashboard)', 'skills', 'page.tsx'),
    'utf8',
  )
  assert.match(source, /个人能力中心/u)
  assert.match(source, /纯指令/u)
  assert.match(source, /无工具/u)
  assert.match(source, /无网络/u)
  assert.match(source, /无密钥/u)
  assert.match(source, /扫描不会执行、安装或联网/u)
  assert.match(source, /未接入 Agent Alpha/u)
  assert.doesNotMatch(source, /coming soon|MCP enabled/iu)
})

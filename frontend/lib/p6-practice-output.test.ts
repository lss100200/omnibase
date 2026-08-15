import assert from 'node:assert/strict'
import { test } from 'node:test'

import { parseP6PracticeOutput } from './p6-practice-output'

test('RAG output accepts a closed citation-index schema', async () => {
  const value = await parseP6PracticeOutput(
    'rag',
    JSON.stringify({
      answer: 'The code is ORCHID-417 [1].',
      claims: [{ fact_id: 'orchid', statement: 'The code is ORCHID-417.', citation_indices: [1] }],
      abstained: false,
    }),
  )
  assert.equal(value.kind, 'rag')
  if (value.kind === 'rag') assert.deepEqual(value.claims[0]?.citationIndices, [1])
})

test('clock renderer escapes model text and accepts no arbitrary markup', async () => {
  const value = await parseP6PracticeOutput(
    'artifact',
    JSON.stringify({
      artifact_type: 'clock_html',
      title: '<img src=x onerror=alert(1)>',
      specification: { accent: '#112233' },
      acceptance_checks: ['Time changes after one second.'],
    }),
  )
  assert.equal(value.kind, 'artifact')
  if (value.kind === 'artifact') {
    assert.equal(value.filename, 'clock.html')
    assert.match(value.sha256, /^[0-9a-f]{64}$/)
    assert.ok(value.html.includes('&lt;img src=x onerror=alert(1)&gt;'))
    assert.ok(!value.html.includes('<img src=x'))
    assert.ok(!value.html.includes('https://'))
  }
})

test('slides are offline HTML and never represented as PPTX', async () => {
  const value = await parseP6PracticeOutput(
    'artifact',
    JSON.stringify({
      artifact_type: 'slides_html',
      title: 'OmniBase',
      specification: {
        slides: [{ heading: 'P6.4', bullets: ['真实 Agent', '可逆修改'] }],
      },
      acceptance_checks: ['Opens offline.'],
    }),
  )
  assert.equal(value.kind, 'artifact')
  if (value.kind === 'artifact') {
    assert.equal(value.filename, 'slides.html')
    assert.equal(value.mediaType, 'text/html; charset=utf-8')
  }
  await assert.rejects(
    parseP6PracticeOutput(
      'artifact',
      JSON.stringify({
        artifact_type: 'pptx',
        title: 'No false claim',
        specification: {},
        acceptance_checks: [],
      }),
    ),
    /p6_practice_artifact_schema_invalid/,
  )
})

test('workspace output is exactly one bounded POSIX-path replacement', async () => {
  const value = await parseP6PracticeOutput(
    'workspace',
    JSON.stringify({
      summary: 'Update the fixture heading.',
      changes: [
        {
          path: 'src/index.html',
          expected_before_sha256: 'a'.repeat(64),
          after_text: '<h1>Updated</h1>',
        },
      ],
      tests: ['Open the fixture in a browser.'],
    }),
  )
  assert.equal(value.kind, 'workspace')
  if (value.kind === 'workspace') assert.equal(value.change.path, 'src/index.html')
})

test('workspace proposal rejects traversal, secret paths and extra keys', async () => {
  for (const path of ['../outside.txt', '.git/config', '.env', 'src\\index.html']) {
    await assert.rejects(
      parseP6PracticeOutput(
        'workspace',
        JSON.stringify({
          summary: 'Unsafe',
          changes: [
            {
              path,
              expected_before_sha256: 'a'.repeat(64),
              after_text: 'value',
            },
          ],
          tests: [],
        }),
      ),
      /p6_practice_change_schema_invalid/,
    )
  }
  await assert.rejects(
    parseP6PracticeOutput(
      'rag',
      JSON.stringify({ answer: 'x', claims: [], abstained: false, hidden: 'not allowed' }),
    ),
    /p6_practice_rag_schema_invalid/,
  )
})

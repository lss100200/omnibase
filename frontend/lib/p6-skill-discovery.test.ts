import assert from 'node:assert/strict'
import test from 'node:test'
import { scanP6SkillCandidates } from './p6-skill-discovery'

const bytes = (value: string) => new TextEncoder().encode(value)

test('scan-only Skill discovery reports a safe instruction candidate without installing it', async () => {
  const report = await scanP6SkillCandidates([
    {
      sourceId: 'selected-root/skill-a',
      directoryName: 'skill-a',
      skillMarkdown: bytes(
        '---\nname: Review Helper\ndescription: Reviews a bounded diff.\n---\nInstructions.',
      ),
      siblingNames: ['SKILL.md', 'README.md'],
      linked: false,
    },
  ])
  assert.equal(report.candidates[0]?.status, 'unsupported_unreviewed')
  assert.match(report.candidates[0]?.digest ?? '', /^[0-9a-f]{64}$/u)
  assert.equal(report.executionPerformed, false)
  assert.equal(report.installationPerformed, false)
  assert.equal(report.networkUsed, false)
})

test('scan-only Skill discovery rejects capabilities, scripts, links and malformed metadata', async () => {
  const report = await scanP6SkillCandidates([
    {
      sourceId: 'selected-root/unsafe',
      directoryName: 'unsafe',
      skillMarkdown: bytes(
        '---\nname: Unsafe\ndescription: Unsafe candidate.\ntools: shell\nnetwork: allow\n---\nRun it.',
      ),
      siblingNames: ['SKILL.md', 'run.ps1', '.env'],
      linked: true,
    },
  ])
  const candidate = report.candidates[0]!
  assert.equal(candidate.status, 'rejected')
  assert.ok(candidate.blockers.includes('skill_link_forbidden'))
  assert.ok(candidate.blockers.includes('skill_capability_forbidden:tools'))
  assert.ok(candidate.blockers.includes('skill_capability_forbidden:network'))
  assert.ok(candidate.blockers.includes('skill_sibling_unsupported:run.ps1'))
  assert.ok(candidate.blockers.includes('skill_sibling_unsupported:.env'))
})

test('scan-only Skill discovery is deterministic and rejects invalid UTF-8', async () => {
  const input = {
    sourceId: 'selected-root/binary',
    directoryName: 'binary',
    skillMarkdown: new Uint8Array([0xff, 0xfe, 0x00]),
    siblingNames: ['SKILL.md'],
    linked: false,
  }
  const first = await scanP6SkillCandidates([input])
  const second = await scanP6SkillCandidates([input])
  assert.deepEqual(first, second)
  assert.ok(first.candidates[0]?.blockers.includes('skill_text_encoding_invalid'))
  assert.ok(first.candidates[0]?.blockers.includes('skill_binary_forbidden'))
})

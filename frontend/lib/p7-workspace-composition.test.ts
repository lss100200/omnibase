import assert from 'node:assert/strict'
import { test } from 'node:test'

import type {
  DesktopApplicationPreference,
  DesktopMessage,
  DesktopWorkspaceCompositionProfileValue,
  DesktopWorkspaceCompositionProposal,
} from './desktop-bridge'
import {
  P7_COMPOSITION_SLOT_IDS,
  p7CloneCompositionProfile,
  p7CompositionAssistantPrompt,
  p7CompositionDiff,
  p7CompositionLayoutChoiceEnabled,
  p7CompositionProjection,
  p7CompositionProposalReview,
  p7CompositionSlotEnabled,
  p7EffectiveDensity,
  p7FindNewAssistantCompositionMessage,
  p7ParseAssistantCompositionEnvelope,
  p7PatchCompositionProfile,
  p7ProfilePayload,
  p7WorkspaceSelectionChangesScope,
} from './p7-workspace-composition'

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`

function profile(): DesktopWorkspaceCompositionProfileValue {
  return p7CloneCompositionProfile({
    schemaVersion: 1,
    template: { id: 'standard-workbench', version: 1 },
    appearance: { density: 'inherit', quietChrome: false },
    layout: {
      agentPanel: 'open',
      bottomPanel: 'output',
      focusMode: false,
      sidebar: 'explorer',
    },
    slots: Object.fromEntries(
      P7_COMPOSITION_SLOT_IDS.map((slotId) => [
        slotId,
        ![
          'knowledge.ebook',
          'mcp.catalog',
          'sandbox.runtime',
          'skills.catalog',
          'source-control',
          'terminal',
        ].includes(slotId),
      ]),
    ) as DesktopWorkspaceCompositionProfileValue['slots'],
  })
}

function snapshot(workspaceId: string) {
  return {
    profile: {
      workspaceId,
      revision: 1,
      profileSha256: '1'.repeat(64),
      sourceKind: 'system' as const,
      proposalId: null,
      value: profile(),
      createdAt: '2026-08-29T00:00:00Z',
    },
    revisions: [],
    proposals: [],
    slotCatalog: [],
    audit: [],
  }
}

test('composition projection never exposes a previous Workspace on the first frame', () => {
  const old = snapshot(WORKSPACE_A)
  assert.deepEqual(
    p7CompositionProjection({
      loadedWorkspaceId: WORKSPACE_A,
      viewWorkspaceId: WORKSPACE_B,
      status: 'ready',
      snapshot: old,
    }),
    { status: 'loading', snapshot: null },
  )
  assert.equal(
    p7CompositionProjection({
      loadedWorkspaceId: WORKSPACE_A,
      viewWorkspaceId: WORKSPACE_A,
      status: 'ready',
      snapshot: old,
    }).snapshot,
    old,
  )
  assert.deepEqual(
    p7CompositionProjection({
      loadedWorkspaceId: WORKSPACE_A,
      viewWorkspaceId: WORKSPACE_A,
      status: 'error',
      snapshot: null,
    }),
    { status: 'error', snapshot: null },
  )
})

test('selecting the current Workspace is a no-op instead of invalidating its Profile', () => {
  assert.equal(p7WorkspaceSelectionChangesScope(WORKSPACE_A, WORKSPACE_A), false)
  assert.equal(p7WorkspaceSelectionChangesScope(WORKSPACE_A, WORKSPACE_B), true)
  assert.equal(p7WorkspaceSelectionChangesScope(null, WORKSPACE_A), true)
})

test('effective density obeys Workspace override then application preference', () => {
  const preference: DesktopApplicationPreference = {
    density: 'comfortable',
    reduceMotion: false,
    rowVersion: 1,
    updatedAt: '2026-08-29T00:00:00Z',
  }
  assert.equal(p7EffectiveDensity(preference, profile()), 'comfortable')
  assert.equal(
    p7EffectiveDensity(
      preference,
      p7PatchCompositionProfile(profile(), { appearance: { density: 'compact' } }),
    ),
    'compact',
  )
  assert.equal(p7EffectiveDensity(null, null), 'compact')
})

test('unloaded composition preserves only recovery-critical Slots until a verified profile arrives', () => {
  assert.equal(p7CompositionSlotEnabled(null, 'conversation.transcript'), true)
  assert.equal(p7CompositionSlotEnabled(null, 'settings.center'), true)
  assert.equal(
    p7CompositionSlotEnabled(null, 'workspace.explorer'),
    true,
    'first launch and safe-mode recovery must retain the Workspace creation entrypoint',
  )
  assert.equal(p7CompositionSlotEnabled(null, 'agent.rail'), false)
  assert.equal(p7CompositionSlotEnabled(null, 'event.agent-log'), false)
  assert.equal(p7CompositionSlotEnabled(profile(), 'workspace.explorer'), true)
})

test('profile patch and exact Diff preserve the immutable template and closed Slots', () => {
  const current = profile()
  const desired = p7PatchCompositionProfile(current, {
    appearance: { quietChrome: true },
    layout: { agentPanel: 'closed' },
    slots: { 'agent.rail': false },
  })
  assert.deepEqual(desired.template, { id: 'standard-workbench', version: 1 })
  assert.deepEqual(
    p7CompositionDiff(current, desired).map((row) => row.key),
    ['appearance.quietChrome', 'layout.agentPanel', 'slots.agent.rail'],
  )
  assert.deepEqual(Object.keys(desired.slots).sort(), [...P7_COMPOSITION_SLOT_IDS].sort())
})

test('layout choices cannot target a disabled Slot', () => {
  const current = profile()
  const disabled = p7PatchCompositionProfile(current, {
    slots: {
      'agent.rail': false,
      'workspace.explorer': false,
      'run.history': false,
      'workspace.brief': false,
      'event.output': false,
      'event.agent-log': false,
    },
    layout: { agentPanel: 'closed', sidebar: 'hidden', bottomPanel: 'hidden' },
  })
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'sidebar', value: 'explorer' }),
    false,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'sidebar', value: 'run' }),
    false,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'sidebar', value: 'blackboard' }),
    false,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'agentPanel', value: 'open' }),
    false,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'bottomPanel', value: 'output' }),
    false,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, {
      field: 'bottomPanel',
      value: 'agent-log',
    }),
    false,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'sidebar', value: 'hidden' }),
    true,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'agentPanel', value: 'closed' }),
    true,
  )
  assert.equal(
    p7CompositionLayoutChoiceEnabled(disabled, { field: 'bottomPanel', value: 'hidden' }),
    true,
  )
})

test('proposal review uses its exact base revision and disables stale approval', () => {
  const current = snapshot(WORKSPACE_A)
  const withHistory = { ...current, revisions: [current.profile] }
  const proposal: DesktopWorkspaceCompositionProposal = {
    id: `proposal_${'4'.repeat(32)}`,
    workspaceId: WORKSPACE_A,
    baseRevision: 1,
    baseProfileSha256: current.profile.profileSha256,
    sourceKind: 'owner',
    sourceReference: null,
    desiredProfileSha256: '2'.repeat(64),
    requestSha256: '3'.repeat(64),
    desiredProfile: p7PatchCompositionProfile(profile(), {
      appearance: { density: 'compact' },
    }),
    decision: null,
    appliedRevision: null,
    createdAt: '2026-08-29T00:01:00Z',
    decidedAt: null,
  }
  const currentReview = p7CompositionProposalReview(withHistory, proposal)
  assert.equal(currentReview.base?.revision, 1)
  assert.equal(currentReview.approvable, true)

  const revision2 = {
    ...current.profile,
    revision: 2,
    profileSha256: '5'.repeat(64),
    sourceKind: 'owner' as const,
    proposalId: `proposal_${'6'.repeat(32)}`,
  }
  const staleReview = p7CompositionProposalReview(
    { ...current, profile: revision2, revisions: [revision2, current.profile] },
    proposal,
  )
  assert.equal(staleReview.base?.revision, 1)
  assert.equal(staleReview.approvable, false)
  assert.deepEqual(p7CompositionProposalReview(current, proposal), {
    base: null,
    approvable: false,
  })
})

test('assistant prompt is bounded and forbids capability expansion', () => {
  const prompt = p7CompositionAssistantPrompt('让界面更安静并关闭 Agent 面板', profile())
  assert.ok(prompt?.includes('不得请求、安装或启用插件、MCP、Skill、沙箱、终端'))
  assert.ok(prompt?.includes('omnibase.workspace-composition.proposal.v1'))
  assert.equal(p7CompositionAssistantPrompt(' '.repeat(3), profile()), null)
  assert.equal(p7CompositionAssistantPrompt('a'.repeat(2_001), profile()), null)
})

test('assistant envelope accepts only exact complete JSON and new completed messages', () => {
  const desired = p7PatchCompositionProfile(profile(), { appearance: { density: 'compact' } })
  const content = JSON.stringify({
    type: 'omnibase.workspace-composition.proposal.v1',
    desired_profile: p7ProfilePayload(desired),
  })
  assert.deepEqual(p7ParseAssistantCompositionEnvelope(content), desired)
  assert.equal(
    p7ParseAssistantCompositionEnvelope(
      JSON.stringify({
        type: 'omnibase.workspace-composition.proposal.v1',
        desired_profile: { ...p7ProfilePayload(desired), extra: true },
      }),
    ),
    null,
  )
  assert.equal(
    p7ParseAssistantCompositionEnvelope(
      JSON.stringify({
        type: 'omnibase.workspace-composition.proposal.v1',
        desired_profile: {
          ...p7ProfilePayload(desired),
          slots: { ...p7ProfilePayload(desired).slots, terminal: true },
        },
      }),
    ),
    null,
  )
  const invocationId = `invocation_${'3'.repeat(32)}`
  const old: DesktopMessage = {
    id: `message_${'1'.repeat(32)}`,
    role: 'assistant',
    content,
    status: 'completed',
    invocationId,
    retryOfMessageId: null,
    createdAt: '2026-08-29T00:00:00Z',
    invocation: {
      id: invocationId,
      providerId: `provider_${'4'.repeat(32)}`,
      requestedModel: 'model',
      actualModel: 'model',
      family: 'generic-openai-compatible',
      gear: 'standard',
      thinkingDepth: 'disabled',
      status: 'succeeded',
      durationMs: 1,
      inputTokens: 1,
      outputTokens: 1,
      totalTokens: 2,
      errorCode: null,
      errorRedacted: null,
      retryOfInvocationId: null,
      createdAt: '2026-08-29T00:00:00Z',
      updatedAt: '2026-08-29T00:00:01Z',
    },
  }
  const fresh = { ...old, id: `message_${'2'.repeat(32)}` }
  assert.equal(p7FindNewAssistantCompositionMessage([old, fresh], new Set([old.id]))?.id, fresh.id)
  assert.equal(
    p7FindNewAssistantCompositionMessage([{ ...fresh, status: 'unknown' }], new Set()),
    null,
  )
  assert.equal(
    p7FindNewAssistantCompositionMessage(
      [{ ...fresh, invocationId: null, invocation: null }],
      new Set(),
    ),
    null,
  )
})

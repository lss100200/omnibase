export type P6ProviderFamily = 'deepseek' | 'glm' | 'kimi' | 'gpt' | 'claude' | 'generic'
export type P6ReasoningGear = 'economy' | 'standard' | 'deep' | 'audit'

export interface P6ModelIdentity {
  readonly providerId: string | null
  readonly modelId: string | null
}

export interface P6ProviderProfile {
  readonly family: P6ProviderFamily
  readonly displayName: string
  readonly strengths: readonly string[]
  readonly adaptationInstruction: string
  readonly nativeReasoningControl: false
  readonly toolsEnabled: false
  readonly mcpEnabled: false
  readonly cliEnabled: false
  readonly visionEnabled: false
}

export interface P6GearProfile {
  readonly id: P6ReasoningGear
  readonly displayName: string
  readonly description: string
  readonly topK: number
  readonly contextCharacterBudget: number
  readonly targetOutputTokens: number
  readonly reasoningInstruction: string
}

export interface P6ContextCandidate {
  readonly id: string
  readonly label: string
  readonly content: string
  readonly priority: 'owner_explicit' | 'pinned' | 'open' | 'supporting'
  readonly required?: boolean
}

export type P6ContextCompilation =
  | {
      readonly ok: true
      readonly text: string
      readonly includedIds: readonly string[]
      readonly omittedIds: readonly string[]
      readonly usedCharacters: number
      readonly budgetCharacters: number
    }
  | {
      readonly ok: false
      readonly code: 'required_context_exceeds_budget' | 'invalid_context_candidate'
      readonly candidateId: string
      readonly budgetCharacters: number
    }

export interface P6TokenUsage {
  readonly inputTokens: number
  readonly outputTokens: number
  readonly reasoningTokens?: number
}

export interface P6TokenRates {
  readonly inputPerMillion: number
  readonly outputPerMillion: number
  readonly reasoningPerMillion?: number
  readonly currency: string
}

export type P6CostEstimate =
  | { readonly known: false; readonly reason: 'rate_not_configured' | 'invalid_usage' }
  | {
      readonly known: true
      readonly currency: string
      readonly amount: number
      readonly inputAmount: number
      readonly outputAmount: number
      readonly reasoningAmount: number
    }

const CLOSED_PROVIDER_PROFILES: Record<P6ProviderFamily, P6ProviderProfile> = {
  deepseek: {
    family: 'deepseek',
    displayName: 'DeepSeek',
    strengths: ['中文工程任务', '代码推理', '成本效率'],
    adaptationInstruction:
      'Use a compact engineering plan, keep implementation claims evidence-bound, and finish with a concrete self-check. Do not reveal hidden chain-of-thought; provide concise decision rationale only.',
    nativeReasoningControl: false,
    toolsEnabled: false,
    mcpEnabled: false,
    cliEnabled: false,
    visionEnabled: false,
  },
  glm: {
    family: 'glm',
    displayName: '智谱 GLM',
    strengths: ['中文结构化表达', '长任务拆解', '工程说明'],
    adaptationInstruction:
      'Prefer explicit Chinese structure, preserve identifiers exactly, and separate confirmed facts from proposals. Do not infer tool execution from textual instructions.',
    nativeReasoningControl: false,
    toolsEnabled: false,
    mcpEnabled: false,
    cliEnabled: false,
    visionEnabled: false,
  },
  kimi: {
    family: 'kimi',
    displayName: 'Kimi / Moonshot',
    strengths: ['长上下文阅读', '文档归纳', '引用整理'],
    adaptationInstruction:
      'Prioritize supplied file context, cite file labels when making claims, and state when context was omitted by the budget. Never claim access to files that were not supplied.',
    nativeReasoningControl: false,
    toolsEnabled: false,
    mcpEnabled: false,
    cliEnabled: false,
    visionEnabled: false,
  },
  gpt: {
    family: 'gpt',
    displayName: 'GPT',
    strengths: ['通用工程推理', '代码与产品协作', '指令遵循'],
    adaptationInstruction:
      'Use the requested engineering altitude, keep proposed edits distinct from completed edits, and report verification evidence explicitly. Tools remain unavailable in this P6.0 request.',
    nativeReasoningControl: false,
    toolsEnabled: false,
    mcpEnabled: false,
    cliEnabled: false,
    visionEnabled: false,
  },
  claude: {
    family: 'claude',
    displayName: 'Claude',
    strengths: ['代码审查', '长文档理解', '边界分析'],
    adaptationInstruction:
      'Keep a clear distinction between observations, risks and actions. Preserve security constraints verbatim and avoid broad rewrites when a bounded change is sufficient.',
    nativeReasoningControl: false,
    toolsEnabled: false,
    mcpEnabled: false,
    cliEnabled: false,
    visionEnabled: false,
  },
  generic: {
    family: 'generic',
    displayName: 'OpenAI-compatible model',
    strengths: ['通用文本与代码任务'],
    adaptationInstruction:
      'Follow the supplied context and security boundaries, distinguish evidence from proposals, and do not claim tool or filesystem access.',
    nativeReasoningControl: false,
    toolsEnabled: false,
    mcpEnabled: false,
    cliEnabled: false,
    visionEnabled: false,
  },
}

export const P6_GEAR_PROFILES: Record<P6ReasoningGear, P6GearProfile> = {
  economy: {
    id: 'economy',
    displayName: '经济挡',
    description: '短上下文、低检索量、直接交付。',
    topK: 2,
    contextCharacterBudget: 6_000,
    targetOutputTokens: 1_024,
    reasoningInstruction: 'Choose the shortest safe path and answer directly.',
  },
  standard: {
    id: 'standard',
    displayName: '标准挡',
    description: '平衡上下文、质量和成本。',
    topK: 5,
    contextCharacterBudget: 14_000,
    targetOutputTokens: 2_048,
    reasoningInstruction:
      'Check the main alternatives, then give a practical implementation answer.',
  },
  deep: {
    id: 'deep',
    displayName: '深度挡',
    description: '更多上下文与边界检查，适合复杂工程。',
    topK: 8,
    contextCharacterBudget: 22_000,
    targetOutputTokens: 4_096,
    reasoningInstruction:
      'Analyze dependencies and failure modes before the conclusion; expose concise rationale, not private chain-of-thought.',
  },
  audit: {
    id: 'audit',
    displayName: '审计挡',
    description: '优先保留证据、反例和未证明项。',
    topK: 8,
    contextCharacterBudget: 24_000,
    targetOutputTokens: 4_096,
    reasoningInstruction:
      'Audit every claim against supplied evidence, enumerate safety blockers, and mark unknown or unverified facts explicitly.',
  },
}

export function resolveP6ProviderFamily(identity: P6ModelIdentity): P6ProviderFamily {
  const value = `${identity.providerId ?? ''} ${identity.modelId ?? ''}`.toLowerCase()
  if (/deepseek/.test(value)) return 'deepseek'
  if (/(?:zhipu|bigmodel|chatglm|\bglm[-_.0-9])/.test(value)) return 'glm'
  if (/(?:moonshot|\bkimi\b)/.test(value)) return 'kimi'
  if (/(?:anthropic|claude)/.test(value)) return 'claude'
  if (/(?:openai|\bgpt[-_.0-9]|\bo[134](?:[-_.]|$))/.test(value)) return 'gpt'
  return 'generic'
}

export function getP6ProviderProfile(identity: P6ModelIdentity): P6ProviderProfile {
  return CLOSED_PROVIDER_PROFILES[resolveP6ProviderFamily(identity)]
}

export function buildP6AdaptationInstruction(
  identity: P6ModelIdentity,
  gear: P6ReasoningGear,
): string {
  const provider = getP6ProviderProfile(identity)
  const selected = P6_GEAR_PROFILES[gear]
  return `[P6.0 model adaptation]\nProvider family: ${provider.displayName}\nGear: ${selected.displayName}\n${provider.adaptationInstruction}\n${selected.reasoningInstruction}\nNative provider reasoning controls are not exposed by the current Agent Alpha API. Tools, MCP, CLI and autonomous delegation remain disabled.`
}

const PRIORITY_ORDER: Record<P6ContextCandidate['priority'], number> = {
  owner_explicit: 0,
  pinned: 1,
  open: 2,
  supporting: 3,
}

export function compileP6Context(
  candidates: readonly P6ContextCandidate[],
  budgetCharacters: number,
): P6ContextCompilation {
  if (!Number.isInteger(budgetCharacters) || budgetCharacters < 0) {
    return {
      ok: false,
      code: 'invalid_context_candidate',
      candidateId: '',
      budgetCharacters,
    }
  }
  const ids = new Set<string>()
  for (const candidate of candidates) {
    if (
      !candidate.id ||
      ids.has(candidate.id) ||
      !candidate.label.trim() ||
      !candidate.content.trim() ||
      !(candidate.priority in PRIORITY_ORDER)
    ) {
      return {
        ok: false,
        code: 'invalid_context_candidate',
        candidateId: candidate.id,
        budgetCharacters,
      }
    }
    ids.add(candidate.id)
  }
  const ordered = candidates
    .map((candidate, index) => ({ candidate, index }))
    .sort(
      (left, right) =>
        PRIORITY_ORDER[left.candidate.priority] - PRIORITY_ORDER[right.candidate.priority] ||
        left.index - right.index,
    )
  const sections: string[] = []
  const includedIds: string[] = []
  const omittedIds: string[] = []
  let usedCharacters = 0
  for (const { candidate } of ordered) {
    const section = `\n\n[Context: ${candidate.label}]\n${candidate.content}`
    if (usedCharacters + section.length > budgetCharacters) {
      if (candidate.required) {
        return {
          ok: false,
          code: 'required_context_exceeds_budget',
          candidateId: candidate.id,
          budgetCharacters,
        }
      }
      omittedIds.push(candidate.id)
      continue
    }
    sections.push(section)
    includedIds.push(candidate.id)
    usedCharacters += section.length
  }
  return {
    ok: true,
    text: sections.join(''),
    includedIds,
    omittedIds,
    usedCharacters,
    budgetCharacters,
  }
}

export function estimateP6Cost(usage: P6TokenUsage, rates?: P6TokenRates): P6CostEstimate {
  if (
    !Number.isFinite(usage.inputTokens) ||
    !Number.isFinite(usage.outputTokens) ||
    !Number.isFinite(usage.reasoningTokens ?? 0) ||
    usage.inputTokens < 0 ||
    usage.outputTokens < 0 ||
    (usage.reasoningTokens ?? 0) < 0
  ) {
    return { known: false, reason: 'invalid_usage' }
  }
  if (!rates) return { known: false, reason: 'rate_not_configured' }
  const inputAmount = (usage.inputTokens / 1_000_000) * rates.inputPerMillion
  const outputAmount = (usage.outputTokens / 1_000_000) * rates.outputPerMillion
  const reasoningAmount =
    ((usage.reasoningTokens ?? 0) / 1_000_000) *
    (rates.reasoningPerMillion ?? rates.outputPerMillion)
  return {
    known: true,
    currency: rates.currency,
    amount: inputAmount + outputAmount + reasoningAmount,
    inputAmount,
    outputAmount,
    reasoningAmount,
  }
}

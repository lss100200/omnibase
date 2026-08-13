export const P6_SKILL_SCAN_MAX_CANDIDATES = 128
export const P6_SKILL_SCAN_MAX_FILE_BYTES = 64 * 1024
export const P6_SKILL_SCAN_MAX_TOTAL_BYTES = 2 * 1024 * 1024

export type P6SkillScanStatus = 'compatible_not_installed' | 'unsupported_unreviewed' | 'rejected'

export interface P6SkillScanInput {
  readonly sourceId: string
  readonly directoryName: string
  readonly skillMarkdown: Uint8Array
  readonly siblingNames: readonly string[]
  readonly linked: boolean
}

export interface P6SkillCandidate {
  readonly sourceId: string
  readonly directoryName: string
  readonly displayName: string
  readonly description: string
  readonly digest: string
  readonly status: P6SkillScanStatus
  readonly blockers: readonly string[]
  readonly bytes: number
}

export interface P6SkillScanReport {
  readonly schemaVersion: 1
  readonly candidates: readonly P6SkillCandidate[]
  readonly totalBytes: number
  readonly truncated: boolean
  readonly executionPerformed: false
  readonly installationPerformed: false
  readonly networkUsed: false
}

const textDecoder = new TextDecoder('utf-8', { fatal: true })
const SAFE_SIBLING_NAMES = new Set(['skill.md', 'readme.md', 'license', 'license.md'])
const FORBIDDEN_FRONTMATTER_KEYS = new Set([
  'allowed-tools',
  'capabilities',
  'commands',
  'dependencies',
  'env',
  'hooks',
  'mcp',
  'network',
  'scripts',
  'secrets',
  'tools',
])
const FORBIDDEN_FILE_SUFFIXES = [
  '.bat',
  '.cmd',
  '.com',
  '.dll',
  '.exe',
  '.js',
  '.mjs',
  '.ps1',
  '.py',
  '.sh',
]

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(',')}}`
}

async function sha256(value: Uint8Array): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error('p6_skill_scan_digest_unavailable')
  const input = Uint8Array.from(value).buffer
  const digest = await globalThis.crypto.subtle.digest('SHA-256', input)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

function parseFrontmatter(text: string): {
  readonly displayName: string
  readonly description: string
  readonly blockers: readonly string[]
} {
  const blockers: string[] = []
  let displayName = ''
  let description = ''
  if (!text.startsWith('---\n')) {
    blockers.push('skill_frontmatter_missing')
    return { displayName, description, blockers }
  }
  const end = text.indexOf('\n---\n', 4)
  if (end < 0) {
    blockers.push('skill_frontmatter_unclosed')
    return { displayName, description, blockers }
  }
  const seen = new Set<string>()
  for (const rawLine of text.slice(4, end).split('\n')) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const separator = line.indexOf(':')
    if (separator <= 0) {
      blockers.push('skill_frontmatter_invalid')
      continue
    }
    const key = line.slice(0, separator).trim().toLocaleLowerCase()
    const value = line
      .slice(separator + 1)
      .trim()
      .replace(/^['"]|['"]$/gu, '')
    if (seen.has(key)) blockers.push('skill_frontmatter_duplicate_key')
    seen.add(key)
    if (FORBIDDEN_FRONTMATTER_KEYS.has(key)) blockers.push(`skill_capability_forbidden:${key}`)
    if (key === 'name') displayName = value
    else if (key === 'description') description = value
    else if (!['license', 'metadata'].includes(key)) blockers.push(`skill_field_unsupported:${key}`)
  }
  if (!displayName || displayName.length > 120) blockers.push('skill_name_invalid')
  if (!description || description.length > 500) blockers.push('skill_description_invalid')
  return { displayName, description, blockers }
}

function siblingBlockers(names: readonly string[]): readonly string[] {
  const blockers: string[] = []
  const normalized = new Set<string>()
  for (const rawName of names) {
    const name = rawName.normalize('NFKC').toLocaleLowerCase()
    if (normalized.has(name)) blockers.push('skill_sibling_duplicate')
    normalized.add(name)
    if (
      !SAFE_SIBLING_NAMES.has(name) ||
      FORBIDDEN_FILE_SUFFIXES.some((suffix) => name.endsWith(suffix)) ||
      name === '.env' ||
      name.startsWith('.env.')
    ) {
      blockers.push(`skill_sibling_unsupported:${name}`)
    }
  }
  return blockers
}

export async function scanP6SkillCandidates(
  inputs: readonly P6SkillScanInput[],
): Promise<P6SkillScanReport> {
  const candidates: P6SkillCandidate[] = []
  let totalBytes = 0
  let truncated = inputs.length > P6_SKILL_SCAN_MAX_CANDIDATES
  for (const input of inputs.slice(0, P6_SKILL_SCAN_MAX_CANDIDATES)) {
    const blockers: string[] = []
    if (!input.sourceId || input.sourceId.length > 200) blockers.push('skill_source_id_invalid')
    if (!input.directoryName || input.directoryName.length > 120)
      blockers.push('skill_directory_invalid')
    if (input.linked) blockers.push('skill_link_forbidden')
    if (input.skillMarkdown.byteLength === 0) blockers.push('skill_file_empty')
    if (input.skillMarkdown.byteLength > P6_SKILL_SCAN_MAX_FILE_BYTES)
      blockers.push('skill_file_too_large')
    totalBytes += input.skillMarkdown.byteLength
    if (totalBytes > P6_SKILL_SCAN_MAX_TOTAL_BYTES) {
      blockers.push('skill_scan_budget_exceeded')
      truncated = true
    }
    let text = ''
    try {
      text = textDecoder.decode(input.skillMarkdown)
    } catch {
      blockers.push('skill_text_encoding_invalid')
    }
    if (input.skillMarkdown.includes(0) || text.includes('\0'))
      blockers.push('skill_binary_forbidden')
    const parsed = parseFrontmatter(text)
    blockers.push(...parsed.blockers, ...siblingBlockers(input.siblingNames))
    const uniqueBlockers = [...new Set(blockers)].sort()
    const digest = await sha256(
      new TextEncoder().encode(
        canonicalJson({
          directoryName: input.directoryName,
          skillMarkdownSha256: await sha256(input.skillMarkdown),
          siblingNames: [...input.siblingNames].sort(),
        }),
      ),
    )
    candidates.push({
      sourceId: input.sourceId,
      directoryName: input.directoryName,
      displayName: parsed.displayName || input.directoryName,
      description: parsed.description || '未能读取安全、完整的 Skill 描述。',
      digest,
      status: uniqueBlockers.length > 0 ? 'rejected' : 'unsupported_unreviewed',
      blockers: uniqueBlockers,
      bytes: input.skillMarkdown.byteLength,
    })
  }
  return {
    schemaVersion: 1,
    candidates,
    totalBytes,
    truncated,
    executionPerformed: false,
    installationPerformed: false,
    networkUsed: false,
  }
}

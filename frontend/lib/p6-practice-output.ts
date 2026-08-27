export interface P6PracticeRagOutput {
  readonly kind: 'rag'
  readonly answer: string
  readonly claims: readonly {
    readonly factId: string
    readonly statement: string
    readonly citationIndices: readonly number[]
  }[]
  readonly abstained: boolean
}

export interface P6PracticeWorkspaceProposal {
  readonly kind: 'workspace'
  readonly summary: string
  readonly change: {
    readonly path: string
    readonly expectedBeforeSha256: string
    readonly afterText: string
  }
  readonly tests: readonly string[]
}

export interface P6RenderedArtifact {
  readonly kind: 'artifact'
  readonly artifactType: 'clock_html' | 'slides_html'
  readonly filename: 'clock.html' | 'slides.html'
  readonly mediaType: 'text/html; charset=utf-8'
  readonly title: string
  readonly html: string
  readonly sha256: string
  readonly byteLength: number
  readonly acceptanceChecks: readonly string[]
}

export type P6PracticeOutput =
  | P6PracticeRagOutput
  | P6PracticeWorkspaceProposal
  | P6RenderedArtifact

const SHA256 = /^[0-9a-f]{64}$/
const SAFE_PATH = /^(?!.*(?:^|\/)\.{1,2}(?:\/|$))(?!.*\\)(?!\/)[^\u0000-\u001f]+$/
const MAX_OUTPUT_CHARACTERS = 256 * 1024
const MAX_ARTIFACT_BYTES = 512 * 1024

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const observed = Object.keys(value).sort()
  const expected = [...keys].sort()
  return (
    observed.length === expected.length && observed.every((key, index) => key === expected[index])
  )
}

function requiredString(value: unknown, maximum: number): string | null {
  return typeof value === 'string' && value.trim() && value.length <= maximum ? value : null
}

function stringList(
  value: unknown,
  maximumItems: number,
  maximumCharacters: number,
): string[] | null {
  if (!Array.isArray(value) || value.length > maximumItems) return null
  const items = value.map((item) => requiredString(item, maximumCharacters))
  return items.some((item) => item === null) ? null : (items as string[])
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

async function digest(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value)
  const result = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(result), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function artifact(
  artifactType: P6RenderedArtifact['artifactType'],
  title: string,
  html: string,
  acceptanceChecks: readonly string[],
): Promise<P6RenderedArtifact> {
  const byteLength = new TextEncoder().encode(html).byteLength
  if (byteLength > MAX_ARTIFACT_BYTES) throw new Error('p6_practice_artifact_budget_exceeded')
  return {
    kind: 'artifact',
    artifactType,
    filename: artifactType === 'clock_html' ? 'clock.html' : 'slides.html',
    mediaType: 'text/html; charset=utf-8',
    title,
    html,
    sha256: await digest(html),
    byteLength,
    acceptanceChecks,
  }
}

async function parseArtifact(value: Record<string, unknown>): Promise<P6RenderedArtifact> {
  if (!exactKeys(value, ['artifact_type', 'title', 'specification', 'acceptance_checks'])) {
    throw new Error('p6_practice_artifact_schema_invalid')
  }
  const artifactType = value.artifact_type
  const title = requiredString(value.title, 100)
  const checks = stringList(value.acceptance_checks, 12, 240)
  const specification = value.specification
  if (
    (artifactType !== 'clock_html' && artifactType !== 'slides_html') ||
    title === null ||
    checks === null ||
    !isRecord(specification)
  ) {
    throw new Error('p6_practice_artifact_schema_invalid')
  }
  const safeTitle = escapeHtml(title)
  if (artifactType === 'clock_html') {
    if (!exactKeys(specification, ['accent'])) {
      throw new Error('p6_practice_clock_schema_invalid')
    }
    const accent = specification.accent
    if (typeof accent !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(accent)) {
      throw new Error('p6_practice_clock_accent_invalid')
    }
    return artifact(
      artifactType,
      title,
      `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>${safeTitle}</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { min-height: 100vh; margin: 0; display: grid; place-items: center; }
    main { text-align: center; padding: 2rem; border: 2px solid ${accent}; border-radius: 1rem; }
    #clock { font-size: clamp(3rem, 12vw, 8rem); font-variant-numeric: tabular-nums; }
  </style>
</head>
<body>
  <main><h1>${safeTitle}</h1><time id="clock" aria-live="off"></time></main>
  <script>
    const clock = document.getElementById('clock');
    const tick = () => { clock.textContent = new Date().toLocaleTimeString(); };
    tick(); setInterval(tick, 1000);
  </script>
</body>
</html>
`,
      checks,
    )
  }

  if (!exactKeys(specification, ['slides']) || !Array.isArray(specification.slides)) {
    throw new Error('p6_practice_slides_schema_invalid')
  }
  if (specification.slides.length < 1 || specification.slides.length > 12) {
    throw new Error('p6_practice_slides_schema_invalid')
  }
  const sections = specification.slides.map((item) => {
    if (!isRecord(item) || !exactKeys(item, ['heading', 'bullets'])) {
      throw new Error('p6_practice_slide_schema_invalid')
    }
    const heading = requiredString(item.heading, 120)
    const bullets = stringList(item.bullets, 8, 240)
    if (heading === null || bullets === null) throw new Error('p6_practice_slide_schema_invalid')
    return `<section tabindex="0"><h2>${escapeHtml(heading)}</h2><ul>${bullets
      .map((bullet) => `<li>${escapeHtml(bullet)}</li>`)
      .join('')}</ul></section>`
  })
  return artifact(
    artifactType,
    title,
    `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${safeTitle}</title><style>
body{margin:0;background:#111;color:#fff;font:24px system-ui,sans-serif}
main{scroll-snap-type:y mandatory;height:100vh;overflow-y:auto}
section{box-sizing:border-box;min-height:100vh;padding:10vh 10vw;scroll-snap-align:start}
h1,h2{font-size:clamp(2rem,6vw,5rem)}li{margin:.75rem 0}
</style></head><body><main><section><h1>${safeTitle}</h1></section>${sections.join('')}</main></body></html>
`,
    checks,
  )
}

function parseRag(value: Record<string, unknown>): P6PracticeRagOutput {
  if (!exactKeys(value, ['answer', 'claims', 'abstained'])) {
    throw new Error('p6_practice_rag_schema_invalid')
  }
  const answer = requiredString(value.answer, 64_000)
  if (answer === null || typeof value.abstained !== 'boolean' || !Array.isArray(value.claims)) {
    throw new Error('p6_practice_rag_schema_invalid')
  }
  if (value.claims.length > 32) throw new Error('p6_practice_rag_schema_invalid')
  const claims = value.claims.map((item) => {
    if (!isRecord(item) || !exactKeys(item, ['fact_id', 'statement', 'citation_indices'])) {
      throw new Error('p6_practice_claim_schema_invalid')
    }
    const factId = requiredString(item.fact_id, 128)
    const statement = requiredString(item.statement, 2_000)
    if (
      factId === null ||
      statement === null ||
      !Array.isArray(item.citation_indices) ||
      item.citation_indices.length > 16 ||
      item.citation_indices.some(
        (index) => typeof index !== 'number' || !Number.isInteger(index) || index < 1,
      ) ||
      new Set(item.citation_indices).size !== item.citation_indices.length
    ) {
      throw new Error('p6_practice_claim_schema_invalid')
    }
    return {
      factId,
      statement,
      citationIndices: item.citation_indices as number[],
    }
  })
  return { kind: 'rag', answer, claims, abstained: value.abstained }
}

function parseWorkspace(value: Record<string, unknown>): P6PracticeWorkspaceProposal {
  if (!exactKeys(value, ['summary', 'changes', 'tests'])) {
    throw new Error('p6_practice_workspace_schema_invalid')
  }
  const summary = requiredString(value.summary, 2_000)
  const tests = stringList(value.tests, 12, 240)
  if (
    summary === null ||
    tests === null ||
    !Array.isArray(value.changes) ||
    value.changes.length !== 1
  ) {
    throw new Error('p6_practice_workspace_schema_invalid')
  }
  const change = value.changes[0]
  if (!isRecord(change) || !exactKeys(change, ['path', 'expected_before_sha256', 'after_text'])) {
    throw new Error('p6_practice_change_schema_invalid')
  }
  const path = requiredString(change.path, 512)
  const expectedBeforeSha256 = requiredString(change.expected_before_sha256, 64)
  const afterText = requiredString(change.after_text, MAX_OUTPUT_CHARACTERS)
  if (
    path === null ||
    !SAFE_PATH.test(path) ||
    path
      .split('/')
      .some((part) => ['.git', '.env', 'node_modules', '__pycache__'].includes(part)) ||
    expectedBeforeSha256 === null ||
    !SHA256.test(expectedBeforeSha256) ||
    afterText === null
  ) {
    throw new Error('p6_practice_change_schema_invalid')
  }
  return {
    kind: 'workspace',
    summary,
    change: { path, expectedBeforeSha256, afterText },
    tests,
  }
}

export async function parseP6PracticeOutput(
  scenario: 'rag' | 'artifact' | 'workspace',
  raw: string,
): Promise<P6PracticeOutput> {
  if (!raw || raw.length > MAX_OUTPUT_CHARACTERS) {
    throw new Error('p6_practice_output_budget_exceeded')
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    throw new Error('p6_practice_output_not_json')
  }
  if (!isRecord(parsed)) throw new Error('p6_practice_output_not_object')
  if (scenario === 'rag') return parseRag(parsed)
  if (scenario === 'workspace') return parseWorkspace(parsed)
  return parseArtifact(parsed)
}

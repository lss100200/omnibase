import {
  P6_SKILL_SCAN_MAX_CANDIDATES,
  P6_SKILL_SCAN_MAX_FILE_BYTES,
  scanP6SkillCandidates,
  type P6SkillScanReport,
} from '@/lib/p6-skill-discovery'
import {
  pickP6Directory,
  requireP6Permission,
  type P6DirectoryHandle,
  type P6FileHandle,
} from '@/lib/p6-file-handles'

function opaqueSourceId(index: number): string {
  return `selected-root/candidate-${index + 1}`
}

async function readCandidate(
  directory: P6DirectoryHandle,
  index: number,
): Promise<Parameters<typeof scanP6SkillCandidates>[0][number] | null> {
  await requireP6Permission(directory, 'read', false)
  const siblingNames: string[] = []
  let skillFile: P6FileHandle | null = null
  for await (const entry of directory.values()) {
    siblingNames.push(entry.name)
    if (entry.kind === 'file' && entry.name.toLocaleLowerCase() === 'skill.md') skillFile = entry
    if (siblingNames.length > 32) break
  }
  if (!skillFile) return null
  await requireP6Permission(skillFile, 'read', false)
  const before = await skillFile.getFile()
  if (before.size > P6_SKILL_SCAN_MAX_FILE_BYTES) {
    return {
      sourceId: opaqueSourceId(index),
      directoryName: directory.name,
      skillMarkdown: new Uint8Array(P6_SKILL_SCAN_MAX_FILE_BYTES + 1),
      siblingNames,
      linked: false,
    }
  }
  const raw = new Uint8Array(await before.arrayBuffer())
  const after = await skillFile.getFile()
  if (before.size !== after.size || before.lastModified !== after.lastModified)
    throw new Error('p6_skill_scan_identity_drifted')
  return {
    sourceId: opaqueSourceId(index),
    directoryName: directory.name,
    skillMarkdown: raw,
    siblingNames,
    linked: false,
  }
}

export async function chooseAndScanP6SkillRoot(): Promise<P6SkillScanReport> {
  const root = await pickP6Directory()
  await requireP6Permission(root, 'read', false)
  const inputs: NonNullable<Awaited<ReturnType<typeof readCandidate>>>[] = []
  let candidateIndex = 0
  for await (const entry of root.values()) {
    if (entry.kind !== 'directory') continue
    const candidate = await readCandidate(entry, candidateIndex)
    candidateIndex += 1
    if (candidate) inputs.push(candidate)
    if (inputs.length >= P6_SKILL_SCAN_MAX_CANDIDATES) break
  }
  return scanP6SkillCandidates(inputs)
}

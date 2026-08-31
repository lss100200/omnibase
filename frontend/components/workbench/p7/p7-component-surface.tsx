'use client'

import { Component, useEffect, useMemo, useState, type ErrorInfo, type ReactNode } from 'react'
import {
  BookOpen,
  Boxes,
  FileText,
  FolderTree,
  Hash,
  ScrollText,
  Search,
  ShieldAlert,
  Workflow,
} from 'lucide-react'
import type {
  P7InstructionSkillSurface,
  P7KnowledgeEbookSurface,
  P7McpSurface,
  P7SandboxSurface,
  P7WorkspaceCanvasSurface,
  P7WorkspaceComponentSurface,
  P7WorkspaceComponentSurfaceProjection,
} from '@/lib/p7-workspace-components'

function safeModeLabel(reason: P7WorkspaceComponentSurfaceProjection['safeModeReason']): string {
  switch (reason) {
    case 'component-inactive':
      return '组件已停用'
    case 'emergency-stop':
      return '组件已紧急停止'
    case 'invocation-failed':
      return '组件调用未完成'
    case 'malformed-output':
      return '组件输出未通过宿主校验'
    default:
      return '组件不可用'
  }
}

function P7SandboxResult({ surface }: { readonly surface: P7SandboxSurface }) {
  return (
    <div className="p7-component-result-view">
      <header className="p7-component-view-head">
        <div>
          <span className="p7-settings-eyebrow">Sandbox Workload</span>
          <h2>{surface.workloadId}</h2>
        </div>
        <span className="p7-component-status p7-component-status-ok">{surface.status}</span>
      </header>
      <div className="p7-component-result-meta">
        <span>{surface.runtimeInstanceId}</span>
        <span>{surface.inputArtifactIds.length} input artifacts</span>
        <span>{surface.usage.wallTimeMs} ms</span>
      </div>
      <div className="p7-component-result-body">
        <div className="p7-component-hash-result">
          <Workflow size={18} />
          <div>
            <strong>{surface.result.artifactCount} output artifacts</strong>
            <span>
              transform {surface.result.transformValue} · {surface.usage.bytesIn} B in ·{' '}
              {surface.usage.bytesOut} B out
            </span>
            <code>{surface.result.fingerprintSha256}</code>
            <code>{surface.workloadSha256}</code>
          </div>
        </div>
      </div>
    </div>
  )
}

function P7ComponentSafeMode({
  reason,
}: {
  readonly reason: P7WorkspaceComponentSurfaceProjection['safeModeReason']
}) {
  return (
    <div className="p7-component-safe-mode" role="status">
      <ShieldAlert size={18} />
      <div>
        <strong>{safeModeLabel(reason)}</strong>
        <span>标准工作台保持可用。</span>
      </div>
    </div>
  )
}

class P7ComponentSurfaceErrorBoundary extends Component<
  { readonly resetKey: string; readonly children: ReactNode },
  { readonly failed: boolean }
> {
  override state = { failed: false }

  static getDerivedStateFromError(): { readonly failed: true } {
    return { failed: true }
  }

  override componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // The renderer owns no component code or secrets. The bounded fallback is
    // intentionally local; operational failure details stay in native audit.
  }

  override componentDidUpdate(previous: { readonly resetKey: string }): void {
    if (previous.resetKey !== this.props.resetKey && this.state.failed) {
      this.setState({ failed: false })
    }
  }

  override render(): ReactNode {
    return this.state.failed ? (
      <P7ComponentSafeMode reason="malformed-output" />
    ) : (
      this.props.children
    )
  }
}

function P7WorkspaceCanvas({ surface }: { readonly surface: P7WorkspaceComponentSurface }) {
  if (surface.kind !== 'workspace-canvas') return null
  return (
    <div className="p7-component-canvas">
      <header className="p7-component-view-head">
        <div>
          <span className="p7-settings-eyebrow">UI / Canvas</span>
          <h2>{surface.title}</h2>
        </div>
        <span className="p7-component-status p7-component-status-ok">active</span>
      </header>
      <div className="p7-component-canvas-grid" aria-label="Workspace Canvas">
        <section>
          <Boxes size={18} />
          <strong>编辑器画布</strong>
          <span>{surface.viewId}</span>
        </section>
        <dl>
          <div>
            <dt>Slot</dt>
            <dd>{surface.slotId}</dd>
          </div>
          <div>
            <dt>Renderer</dt>
            <dd>{surface.renderer}</dd>
          </div>
          <div>
            <dt>Operation</dt>
            <dd>{surface.operationId}</dd>
          </div>
          {surface.sections.map((section) => (
            <div key={section.label}>
              <dt>{section.label}</dt>
              <dd>{section.value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}

function readyCanvasSurfaces(
  projection: P7WorkspaceComponentSurfaceProjection,
): readonly P7WorkspaceCanvasSurface[] {
  return projection.status === 'ready'
    ? projection.surfaces.filter(
        (surface): surface is P7WorkspaceCanvasSurface => surface.kind === 'workspace-canvas',
      )
    : []
}

export function P7SidebarComponentSurface({
  projection,
}: {
  readonly projection: P7WorkspaceComponentSurfaceProjection
}) {
  const surfaces = readyCanvasSurfaces(projection)
  if (surfaces.length === 0 && projection.failures.length === 0) return null
  return (
    <>
      {surfaces.map((surface) => (
        <P7ComponentSurfaceErrorBoundary
          key={`${surface.componentId}:${surface.operationId}`}
          resetKey={`${surface.workspaceId}:${surface.operationId}`}
        >
          <section className="p7-component-sidebar-slot" aria-label={`组件 · ${surface.title}`}>
            <header>
              <Boxes size={14} />
              <span>{surface.title}</span>
            </header>
            <dl>
              {surface.sections.map((section) => (
                <div key={section.label}>
                  <dt>{section.label}</dt>
                  <dd>{section.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </P7ComponentSurfaceErrorBoundary>
      ))}
      {projection.failures.map((entry) => (
        <P7ComponentSafeMode key={entry.key} reason={entry.safeModeReason} />
      ))}
    </>
  )
}

export function P7StatusComponentSurface({
  projection,
}: {
  readonly projection: P7WorkspaceComponentSurfaceProjection
}) {
  const surfaces = readyCanvasSurfaces(projection)
  if (surfaces.length === 0) return null
  return (
    <>
      {surfaces.map((surface) => {
        const detail = surface.sections
          .map((section) => `${section.label}: ${section.value}`)
          .join(' · ')
        return (
          <P7ComponentSurfaceErrorBoundary
            key={`${surface.componentId}:${surface.operationId}`}
            resetKey={`${surface.workspaceId}:${surface.operationId}`}
          >
            <span
              className="p7-status-item p7-status-static p7-component-status-slot"
              title={detail === '' ? surface.componentId : detail}
            >
              <Boxes size={11} />
              <span>{surface.title}</span>
            </span>
          </P7ComponentSurfaceErrorBoundary>
        )
      })}
    </>
  )
}

function P7InstructionSkill({ surface }: { readonly surface: P7InstructionSkillSurface }) {
  return (
    <div className="p7-component-result-view">
      <header className="p7-component-view-head">
        <div>
          <span className="p7-settings-eyebrow">Instruction Skill</span>
          <h2>Owner-reviewable instructions</h2>
        </div>
        <span className="p7-component-status p7-component-status-ok">instruction only</span>
      </header>
      <div className="p7-component-result-meta">
        <span>{surface.skillId}</span>
        <span className="p7-component-digest" title={surface.taskSha256}>
          task {surface.taskSha256.slice(0, 12)}
        </span>
      </div>
      <div className="p7-component-result-body">
        <div className="p7-component-result-title">
          <ScrollText size={16} />
          <strong>解析后的受信指令</strong>
        </div>
        <pre>{surface.instructions}</pre>
      </div>
    </div>
  )
}

function P7McpResult({ surface }: { readonly surface: P7McpSurface }) {
  const result = surface.result
  return (
    <div className="p7-component-result-view">
      <header className="p7-component-view-head">
        <div>
          <span className="p7-settings-eyebrow">Read-only MCP</span>
          <h2>{result.tool}</h2>
        </div>
        <span className="p7-component-status p7-component-status-ok">read only</span>
      </header>
      <div className="p7-component-result-meta">
        <span>{'path' in result ? result.path : result.directoryPath || '/'}</span>
        <span>{surface.operationId}</span>
      </div>
      <div className="p7-component-result-body">
        {result.kind === 'list' && (
          <>
            <div className="p7-component-result-title">
              <FolderTree size={16} />
              <strong>{result.entries.length} 个条目</strong>
              {result.truncated && <span>结果已截断</span>}
            </div>
            <div className="p7-component-file-results">
              {result.entries.map((entry) => (
                <div key={entry.path}>
                  <span>{entry.kind}</span>
                  <strong>{entry.path}</strong>
                  <span>{entry.sizeBytes === null ? 'directory' : `${entry.sizeBytes} B`}</span>
                </div>
              ))}
            </div>
          </>
        )}
        {result.kind === 'read' && (
          <>
            <div className="p7-component-result-title">
              <FileText size={16} />
              <strong>{result.path}</strong>
              <span>{result.sizeBytes} B</span>
              <span className="p7-component-digest" title={result.sha256}>
                {result.sha256.slice(0, 12)}
              </span>
            </div>
            <pre>{result.content}</pre>
          </>
        )}
        {result.kind === 'hash' && (
          <div className="p7-component-hash-result">
            <Hash size={18} />
            <div>
              <strong>{result.path}</strong>
              <span>{result.sizeBytes} B</span>
              <code>{result.sha256}</code>
            </div>
          </div>
        )}
        {result.kind === 'search' && (
          <>
            <div className="p7-component-result-title">
              <Search size={16} />
              <strong>{result.matches.length} 个匹配</strong>
              {result.truncated && <span>结果已截断</span>}
            </div>
            <div className="p7-component-search-results">
              {result.matches.map((match) => (
                <div key={`${match.line}:${match.snippet}`}>
                  <span>{match.line}</span>
                  <code>{match.snippet}</code>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function P7KnowledgeEbook({ surface }: { readonly surface: P7KnowledgeEbookSurface }) {
  const [selectedDocumentId, setSelectedDocumentId] = useState(
    () => surface.catalog.documents[0]?.id ?? null,
  )
  useEffect(() => {
    setSelectedDocumentId(surface.catalog.documents[0]?.id ?? null)
  }, [surface.operationId, surface.catalog.documents])
  const selected = useMemo(
    () =>
      surface.catalog.documents.find((document) => document.id === selectedDocumentId) ??
      surface.catalog.documents[0] ??
      null,
    [selectedDocumentId, surface.catalog.documents],
  )
  return (
    <div className="p7-ebook-view">
      <header className="p7-component-view-head">
        <div>
          <span className="p7-settings-eyebrow">Local Adapter</span>
          <h2>OmniBase Knowledge Ebook</h2>
        </div>
        <span className="p7-component-digest" title={surface.assetSha256}>
          {surface.assetSha256.slice(0, 12)}
        </span>
      </header>
      <div className="p7-ebook-meta">
        <span>{surface.catalog.documents.length} 文档</span>
        <span>{surface.catalog.invariantCount} 约束</span>
        <span>{surface.catalog.moduleCount} 模块</span>
        <span>{surface.catalog.glossaryCount} 术语</span>
        <span>v{surface.catalog.componentVersion}</span>
      </div>
      <div className="p7-ebook-layout">
        <nav className="p7-ebook-documents" aria-label="知识文档">
          {surface.catalog.documents.length === 0 && (
            <div className="p7-muted-text">目录中没有文档。</div>
          )}
          {surface.catalog.documents.map((document) => (
            <button
              key={document.id}
              type="button"
              className={
                document.id === selected?.id ? 'p7-ebook-document active' : 'p7-ebook-document'
              }
              aria-current={document.id === selected?.id ? 'page' : undefined}
              onClick={() => setSelectedDocumentId(document.id)}
            >
              <FileText size={14} />
              <span>
                <strong>{document.title}</strong>
                <small>{document.type || document.id}</small>
              </span>
            </button>
          ))}
        </nav>
        <article className="p7-ebook-document-view">
          {selected === null ? (
            <div className="p7-code-empty">
              <BookOpen size={18} />
              <span>未选择文档</span>
            </div>
          ) : (
            <>
              <header>
                <h2>{selected.title}</h2>
                <span>{selected.id}</span>
              </header>
              {selected.summary && <p className="p7-ebook-summary">{selected.summary}</p>}
              {selected.content && <div className="p7-ebook-content">{selected.content}</div>}
              {selected.sections.map((section) => (
                <section key={section.id} className="p7-ebook-section">
                  {section.heading && <h3>{section.heading}</h3>}
                  {section.explanation && <p>{section.explanation}</p>}
                  {section.content && <div>{section.content}</div>}
                </section>
              ))}
            </>
          )}
        </article>
      </div>
    </div>
  )
}

export function P7ComponentSurface({
  projection,
}: {
  readonly projection: P7WorkspaceComponentSurfaceProjection
}) {
  if (projection.status === 'loading') {
    return <div className="p7-component-view-empty">正在读取当前工作空间组件…</div>
  }
  if (projection.status === 'safe-mode') {
    return <P7ComponentSafeMode reason={projection.safeModeReason} />
  }
  if (projection.entries.length === 0) {
    return <div className="p7-component-view-empty">当前工作空间没有打开的组件视图。</div>
  }
  return (
    <>
      {projection.entries.map((entry) =>
        entry.surface === null ? (
          <P7ComponentSafeMode key={entry.key} reason={entry.safeModeReason} />
        ) : (
          <P7ComponentSurfaceErrorBoundary
            key={entry.key}
            resetKey={`${entry.key}:${entry.surface.operationId}`}
          >
            {entry.surface.kind === 'workspace-canvas' && (
              <P7WorkspaceCanvas surface={entry.surface} />
            )}
            {entry.surface.kind === 'instruction-skill' && (
              <P7InstructionSkill surface={entry.surface} />
            )}
            {entry.surface.kind === 'readonly-mcp' && <P7McpResult surface={entry.surface} />}
            {entry.surface.kind === 'sandbox-workload' && (
              <P7SandboxResult surface={entry.surface} />
            )}
            {entry.surface.kind === 'knowledge-ebook' && (
              <P7KnowledgeEbook surface={entry.surface} />
            )}
          </P7ComponentSurfaceErrorBoundary>
        ),
      )}
    </>
  )
}

import type { Metadata } from 'next'
import { PublicPreviewClient } from './public-preview-client'

export const metadata: Metadata = {
  title: 'OmniBase · Personal AI engineering workbench',
  description:
    'A self-hosted personal AI engineering workbench with file context, model routing, native Skills, auditable changes and quiet specialist roles.',
  openGraph: {
    title: 'OmniBase · One personal engineering space for you and your AI team',
    description:
      'Workspaces, file context, model-name-first routing, native Skills, durable runs and reviewable ChangeSets in one self-hosted workbench.',
    type: 'website',
  },
}

export default function PublicPreviewPage() {
  return <PublicPreviewClient />
}

import type { Metadata } from 'next'
import { PublicPreviewClient } from './public-preview-client'

export const metadata: Metadata = {
  title: 'OmniBase · Agent workbench',
  description:
    'Create a workspace, connect your own model provider and build a versioned AI worker on an open-source, self-hosted workbench.',
  openGraph: {
    title: 'OmniBase · Build AI workers you can understand',
    description:
      'A self-hosted Agent workbench with personal model providers, workspaces, versioned workers, durable runs and governed knowledge.',
    type: 'website',
  },
}

export default function PublicPreviewPage() {
  return <PublicPreviewClient />
}

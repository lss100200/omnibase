import type { Metadata } from 'next'
import { PublicPreviewClient } from './public-preview-client'

export const metadata: Metadata = {
  title: 'OmniBase · P6.9 Personal Multi-Agent Team R0',
  description:
    'A parent-directed personal AI team engineering-accepted for deterministic loopback, with host-validated identity, budgets, collaboration, cancellation and recovery.',
  openGraph: {
    title: 'OmniBase · P6.9 Personal Multi-Agent Team R0',
    description:
      'One Owner, one parent Agent and nine fixed specialists in a deterministic-loopback-proven, host-validated desktop team.',
    type: 'website',
  },
}

export default function PublicPreviewPage() {
  return <PublicPreviewClient />
}

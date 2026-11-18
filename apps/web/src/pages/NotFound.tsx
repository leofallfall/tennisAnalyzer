import { Link } from 'react-router-dom'

import { PageHeader } from '@/components/common'

export function NotFoundPage() {
  return (
    <PageHeader
      title="Page not found"
      description={
        <Link to="/" className="text-primary hover:underline">
          Back to the dashboard
        </Link>
      }
    />
  )
}

import { useEffect, useState } from 'react'
import { fetchArtifactBlob } from '../api/client'
import { cn } from '../lib/cn'

interface AuthenticatedImageProps {
  url: string
  alt?: string
  title?: string
  className?: string
  placeholderClassName?: string
  loading?: 'lazy' | 'eager'
}

/**
 * Images cannot carry an Authorization header. Fetch the artifact as a blob
 * with the session token, then render a short-lived object URL.
 */
export function AuthenticatedImage({
  url,
  alt = '',
  title,
  className,
  placeholderClassName,
  loading = 'lazy',
}: AuthenticatedImageProps) {
  const [image, setImage] = useState<{ url: string; objectUrl: string } | null>(
    null,
  )
  const [failedUrl, setFailedUrl] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    fetchArtifactBlob(url)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setImage({ url, objectUrl })
        setFailedUrl(null)
      })
      .catch(() => {
        if (!cancelled) setFailedUrl(url)
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])

  if (image?.url === url) {
    return (
      <img
        src={image.objectUrl}
        alt={alt}
        title={title}
        className={className}
        loading={loading}
      />
    )
  }

  return (
    <span
      className={cn(
        'inline-flex items-center justify-center rounded-lg border border-slate-800 bg-slate-950/40 px-2 py-1 text-[11px] text-slate-500',
        placeholderClassName,
      )}
      role="status"
    >
      {failedUrl === url ? `图片加载失败${alt ? `：${alt}` : ''}` : '图片加载中…'}
    </span>
  )
}

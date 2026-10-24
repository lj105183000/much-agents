import { useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AuthenticatedImage } from './AuthenticatedImage'

export type MarkdownImageResolver = (src: string) => string | null

interface MarkdownPreviewProps {
  content: string
  className?: string
  resolveImageUrl?: MarkdownImageResolver
}

export function MarkdownPreview({
  content,
  className = '',
  resolveImageUrl,
}: MarkdownPreviewProps) {
  const components = useMemo<Components>(() => {
    const base: Components = {
      a: ({ children, href }) => (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      ),
      table: ({ children }) => (
        <div className="overflow-x-auto">
          <table>{children}</table>
        </div>
      ),
    }
    if (!resolveImageUrl) return base
    return {
      ...base,
      img: ({ src, alt, title }) => {
        const resolved = src ? resolveImageUrl(src) : null
        if (!resolved) {
          return (
            <span className="inline-flex rounded-lg border border-amber-500/20 bg-amber-500/5 px-2 py-1 text-[11px] text-amber-200">
              图片不可用{alt ? `：${alt}` : ''}
            </span>
          )
        }
        return (
          <AuthenticatedImage
            url={resolved}
            alt={alt ?? ''}
            title={title ?? undefined}
          />
        )
      },
    }
  }, [resolveImageUrl])

  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

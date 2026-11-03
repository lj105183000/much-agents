import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const indexPath = resolve('dist/index.html')
const html = await readFile(indexPath, 'utf8')
const match = html.match(
  /<meta[^>]+http-equiv=["']Content-Security-Policy["'][^>]*>/i,
)
if (!match) {
  console.error('CSP meta tag is missing from dist/index.html')
  process.exit(1)
}
const tag = match[0]
  .replaceAll('&#39;', "'")
  .replaceAll('&quot;', '"')
for (const required of [
  "script-src 'self' file:",
  "connect-src 'self' file:",
  "object-src 'none'",
]) {
  if (!tag.includes(required)) {
    console.error(`CSP meta tag is missing: ${required}`)
    process.exit(1)
  }
}
if (!html.includes('src="./theme-init.js"')) {
  console.error('external theme bootstrap script is missing')
  process.exit(1)
}
console.log('CSP meta tag and external theme bootstrap verified')

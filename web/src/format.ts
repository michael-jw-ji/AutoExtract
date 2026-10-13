export const f1 = (x: number | null | undefined) => (x == null ? '—' : x.toFixed(4))
export const pct = (x: number | null | undefined) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)
export const pts = (x: number | null | undefined) => (x == null ? '—' : `${x >= 0 ? '+' : ''}${x.toFixed(2)}`)

export const REPO = 'https://github.com/michael-jw-ji/insider-reading'
export const repoFile = (path: string) => `${REPO}/blob/main/${path}`

// The shape scripts/site_data.py writes to src/data.json.

export type Decision = 'promoted' | 'rejected'

export interface Version {
  name: string
  stage: string
  date: string
  note: string
  decision: Decision
  incumbent: string
  before: number
  after: number
  gain_pp: number
  valid: number
  reason: string
  record: string
  commit: string
}

export interface FieldRecall {
  field: string
  before: number | null
  after: number
}

export interface Cost {
  model: string
  gpu: string
  usd_per_filing: number
  p50_s: number
  p99_s: number
}

export interface Large {
  model: string
  f1: number
  valid: number
  small_name: string
  small_f1: number
  small_valid: number
  outcomes: Record<string, number>
  ci_pp: { delta: number; lo: number; hi: number }
}

export interface SiteData {
  current: { name: string; f1: number; valid: number; commit: string }
  base: { name: string; f1: number }
  holdout: { documents: number; hash: string }
  fields: FieldRecall[]
  fields_before: string
  versions: Version[]
  cost: Cost | null
  large: Large | null
}

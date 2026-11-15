import { existsSync } from "fs"
import { join } from "path"
import { BudgetError, REPO_ROOT } from "./budget.js"

export const API_BASE = "https://api.firecrawl.dev/v2"
export const TOKEN_VAR = "FIRECRAWL_API_TOKEN"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

export function fail(error: string, code: string): number {
  writeError(error, code)
  return 1
}

/** Map any thrown error to the standard stderr JSON and exit code 1. */
export function failFrom(e: unknown): number {
  if (e instanceof BudgetError || e instanceof ApiError) return fail(e.message, e.code)
  return fail(e instanceof Error ? e.message : String(e), "INTERNAL_ERROR")
}

export class ApiError extends Error {
  constructor(message: string, readonly code: string, readonly status: number, readonly retryAfter?: number) {
    super(message)
  }
}

export function token(env = process.env): string {
  const t = env[TOKEN_VAR]?.trim()
  if (!t) {
    throw new ApiError(
      `${TOKEN_VAR} is not set. Create a free key at firecrawl.dev, put ${TOKEN_VAR}=fc-... in the repo's .env (gitignored), and run from the repo root`,
      "MISSING_CREDENTIALS",
      0,
    )
  }
  return t
}

/**
 * Zero-width, bidi-control, tag and other invisible characters. Fetched page text is
 * third-party data; these can hide an instruction inside otherwise normal text.
 */
const INVISIBLE_CHARS =
  /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F­؜᠎​-‏‪-‮⁠-⁯︀-️﻿\u{E0000}-\u{E007F}\u{E0100}-\u{E01EF}]/gu

export function stripInvisible<T>(value: T): T {
  if (typeof value === "string") return value.replace(INVISIBLE_CHARS, "") as T
  if (Array.isArray(value)) return value.map(stripInvisible) as T
  if (value !== null && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, stripInvisible(v)])) as T
  }
  return value
}

function retryAfter(res: Response): number | undefined {
  const raw = res.headers.get("retry-after")
  if (!raw) return undefined
  const s = Number(raw)
  if (Number.isFinite(s) && s >= 0) return s
  const at = Date.parse(raw)
  return Number.isFinite(at) ? Math.max(0, Math.round((at - Date.now()) / 1000)) : undefined
}

export async function api<T>(method: "GET" | "POST", path: string, body?: unknown, timeoutMs = 75_000): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token()}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  })
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    // non-JSON error body
  }
  if (!res.ok) {
    const msg = (data as { error?: unknown } | null)?.error
    const detail = typeof msg === "string" ? msg : text.slice(0, 200)
    if (res.status === 402) throw new ApiError(`Firecrawl: out of credits (402) - ${detail}`, "BUDGET_EXHAUSTED", 402)
    if (res.status === 429) throw new ApiError(`Firecrawl: rate limited (429) - ${detail}`, "RATE_LIMITED", 429, retryAfter(res))
    if (res.status === 401 || res.status === 403) throw new ApiError(`Firecrawl rejected the API token (${res.status})`, "BAD_CREDENTIALS", res.status)
    throw new ApiError(`Firecrawl HTTP ${res.status} - ${detail}`, "UPSTREAM_ERROR", res.status)
  }
  return data as T
}

export type RobotsGate = (url: string) => { allowed: boolean; message: string }

/**
 * robots.txt gate via tools/robots_check.py - the same rules fetch_page.py enforces.
 * Exit 0 allows, 1 refuses; anything else (no Python, broken policy read) leaves
 * permission unconfirmed, which also refuses.
 */
export const pythonRobotsGate: RobotsGate = (url) => {
  const script = join(REPO_ROOT, "tools", "robots_check.py")
  if (!existsSync(script)) return { allowed: false, message: `robots gate unavailable: ${script} not found` }
  for (const py of ["python3", "python"]) {
    let r
    try {
      r = Bun.spawnSync([py, script, url], { stdout: "pipe", stderr: "pipe", timeout: 60_000 })
    } catch {
      continue
    }
    const out = (r.stdout.toString() + r.stderr.toString()).trim()
    if (r.exitCode === 0) return { allowed: true, message: out }
    if (r.exitCode === 1) return { allowed: false, message: out || "disallowed by robots.txt" }
  }
  return { allowed: false, message: "robots gate could not run (no working python3/python); permission unconfirmed" }
}

let gate: RobotsGate = pythonRobotsGate
export function setRobotsGate(g: RobotsGate): void {
  gate = g
}
export function robotsGate(url: string) {
  return gate(url)
}

/** http(s) URL with a host and no embedded credentials, or null. */
export function safeUrl(input: string): URL | null {
  let u: URL
  try {
    u = new URL(input.trim())
  } catch {
    return null
  }
  if (!["http:", "https:"].includes(u.protocol) || !u.hostname || u.username || u.password) return null
  return u
}

// --- ATS posting URLs ------------------------------------------------------

/** Applicant-tracking hosts whose posting URLs carry the employer's slug. */
export const ATS_DOMAINS = [
  "boards.greenhouse.io",
  "job-boards.greenhouse.io",
  "jobs.lever.co",
  "jobs.ashbyhq.com",
  "apply.workable.com",
  "jobs.smartrecruiters.com",
  "recruitee.com",
  "breezy.hr",
]

const ATS_PATTERNS: { re: RegExp; slug: number }[] = [
  { re: /^(?:job-)?boards\.greenhouse\.io\/([^/]+)\/jobs\/\d+/i, slug: 1 },
  { re: /^jobs\.lever\.co\/([^/]+)\/[0-9a-f-]{20,}/i, slug: 1 },
  { re: /^jobs\.ashbyhq\.com\/([^/]+)\/[0-9a-f-]{20,}/i, slug: 1 },
  { re: /^apply\.workable\.com\/([^/]+)\/j\/[0-9a-z]+/i, slug: 1 },
  { re: /^jobs\.smartrecruiters\.com\/([^/]+)\/\d+/i, slug: 1 },
  { re: /^([a-z0-9-]+)\.recruitee\.com\/o\/[^/]+/i, slug: 1 },
  { re: /^([a-z0-9-]+)\.breezy\.hr\/p\/[^/]+/i, slug: 1 },
]

/** Employer slug when `url` is a single posting on a known ATS, else null (listing pages, other sites). */
export function atsSlug(url: string): string | null {
  const u = safeUrl(url)
  if (!u) return null
  const hostPath = `${u.hostname.replace(/^www\./, "")}${u.pathname}`
  for (const p of ATS_PATTERNS) {
    const m = hostPath.match(p.re)
    if (m) return decodeURIComponent(m[p.slug])
  }
  return null
}

export function humanizeSlug(slug: string): string {
  return slug
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Strip ATS boilerplate ("Job Application for X at Y", "Y - X", "X | Y") from a result title. */
export function cleanTitle(raw: string, company: string): string {
  let t = raw.replace(/\s+/g, " ").trim()
  t = t.replace(/^job application for\s+/i, "")
  const co = company.toLowerCase()
  const parts = t.split(/\s+(?:[-–—|@·]|at)\s+/i)
  if (parts.length > 1) {
    const kept = parts.filter((p) => {
      const pl = p.toLowerCase()
      return !(pl === co || pl.replace(/\s+/g, "") === co.replace(/\s+/g, "") || /^(lever|greenhouse|ashby|workable|smartrecruiters|recruitee|breezy( hr)?|careers?|jobs?)$/i.test(p))
    })
    if (kept.length) t = kept.join(" - ")
  }
  return t.trim()
}

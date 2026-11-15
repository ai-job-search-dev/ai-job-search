import { readFileSync } from "fs"
import { join, resolve } from "path"
import { BudgetError } from "./budget.js"

export const API_BASE = "https://api.apify.com/v2"
export const TOKEN_VAR = "APIFY_API_TOKEN"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

export function fail(error: string, code: string): number {
  writeError(error, code)
  return 1
}

export class ApiError extends Error {
  constructor(message: string, readonly code: string, readonly status: number) {
    super(message)
  }
}

export function failFrom(e: unknown): number {
  if (e instanceof BudgetError || e instanceof ApiError) return fail(e.message, e.code)
  return fail(e instanceof Error ? e.message : String(e), "INTERNAL_ERROR")
}

export function token(env = process.env): string {
  const t = env[TOKEN_VAR]?.trim()
  if (!t) {
    throw new ApiError(
      `${TOKEN_VAR} is not set. Copy your token from console.apify.com > Settings > API & Integrations, put ${TOKEN_VAR}=apify_api_... in the repo's .env (gitignored), and run from the repo root`,
      "MISSING_CREDENTIALS",
      0,
    )
  }
  return t
}

const EXHAUSTED_TYPES = new Set(["not-enough-usage-to-run-paid-actor", "monthly-usage-limit-too-low", "platform-feature-disabled"])

export async function api<T>(method: "GET" | "POST", path: string, body?: unknown, timeoutMs = 90_000): Promise<T> {
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
    // non-JSON body
  }
  if (!res.ok) {
    const err = (data as { error?: { type?: string; message?: string } } | null)?.error
    const type = err?.type ?? ""
    const detail = err?.message ?? text.slice(0, 200)
    if (res.status === 402 || EXHAUSTED_TYPES.has(type)) throw new ApiError(`Apify: usage allowance exhausted (${type || res.status}) - ${detail}`, "BUDGET_EXHAUSTED", res.status)
    if (res.status === 429 || type === "rate-limit-exceeded" || type === "concurrent-runs-limit-exceeded") {
      throw new ApiError(`Apify: rate or concurrency limit (${type || 429}) - ${detail}`, "RATE_LIMITED", res.status)
    }
    if (res.status === 401) throw new ApiError("Apify rejected the API token (401)", "BAD_CREDENTIALS", 401)
    if (res.status === 404) throw new ApiError(`Apify: not found (${detail})`, "ACTOR_NOT_FOUND", 404)
    throw new ApiError(`Apify HTTP ${res.status} - ${detail}`, "UPSTREAM_ERROR", res.status)
  }
  return data as T
}

/** Same set as the other portal CLIs: invisible characters that can hide instructions in listing text. */
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

// --- board config -----------------------------------------------------------

export interface Board {
  enabled: boolean
  actor: string
  url?: string
  input: Record<string, unknown>
  defaults: Record<string, string>
  fields: Record<string, string[]>
}

export const SKILL_DIR = resolve(import.meta.dir, "..", "..")

export function loadBoards(env = process.env): Record<string, Board> {
  const path = env.APIFY_ACTORS_FILE?.trim() || join(SKILL_DIR, "actors.json")
  let data: { boards?: Record<string, Board> }
  try {
    data = JSON.parse(readFileSync(path, "utf8"))
  } catch (e) {
    throw new ApiError(`cannot read board config ${path}: ${e instanceof Error ? e.message : String(e)}`, "BAD_CONFIG", 0)
  }
  if (!data.boards || typeof data.boards !== "object") throw new ApiError(`${path} has no "boards" object`, "BAD_CONFIG", 0)
  for (const [name, b] of Object.entries(data.boards)) {
    if (!b || typeof b.actor !== "string" || !/^[\w.-]+[/~][\w.-]+$/.test(b.actor) || typeof b.input !== "object" || typeof b.fields !== "object") {
      throw new ApiError(`board "${name}" in ${path} needs actor "user/name", an input object and a fields object`, "BAD_CONFIG", 0)
    }
    b.defaults ??= {}
    b.enabled = b.enabled !== false
  }
  return data.boards
}

/** Fill {query} {location} {country} {limit}; a placeholder left without a value drops its key. */
export function buildInput(template: Record<string, unknown>, vals: Record<string, string | number | undefined>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(template)) {
    if (typeof v !== "string") {
      out[k] = v
      continue
    }
    const whole = v.match(/^\{(\w+)\}$/)
    if (whole) {
      const val = vals[whole[1]]
      if (val === undefined || val === "") continue
      out[k] = val
      continue
    }
    let missing = false
    const s = v.replace(/\{(\w+)\}/g, (_, name) => {
      const val = vals[name]
      if (val === undefined || val === "") missing = true
      return String(val ?? "")
    })
    if (!missing) out[k] = s
  }
  return out
}

function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj
  for (const part of path.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined
    cur = (cur as Record<string, unknown>)[part]
  }
  return cur
}

export function pick(item: unknown, paths: string[] | undefined): string | null {
  for (const p of paths ?? []) {
    const v = getPath(item, p)
    if (typeof v === "string" && v.trim()) return v.trim()
    if (typeof v === "number" && Number.isFinite(v)) return String(v)
    if (Array.isArray(v)) {
      const s = v.filter((x) => typeof x === "string" && x.trim()).join(", ")
      if (s) return s
    }
  }
  return null
}

/** Posting date as YYYY-MM-DD when the actor gives one; relative "N days ago" resolves, "30+ days" stays null. */
export function normalizeDate(raw: string | null, now: Date = new Date()): string | null {
  if (!raw) return null
  const s = raw.trim().toLowerCase()
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
    const d = new Date(raw)
    return Number.isNaN(d.getTime()) ? s.slice(0, 10) : d.toISOString().slice(0, 10)
  }
  if (/\+/.test(s)) return null
  if (/^(just posted|today|few hours ago|\d+\s*(hours?|hrs?|minutes?|mins?)\s*ago)$/.test(s)) return now.toISOString().slice(0, 10)
  const m = s.match(/^(\d+)\s*(day|days|d|week|weeks|w)\s*ago$/)
  if (m) {
    const days = Number(m[1]) * (m[2].startsWith("w") ? 7 : 1)
    return new Date(now.getTime() - days * 86_400_000).toISOString().slice(0, 10)
  }
  const ms = Date.parse(raw)
  return Number.isFinite(ms) && /\d{4}/.test(raw) ? new Date(ms).toISOString().slice(0, 10) : null
}

export function htmlToText(s: string): string {
  if (!/<[a-z][\s\S]*>/i.test(s)) return s.trim()
  return s
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, "")
    .replace(/<br\s*\/?>|<\/(p|div|li|h\d)>/gi, "\n")
    .replace(/<li[^>]*>/gi, "- ")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n\s*\n+/g, "\n\n")
    .trim()
}

export function httpUrl(s: string | null): string | null {
  if (!s) return null
  try {
    const u = new URL(s)
    return ["http:", "https:"].includes(u.protocol) && u.hostname && !u.username && !u.password ? u.href : null
  } catch {
    return null
  }
}

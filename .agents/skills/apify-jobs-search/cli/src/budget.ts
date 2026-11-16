// Free-tier budget ledger and rate limiter for apify-jobs-search.
//
// What it enforces, across every process that shares the ledger file:
//   * a monthly allowance per provider (Apify USD), reset on
//     the provider's billing-cycle day, and synced upward from the provider's own
//     usage endpoint when one is reachable (the provider's number wins if higher)
//   * a reserve: /scrape stops at (1 - SCRAPE_BUDGET_RESERVE) of the allowance so
//     single-posting fetches from /apply (--allow-reserve) still work late in the month
//   * an optional per-run cap, keyed by --run-id, so one /scrape cannot spend the month
//   * requests-per-minute and in-flight concurrency, sized under the free-tier caps
//   * a pause after a 429 and a hard stop after "out of credits", until the cycle resets
//
// Every check happens under a lock file before the request is sent; a request
// that cannot get a slot within the wait cap fails with RATE_LIMITED rather than
// being sent anyway.

import { closeSync, mkdirSync, openSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "fs"
import { dirname, join, resolve } from "path"

export type Provider = "apify"

export interface ProviderConfig {
  unit: "credits" | "usd"
  monthly: number
  cycleDay: number
  rpm: number
  concurrency: number
}

export interface BudgetEnv {
  [k: string]: string | undefined
}

const DEFAULTS: Record<Provider, ProviderConfig> = {
  // Free plan: $5/month of platform usage. One actor run at a time keeps the spend predictable.
  apify: { unit: "usd", monthly: 5, cycleDay: 1, rpm: 4, concurrency: 1 },
}

const PREFIX: Record<Provider, string> = { apify: "APIFY" }

export class BudgetError extends Error {
  constructor(message: string, readonly code: string) {
    super(message)
  }
}

function num(env: BudgetEnv, key: string, fallback: number, min: number, max: number): number {
  const raw = env[key]
  if (raw === undefined || raw.trim() === "") return fallback
  const v = Number(raw)
  if (!Number.isFinite(v) || v < min || v > max) {
    throw new BudgetError(`${key} must be a number between ${min} and ${max}, got "${raw}"`, "BAD_CONFIG")
  }
  return v
}

export function loadConfig(p: Provider, env: BudgetEnv = process.env): ProviderConfig {
  const d = DEFAULTS[p]
  const k = PREFIX[p]
  return {
    unit: d.unit,
    monthly: num(env, `${k}_MONTHLY_USD`, d.monthly, 0, 1e9),
    cycleDay: Math.trunc(num(env, `${k}_CYCLE_DAY`, d.cycleDay, 1, 28)),
    rpm: Math.trunc(num(env, `${k}_RPM`, d.rpm, 1, 10000)),
    concurrency: Math.trunc(num(env, `${k}_CONCURRENCY`, d.concurrency, 1, 100)),
  }
}

export function reserveFraction(env: BudgetEnv = process.env): number {
  return num(env, "SCRAPE_BUDGET_RESERVE", 0.2, 0, 0.9)
}

export function runsPerMonth(env: BudgetEnv = process.env): number {
  return Math.trunc(num(env, "SCRAPE_RUNS_PER_MONTH", 8, 1, 1000))
}

/** Repo root: this file lives at .agents/skills/<skill>/cli/src/budget.ts. */
export const REPO_ROOT = resolve(import.meta.dir, "..", "..", "..", "..", "..")

export function ledgerPath(env: BudgetEnv = process.env): string {
  return env.SCRAPE_BUDGET_FILE?.trim() || join(REPO_ROOT, ".claude", "skills", "job-scraper", "job_scraper", "api_budget.json")
}

interface Inflight {
  id: string
  pid: number
  t: number
}

interface ProviderState {
  period_start: string
  used: number
  runs: Record<string, number>
  recent: number[]
  inflight: Inflight[]
  paused_until: number | null
  exhausted: boolean
  remote: { used: number | null; limit: number | null; period_start: string | null; synced_at: string } | null
}

interface Ledger {
  version: 1
  providers: Partial<Record<Provider, ProviderState>>
}

/** Start (YYYY-MM-DD, UTC) of the billing cycle containing `now`. */
export function cycleStart(now: Date, cycleDay: number): string {
  let y = now.getUTCFullYear()
  let m = now.getUTCMonth()
  if (now.getUTCDate() < cycleDay) {
    m -= 1
    if (m < 0) {
      m = 11
      y -= 1
    }
  }
  return new Date(Date.UTC(y, m, cycleDay)).toISOString().slice(0, 10)
}

function nextCycleStart(start: string): Date {
  const d = new Date(`${start}T00:00:00Z`)
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()))
}

function freshState(period: string): ProviderState {
  return { period_start: period, used: 0, runs: {}, recent: [], inflight: [], paused_until: null, exhausted: false, remote: null }
}

function readLedger(path: string): Ledger {
  let text: string
  try {
    text = readFileSync(path, "utf8")
  } catch {
    return { version: 1, providers: {} }
  }
  try {
    const data = JSON.parse(text)
    if (data && typeof data === "object" && data.providers && typeof data.providers === "object") return data as Ledger
  } catch {
    // fall through
  }
  throw new BudgetError(`budget ledger ${path} is not valid JSON; fix or delete it (deleting resets local usage counts)`, "BAD_LEDGER")
}

function writeLedger(path: string, ledger: Ledger): void {
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.${process.pid}.tmp`
  writeFileSync(tmp, JSON.stringify(ledger, null, 2) + "\n")
  renameSync(tmp, path)
}

function pidAlive(pid: number): boolean {
  if (pid === process.pid) return true
  try {
    process.kill(pid, 0)
    return true
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "EPERM"
  }
}

export interface Clock {
  now(): number
  sleep(ms: number): Promise<void>
}

export const realClock: Clock = {
  now: () => Date.now(),
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
}

const LOCK_STALE_MS = 30_000
const INFLIGHT_STALE_MS = 15 * 60_000

async function withLock<T>(path: string, clock: Clock, fn: () => T): Promise<T> {
  const lock = `${path}.lock`
  mkdirSync(dirname(lock), { recursive: true })
  const deadline = clock.now() + 20_000
  for (;;) {
    try {
      closeSync(openSync(lock, "wx"))
      break
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e
      try {
        if (Date.now() - statSync(lock).mtimeMs > LOCK_STALE_MS) unlinkSync(lock)
      } catch {
        // lock vanished between checks; retry
      }
      if (clock.now() > deadline) throw new BudgetError(`could not lock ${lock} within 20 s`, "LOCK_TIMEOUT")
      await clock.sleep(50 + Math.floor(Math.random() * 100))
    }
  }
  try {
    return fn()
  } finally {
    try {
      unlinkSync(lock)
    } catch {
      // already gone
    }
  }
}

export interface BudgetOpts {
  env?: BudgetEnv
  clock?: Clock
  path?: string
}

function ctx(p: Provider, o: BudgetOpts) {
  const env = o.env ?? process.env
  return { env, clock: o.clock ?? realClock, path: o.path ?? ledgerPath(env), cfg: loadConfig(p, env) }
}

/** Load the provider's state, rolling it over when a new billing cycle has started. */
function stateFor(ledger: Ledger, p: Provider, cfg: ProviderConfig, now: number): ProviderState {
  const period = cycleStart(new Date(now), cfg.cycleDay)
  let s = ledger.providers[p]
  if (!s || s.period_start < period) {
    s = freshState(period)
    ledger.providers[p] = s
  }
  s.recent = s.recent.filter((t) => now - t < 60_000)
  s.inflight = s.inflight.filter((f) => now - f.t < INFLIGHT_STALE_MS && pidAlive(f.pid))
  if (s.paused_until !== null && s.paused_until <= now) s.paused_until = null
  return s
}

function round(v: number, unit: ProviderConfig["unit"]): number {
  return unit === "usd" ? Math.round(v * 10000) / 10000 : Math.round(v * 100) / 100
}

export interface BudgetStatus {
  provider: Provider
  unit: ProviderConfig["unit"]
  period_start: string
  period_end: string
  used: number
  monthly: number
  reserve_fraction: number
  scrape_ceiling: number
  remaining_for_scrape: number
  remaining_total: number
  runs_left_estimate: number
  suggested_run_budget: number
  rpm: number
  concurrency: number
  paused_until: string | null
  exhausted: boolean
  remote: ProviderState["remote"]
}

function describe(p: Provider, s: ProviderState, cfg: ProviderConfig, env: BudgetEnv, now: number): BudgetStatus {
  const reserve = reserveFraction(env)
  const ceiling = cfg.monthly * (1 - reserve)
  const end = nextCycleStart(s.period_start)
  const start = new Date(`${s.period_start}T00:00:00Z`).getTime()
  const fracLeft = Math.max(0, Math.min(1, (end.getTime() - now) / (end.getTime() - start)))
  const runsLeft = Math.max(1, Math.ceil(runsPerMonth(env) * fracLeft))
  const remainingScrape = s.exhausted ? 0 : Math.max(0, ceiling - s.used)
  return {
    provider: p,
    unit: cfg.unit,
    period_start: s.period_start,
    period_end: end.toISOString().slice(0, 10),
    used: round(s.used, cfg.unit),
    monthly: cfg.monthly,
    reserve_fraction: reserve,
    scrape_ceiling: round(ceiling, cfg.unit),
    remaining_for_scrape: round(remainingScrape, cfg.unit),
    remaining_total: s.exhausted ? 0 : round(Math.max(0, cfg.monthly - s.used), cfg.unit),
    runs_left_estimate: runsLeft,
    suggested_run_budget: round(remainingScrape / runsLeft, cfg.unit),
    rpm: cfg.rpm,
    concurrency: cfg.concurrency,
    paused_until: s.paused_until ? new Date(s.paused_until).toISOString() : null,
    exhausted: s.exhausted,
    remote: s.remote,
  }
}

export async function status(p: Provider, o: BudgetOpts = {}): Promise<BudgetStatus> {
  const c = ctx(p, o)
  return withLock(c.path, c.clock, () => {
    const ledger = readLedger(c.path)
    const now = c.clock.now()
    const s = stateFor(ledger, p, c.cfg, now)
    writeLedger(c.path, ledger)
    return describe(p, s, c.cfg, c.env, now)
  })
}

export interface ReserveOpts extends BudgetOpts {
  runId?: string
  runBudget?: number
  allowReserve?: boolean
  /** Longest total wait for a rate/concurrency slot before giving up. */
  maxWaitMs?: number
}

export interface Ticket {
  provider: Provider
  id: string
  estimate: number
  runId?: string
}

/**
 * Charge `estimate` against the budget and take a rate + concurrency slot.
 * Throws BudgetError (BUDGET_EXHAUSTED, BUDGET_CEILING, RUN_BUDGET, RATE_LIMITED)
 * without sending anything when the call is not allowed.
 */
export async function reserve(p: Provider, estimate: number, o: ReserveOpts = {}): Promise<Ticket> {
  const c = ctx(p, o)
  const maxWait = o.maxWaitMs ?? 90_000
  const started = c.clock.now()
  const id = `${process.pid}-${started}-${Math.random().toString(36).slice(2, 8)}`
  for (;;) {
    const waitMs = await withLock(c.path, c.clock, () => {
      const ledger = readLedger(c.path)
      const now = c.clock.now()
      const s = stateFor(ledger, p, c.cfg, now)
      const st = describe(p, s, c.cfg, c.env, now)
      if (s.exhausted) {
        throw new BudgetError(`${p} reported its allowance exhausted this cycle; it resets on ${st.period_end}`, "BUDGET_EXHAUSTED")
      }
      const ceiling = o.allowReserve ? c.cfg.monthly : st.scrape_ceiling
      if (s.used + estimate > ceiling + 1e-9) {
        const what = o.allowReserve ? "monthly allowance" : `scrape ceiling (${Math.round((1 - st.reserve_fraction) * 100)}% of the allowance; the rest is reserved for --allow-reserve)`
        throw new BudgetError(
          `${p}: this call needs ${estimate} ${c.cfg.unit} but ${round(s.used, c.cfg.unit)} of the ${what} ${round(ceiling, c.cfg.unit)} is already used; resets on ${st.period_end}`,
          "BUDGET_CEILING",
        )
      }
      if (o.runId && o.runBudget !== undefined) {
        const spent = s.runs[o.runId] ?? 0
        if (spent + estimate > o.runBudget + 1e-9) {
          throw new BudgetError(
            `${p}: run ${o.runId} has spent ${round(spent, c.cfg.unit)} of its ${o.runBudget} ${c.cfg.unit} run budget; this call needs ${estimate}`,
            "RUN_BUDGET",
          )
        }
      }
      let wait = 0
      if (s.paused_until !== null) wait = s.paused_until - now
      else if (s.recent.length >= c.cfg.rpm) wait = s.recent[s.recent.length - c.cfg.rpm] + 60_000 - now
      else if (s.inflight.length >= c.cfg.concurrency) wait = 1_000
      if (wait > 0) {
        writeLedger(c.path, ledger)
        return wait
      }
      s.recent.push(now)
      s.inflight.push({ id, pid: process.pid, t: now })
      s.used += estimate
      if (o.runId) s.runs[o.runId] = (s.runs[o.runId] ?? 0) + estimate
      writeLedger(c.path, ledger)
      return 0
    })
    if (waitMs === 0) return { provider: p, id, estimate, runId: o.runId }
    if (c.clock.now() - started + waitMs > maxWait) {
      throw new BudgetError(`${p}: no request slot free within ${Math.round(maxWait / 1000)} s (rate or concurrency limit); try again later`, "RATE_LIMITED")
    }
    await c.clock.sleep(Math.max(250, Math.min(waitMs, 10_000)))
  }
}

export interface SettleOpts extends BudgetOpts {
  /** Seconds the provider asked us to wait (429). Pauses the provider for at least 60 s. */
  rateLimitedFor?: number
  /** The provider said the allowance is used up (402 / not-enough-usage). */
  exhausted?: boolean
}

/** Release the slot and replace the estimate with what the call actually cost. */
export async function settle(t: Ticket, actual: number, o: SettleOpts = {}): Promise<void> {
  const c = ctx(t.provider, o)
  await withLock(c.path, c.clock, () => {
    const ledger = readLedger(c.path)
    const now = c.clock.now()
    const s = stateFor(ledger, t.provider, c.cfg, now)
    s.inflight = s.inflight.filter((f) => f.id !== t.id)
    const delta = actual - t.estimate
    s.used = Math.max(0, s.used + delta)
    if (t.runId) s.runs[t.runId] = Math.max(0, (s.runs[t.runId] ?? 0) + delta)
    if (o.rateLimitedFor !== undefined) s.paused_until = now + Math.max(60, o.rateLimitedFor) * 1000
    if (o.exhausted) s.exhausted = true
    writeLedger(c.path, ledger)
  })
}

/** Record the provider's own usage figure. Local usage only ever moves up to meet it. */
export async function syncRemote(
  p: Provider,
  remote: { used: number | null; limit: number | null; periodStart?: string | null },
  o: BudgetOpts = {},
): Promise<void> {
  const c = ctx(p, o)
  await withLock(c.path, c.clock, () => {
    const ledger = readLedger(c.path)
    const now = c.clock.now()
    const s = stateFor(ledger, p, c.cfg, now)
    const period = remote.periodStart ? remote.periodStart.slice(0, 10) : null
    // A provider period that started after ours means a reset we had not seen yet.
    if (period && period > s.period_start && period <= new Date(now).toISOString().slice(0, 10)) {
      Object.assign(s, freshState(period), { recent: s.recent, inflight: s.inflight })
    }
    if (remote.used !== null && remote.used > s.used) s.used = remote.used
    if (remote.used !== null && remote.limit !== null && remote.limit > 0 && remote.used >= remote.limit) s.exhausted = true
    s.remote = { used: remote.used, limit: remote.limit, period_start: period, synced_at: new Date(now).toISOString() }
    writeLedger(c.path, ledger)
  })
}

export function formatStatus(st: BudgetStatus): string {
  const u = st.unit === "usd" ? "$" : ""
  const tail = st.unit === "credits" ? " credits" : ""
  const lines = [
    `${st.provider}: ${u}${st.used} of ${u}${st.monthly}${tail} used (cycle ${st.period_start} → ${st.period_end})`,
    `  scrape ceiling ${u}${st.scrape_ceiling}${tail} (${Math.round(st.reserve_fraction * 100)}% reserved for /apply); ${u}${st.remaining_for_scrape}${tail} left for scraping`,
    `  suggested per-run budget ${u}${st.suggested_run_budget}${tail} (~${st.runs_left_estimate} runs left this cycle)`,
    `  limits: ${st.rpm} req/min, ${st.concurrency} at a time`,
  ]
  if (st.paused_until) lines.push(`  paused after a 429 until ${st.paused_until}`)
  if (st.exhausted) lines.push(`  EXHAUSTED: the provider reported no allowance left this cycle`)
  if (st.remote) lines.push(`  provider says: used ${st.remote.used ?? "?"} of ${st.remote.limit ?? "?"} (synced ${st.remote.synced_at})`)
  return lines.join("\n") + "\n"
}

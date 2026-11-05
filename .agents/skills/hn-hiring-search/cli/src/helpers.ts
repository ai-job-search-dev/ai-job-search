// hn-hiring-search: Hacker News "Ask HN: Who is hiring?" threads and YC startup
// job posts, through HN's public APIs (hn.algolia.com and the official
// hacker-news.firebaseio.com). No key, no dependencies.

const ALGOLIA = "https://hn.algolia.com/api/v1";
const FIREBASE = "https://hacker-news.firebaseio.com/v0";
export const itemLink = (id: string | number) => `https://news.ycombinator.com/item?id=${id}`;
const TIMEOUT_MS = 20_000;

export type Job = {
  id: string;
  source: "hn-whoishiring" | "yc-jobs";
  title: string;
  company: string | null;
  location: string | null;
  remote: boolean;
  date: string | null;
  url: string;
  applyUrl: string | null;
  description: string;
};

/** Portal-skill contract: errors go to stderr as one JSON line, exit 1. */
export function fail(message: string, code = "ERROR"): never {
  process.stderr.write(JSON.stringify({ error: message, code }) + "\n");
  process.exit(1);
}

export async function getJson<T>(url: string): Promise<T> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS), headers: { "User-Agent": "hn-hiring-search/1.0" } });
      if (response.status === 429 || response.status >= 500) throw new Error(`HTTP ${response.status}`);
      if (!response.ok) fail(`HTTP ${response.status} from ${url}`, "HTTP_ERROR");
      return (await response.json()) as T;
    } catch (error) {
      if (attempt === 2) fail(`request failed for ${url}: ${(error as Error).message}`, "NETWORK_ERROR");
      await Bun.sleep(1000 * 2 ** attempt);
    }
  }
  fail("unreachable");
}

/** HN item HTML as plain text. */
export function toText(html: string | null | undefined): string {
  if (!html) return "";
  return html
    .replace(/<p>/gi, "\n\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<a [^>]*href="([^"]*)"[^>]*>[^<]*<\/a>/gi, "$1")
    .replace(/<[^>]+>/g, "")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(Number(dec)))
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&nbsp;/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export const REMOTE = /\bremote\b/i;
export const ONSITE_ONLY = /\b(no remote|onsite only|on-site only|in[- ]office only)\b/i;
const PLACE_HINT = /\b(remote|onsite|on-site|hybrid|[A-Z][a-z]+,\s*[A-Z]{2}\b|europe|eu\b|emea|usa?\b|uk\b|germany|berlin|london|worldwide|anywhere|global)/i;

/** Who-is-hiring comments open with "Company | Role | Location | ..." by convention. */
export function parseHiringComment(id: number, html: string, time: number): Job | null {
  const text = toText(html);
  if (!text) return null;
  const header = text.split("\n")[0].trim();
  const parts = header.split("|").map((part) => part.trim()).filter(Boolean);
  if (parts.length < 2) return null; // not a job post (replies, meta comments)
  const company = parts[0].replace(/\s*\(.*?\)\s*$/, "").slice(0, 80) || null;
  const location = parts.slice(1).filter((part) => PLACE_HINT.test(part)).join(" | ") || null;
  const roleParts = parts.slice(1).filter((part) => !PLACE_HINT.test(part) && !/^https?:/i.test(part) && !/\$|€|£|\d+k/i.test(part) && !/^(full[- ]?time|part[- ]?time|contract(or)?|freelance|intern(ship)?|visa|equity|salary|fte|ft|pt)\b/i.test(part));
  const title = (roleParts[0] ?? header).slice(0, 160);
  const remote = REMOTE.test(header) && !ONSITE_ONLY.test(header);
  const applyUrl = text.match(/https?:\/\/\S+/)?.[0]?.replace(/[).,]+$/, "") ?? null;
  return {
    id: `hn-${id}`,
    source: "hn-whoishiring",
    title,
    company,
    location,
    remote,
    date: new Date(time * 1000).toISOString().slice(0, 10),
    url: itemLink(id),
    applyUrl,
    description: text,
  };
}

type AlgoliaItem = { id: number; created_at_i: number; text: string | null; children: AlgoliaItem[] };

export async function whoIsHiring(months: number): Promise<Job[]> {
  const search = await getJson<{ hits: { objectID: string; title: string }[] }>(
    `${ALGOLIA}/search_by_date?tags=story,author_whoishiring&hitsPerPage=12`,
  );
  const threads = search.hits.filter((hit) => /^ask hn: who is hiring\?/i.test(hit.title)).slice(0, months);
  const jobs: Job[] = [];
  for (const thread of threads) {
    const item = await getJson<AlgoliaItem>(`${ALGOLIA}/items/${thread.objectID}`);
    for (const child of item.children ?? []) {
      const job = parseHiringComment(child.id, child.text ?? "", child.created_at_i);
      if (job) jobs.push(job);
    }
  }
  return jobs;
}

export type FirebaseItem = { id: number; title?: string; text?: string; url?: string; time: number; dead?: boolean; deleted?: boolean };

/** "Acme (YC W21) Is Hiring a Senior PM" -> company "Acme". */
export function ycCompany(title: string): string | null {
  const match = title.match(/^(.+?)\s*\((?:YC|S|W)[^)]*\)/i) ?? title.match(/^(.+?)\s+(?:is|are)\s+hiring/i);
  return match ? match[1].trim() : null;
}

export async function ycJobs(limit: number): Promise<Job[]> {
  const ids = (await getJson<number[]>(`${FIREBASE}/jobstories.json`)).slice(0, limit);
  const items = await Promise.all(ids.map((id) => getJson<FirebaseItem | null>(`${FIREBASE}/item/${id}.json`)));
  return items
    .filter((item): item is FirebaseItem => !!item && !item.dead && !item.deleted && !!item.title)
    .map((item) => {
      const description = toText(item.text);
      const blob = `${item.title} ${description}`;
      return {
        id: `yc-${item.id}`,
        source: "yc-jobs" as const,
        title: item.title!,
        company: ycCompany(item.title!),
        location: REMOTE.test(blob) ? "Remote (see posting)" : null,
        remote: REMOTE.test(blob) && !ONSITE_ONLY.test(blob),
        date: new Date(item.time * 1000).toISOString().slice(0, 10),
        url: item.url?.trim() || itemLink(item.id),
        applyUrl: item.url?.trim() || null,
        description,
      };
    });
}

/** "product manager OR chief of staff" -> any phrase matches (case-insensitive). */
export function matchesQuery(job: Job, query: string | undefined, titleOnly: boolean): boolean {
  if (!query) return true;
  const haystack = (titleOnly ? `${job.title}\n${job.description.split("\n")[0]}` : `${job.title}\n${job.description}`).toLowerCase();
  return query.split(/\s+OR\s+/).some((phrase) => haystack.includes(phrase.trim().toLowerCase()));
}

export function parseArgs(argv: string[]) {
  const [command, ...rest] = argv;
  const flags: Record<string, string | boolean> = {};
  const positional: string[] = [];
  const alias: Record<string, string> = { q: "query", n: "limit", l: "location" };
  const known = new Set(["query", "limit", "location", "remote", "jobage", "source", "format", "months", "title-only", "no-description"]);
  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i];
    if (!arg.startsWith("-")) {
      positional.push(arg);
      continue;
    }
    const raw = arg.replace(/^--?/, "");
    const name = alias[raw] ?? raw;
    if (!known.has(name)) fail(`unknown flag ${arg}. Known: ${[...known].map((k) => `--${k}`).join(" ")}`, "BAD_ARG");
    if (["remote", "title-only", "no-description"].includes(name)) flags[name] = true;
    else {
      const value = rest[++i];
      if (value === undefined) fail(`flag ${arg} needs a value`, "BAD_ARG");
      flags[name] = value;
    }
  }
  return { command, flags, positional };
}

export function render(jobs: Job[], format: string, withDescription: boolean) {
  if (format === "json") {
    const results = jobs.map((job) => (withDescription ? job : { ...job, description: undefined }));
    console.log(JSON.stringify({ meta: { count: results.length }, results }, null, 2));
  } else if (format === "table") {
    for (const job of jobs) {
      console.log([job.date ?? "", job.id, (job.company ?? "").slice(0, 25), job.title.slice(0, 60), (job.location ?? "").slice(0, 40), job.url].join(" | "));
    }
  } else if (format === "plain") {
    for (const job of jobs) {
      console.log(`${job.title}\n${job.company ?? "?"} · ${job.location ?? "location not stated"} · ${job.date}\n${job.url}\n\n${job.description}\n\n---\n`);
    }
  } else fail("--format must be json, table or plain", "BAD_ARG");
}


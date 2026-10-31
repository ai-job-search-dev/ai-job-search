import { fail, matchesQuery, render, whoIsHiring, ycJobs, type Job } from "../helpers";

/** `search`: every result carries title, company, location, date and url (the /scrape Step 2 contract). */
export async function search(flags: Record<string, string | boolean>, format: string) {
  const source = String(flags.source ?? "all");
  if (!["all", "hn", "yc"].includes(source)) fail("--source must be all, hn or yc", "BAD_ARG");
  const limit = Number(flags.limit ?? 25);
  const months = Number(flags.months ?? 1);
  const jobage = flags.jobage === undefined ? null : Number(flags.jobage);
  if (!Number.isFinite(limit) || limit < 1) fail("--limit must be a positive number", "BAD_ARG");
  let jobs: Job[] = [];
  if (source !== "yc") jobs.push(...(await whoIsHiring(months)));
  if (source !== "hn") jobs.push(...(await ycJobs(100)));
  const location = flags.location ? String(flags.location).toLowerCase() : null;
  const cutoff = jobage === null ? null : new Date(Date.now() - jobage * 86_400_000).toISOString().slice(0, 10);
  jobs = jobs.filter((job) => {
    if (!matchesQuery(job, flags.query as string | undefined, !!flags["title-only"])) return false;
    if (cutoff && job.date && job.date < cutoff) return false;
    // --remote and --location are an OR: remote anywhere, or mentions the place.
    if (flags.remote || location) {
      const placeHit = location ? `${job.location ?? ""} ${job.description.split("\n")[0]}`.toLowerCase().includes(location) : false;
      if (!((flags.remote && job.remote) || placeHit)) return false;
    }
    return true;
  });
  jobs.sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""));
  const results = jobs.slice(0, limit).map((job) => ({
    id: job.id,
    source: job.source,
    title: job.title,
    company: job.company,
    location: job.location,
    remote: job.remote,
    date: job.date,
    url: job.url,
    applyUrl: job.applyUrl,
    description: job.description,
  }));
  render(results, format, !flags["no-description"]);
}

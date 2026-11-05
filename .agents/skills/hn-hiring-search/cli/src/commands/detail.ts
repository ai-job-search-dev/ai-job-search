import { fail, getJson, parseHiringComment, render, toText, ycCompany, REMOTE, type FirebaseItem } from "../helpers";

const FIREBASE = "https://hacker-news.firebaseio.com/v0";

/** `detail`: one HN item (who-is-hiring comment or YC job post), plus isActive. */
export async function detail(target: string | undefined, format: string) {
  if (!target) fail("detail needs an id (hn-123, yc-123, 123) or a news.ycombinator.com URL", "NO_ID");
  const id = target.match(/(\d+)/)?.[1] ?? fail(`no item id in ${target}`, "NO_ID");
  const item = await getJson<FirebaseItem | null>(`${FIREBASE}/item/${id}.json`);
  if (!item) fail(`item ${id} not found`, "NOT_FOUND");
  const isActive = !item.dead && !item.deleted;
  const description = toText(item.text);
  const job = item.title
    ? {
        id: `yc-${id}`,
        source: "yc-jobs" as const,
        title: item.title,
        company: ycCompany(item.title),
        location: null,
        remote: REMOTE.test(`${item.title} ${description}`),
        date: new Date(item.time * 1000).toISOString().slice(0, 10),
        url: item.url?.trim() || `https://news.ycombinator.com/item?id=${id}`,
        applyUrl: item.url?.trim() || null,
        description,
      }
    : parseHiringComment(item.id, item.text ?? "", item.time);
  if (!job) fail(`item ${id} is not a job post`, "NOT_A_JOB");
  if (format === "json") console.log(JSON.stringify({ ...job, isActive }, null, 2));
  else render([job], "plain", true);
}

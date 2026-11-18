// Which URLs the fetch proxy will fetch, and nothing else.
//
// Four strict shapes, each a thing the archive's Worker needs and cannot fetch
// itself (its hosts are all on Cloudflare; see docs/worker-fetch-block.md):
//
//   share          https://chatgpt.com/share/…, https://chat.openai.com/share/…
//   image          https://images.openai.com/static-rsc-…
//   reportResolver https://chatgpt.com/backend-anon/share/<uuid>/file_from_message/<uuid>
//                  ?file_path=/mnt/data/…   (that one query parameter, nothing else)
//   reportFile     https://sdmntpr<region>.oaiusercontent.com/files/<id>/raw?<signature>
//
// The last two are how a share's linked report file downloads signed out: the
// resolver answers a short-lived signed blob URL, which is fetched at once and
// whose signed query is passed through untouched.

export type Allowed = "share" | "image" | "reportResolver" | "reportFile";

const SHARE_ORIGINS = new Set([
  "https://chatgpt.com",
  "https://chat.openai.com",
]);

const IMAGE_ORIGIN = "https://images.openai.com";
const IMAGE_PATH_PREFIX = "/static-rsc-";

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const REPORT_RESOLVER_ORIGIN = "https://chatgpt.com";
const REPORT_RESOLVER_PATH = new RegExp(
  `^/backend-anon/share/${UUID}/file_from_message/${UUID}$`,
  "i",
);
const REPORT_FILE_HOST = /^sdmntpr[a-z0-9]+\.oaiusercontent\.com$/;
const REPORT_FILE_PATH = /^\/files\/[A-Za-z0-9_-]+\/raw$/;

export function allowedKind(url: URL): Allowed | null {
  if (url.username || url.password) return null;
  if (url.origin === IMAGE_ORIGIN && url.pathname.startsWith(IMAGE_PATH_PREFIX)) return "image";
  if (isReportResolver(url)) return "reportResolver";
  if (SHARE_ORIGINS.has(url.origin) && url.pathname.startsWith("/share/")) return "share";
  if (isReportFile(url)) return "reportFile";
  return null;
}

function isReportResolver(url: URL): boolean {
  if (url.origin !== REPORT_RESOLVER_ORIGIN || !REPORT_RESOLVER_PATH.test(url.pathname)) {
    return false;
  }
  const keys = [...url.searchParams.keys()];
  return keys.length === 1 && keys[0] === "file_path" &&
    url.searchParams.get("file_path")!.startsWith("/mnt/data/");
}

function isReportFile(url: URL): boolean {
  return url.protocol === "https:" && url.port === "" &&
    REPORT_FILE_HOST.test(url.hostname) && REPORT_FILE_PATH.test(url.pathname);
}

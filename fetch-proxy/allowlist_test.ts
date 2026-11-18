// The proxy's allowlist: exactly the shapes the Worker needs, nothing wider.
//
// Run:  deno test fetch-proxy/

import { assertEquals } from "jsr:@std/assert@1";
import { allowedKind } from "./allowlist.ts";

const SHARE = "6afc6e92-83c4-83e8-a528-542c00a41add";
const MESSAGE = "98511ba3-be9f-4902-a9f0-840056cd2e6c";
const RESOLVER = `https://chatgpt.com/backend-anon/share/${SHARE}/file_from_message/${MESSAGE}`;
const BLOB = "https://sdmntprnznorth.oaiusercontent.com/files/00000000-a4f4-820b-8b39-a3a54d01df67/raw";

function kind(url: string) {
  return allowedKind(new URL(url));
}

Deno.test("share pages and GenUI images are still allowed", () => {
  assertEquals(kind(`https://chatgpt.com/share/${SHARE}`), "share");
  assertEquals(kind(`https://chat.openai.com/share/${SHARE}`), "share");
  assertEquals(kind("https://images.openai.com/static-rsc-4/abc.png"), "image");
});

Deno.test("the report resolver is allowed with only a sandbox file_path", () => {
  assertEquals(kind(`${RESOLVER}?file_path=/mnt/data/report.md`), "reportResolver");
  assertEquals(kind(`${RESOLVER}?file_path=%2Fmnt%2Fdata%2Fa%20b.md`), "reportResolver");
});

Deno.test("the report resolver is refused in any other shape", () => {
  assertEquals(kind(RESOLVER), null); // no file_path
  assertEquals(kind(`${RESOLVER}?file_path=/etc/passwd`), null);
  assertEquals(kind(`${RESOLVER}?file_path=/mnt/data/a.md&x=1`), null);
  assertEquals(kind(`${RESOLVER}?file_path=/mnt/data/a.md&file_path=/mnt/data/b.md`), null);
  assertEquals(kind(`${RESOLVER}/extra?file_path=/mnt/data/a.md`), null);
  assertEquals(
    kind(`https://chatgpt.com/backend-anon/share/not-a-uuid/file_from_message/${MESSAGE}?file_path=/mnt/data/a.md`),
    null,
  );
  assertEquals(
    kind(`https://chatgpt.com/backend-api/share/${SHARE}/file_from_message/${MESSAGE}?file_path=/mnt/data/a.md`),
    null,
  ); // the signed-in API
  assertEquals(
    kind(`https://chat.openai.com/backend-anon/share/${SHARE}/file_from_message/${MESSAGE}?file_path=/mnt/data/a.md`),
    null,
  );
  assertEquals(kind(`https://chatgpt.com/backend-anon/conversation?file_path=/mnt/data/a.md`), null);
});

Deno.test("a signed report blob is allowed with its query untouched", () => {
  const signed = `${BLOB}?se=2026-11-18T08%3A32%3A13Z&sp=r&sv=2026-06-06&sr=b&sig=a%2Bb%3D`;
  assertEquals(kind(signed), "reportFile");
  assertEquals(new URL(signed).href, signed);
  assertEquals(kind(BLOB.replace("nznorth", "indiasocentral")), "reportFile");
});

Deno.test("anything else on the blob hosts is refused", () => {
  assertEquals(kind(BLOB.replace("/raw", "/raw/x")), null);
  assertEquals(kind(BLOB.replace("/raw", "")), null);
  assertEquals(kind(BLOB.replace("https:", "http:")), null);
  assertEquals(kind(BLOB.replace(".com/", ".com:8443/")), null);
  assertEquals(kind(BLOB.replace("sdmntprnznorth", "files")), null);
  assertEquals(kind(BLOB.replace("sdmntprnznorth.oaiusercontent.com", "sdmntprx.oaiusercontent.com.evil.test")), null);
  assertEquals(kind(BLOB.replace("https://", "https://user:pw@")), null);
  assertEquals(kind("https://oaiusercontent.com/files/x/raw"), null);
});

Deno.test("unrelated hosts and paths are refused", () => {
  assertEquals(kind("https://example.com/share/x"), null);
  assertEquals(kind("https://chatgpt.com/backend-api/conversation/x"), null);
  assertEquals(kind("https://images.openai.com/other.png"), null);
});

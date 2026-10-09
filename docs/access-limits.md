# Can Cloudflare Access protect the login page, and the OTP that follows it?

**Short answer: it can protect a path, but it cannot protect *your* login page —
because Access's own one-time-PIN screen is the login page. You have to pick one
of the two, and the reason is a hard ordering rule, not a configuration detail.**

Every claim below is quoted from `developers.cloudflare.com`. Where the docs are
silent, this file says **NOT DOCUMENTED** instead of guessing. Two things I
believed before writing this turned out to be wrong; they are corrected in
§7 and §8.

---

## 1. The ordering rule that makes it circular

Access is an identity-aware proxy that runs **before** your code:

> "With Cloudflare Access, you can restrict who is authorized to access your
> application. You decide who is approved, and **every request is checked before
> your Worker runs**. Approved visitors are let through, while everyone else is
> shown a login page or blocked."
> — <https://developers.cloudflare.com/workers/configuration/cloudflare-access/>

> "When you protect a site with Cloudflare Access, Cloudflare checks every HTTP
> request bound for that site to ensure that the request has a valid
> `CF_Authorization` cookie. If a request does not include the cookie, Access
> will block the request."
> — <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/>

So if `/chat-archives?login` is behind Access, an unauthenticated visitor never
reaches our `AuthFlow` component. They get Cloudflare's screen instead. Our
six-digit code inputs would be dead code — and worse, there would then be *two*
OTP flows stacked, Cloudflare's and ours.

This is the circularity: **Access's login page is the thing you would be
protecting.** Protecting it means replacing it.

## 2. Path matching works; query strings do not

Access applications are scoped by hostname plus an optional path, and paths
support wildcards:

> "When you create an application for a specific subdomain or path, you can use
> asterisks (`*`) as wildcards."
> — <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/>

But:

> "**Query strings (such as`?foo=bar`) are not supported in Access application
> paths.**"
> — same page (Cloudflare's missing space after "such as" is reproduced verbatim)

This is decisive for the route layout we chose. `/chat-archives` and
`/chat-archives?login` are the **same Access-scoped URL**. There is no Access
rule that protects one and not the other.

*Precision note:* the docs say query strings are unsupported *in the Path
field*, i.e. you cannot author them as a match rule. They do not explicitly say
what happens to an inbound request carrying a query string. That the query
string is simply not part of the match is an **inference**, not a quote — but it
is the only reading consistent with "not supported".

Related limits on the same page:

> "Port numbers are not supported in Access application paths."
> "Since anchor links are processed by the browser and not the server, Access
> applications do not support `#` characters in the URL."

And nesting works as you would hope:

> "When multiple rules are set for a common root path, the more specific rule
> takes precedence."

## 3. Access has no notion of HTTP method at all

This was the load-bearing assumption for the public-read / private-write split,
and it holds. The policies page lists four building blocks — Actions, Rule
types, Selectors, Values — and the **complete** selector list is: Emails, Emails
ending in, External Evaluation, IP ranges, Country, Everyone, Common Name, Valid
Certificate, Service Token, Any Access Service Token, User Risk Score, Linked
App Token, Login Methods, Authentication Method, Identity provider group, SAML
Group, OIDC Claim, Device posture, Warp, Gateway, Cloudflare Account Member.

There is no HTTP-method row.
— <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/>

The machine-readable policy schema confirms it: its full set of rule variants
contains no member representing a request verb. (Careful: `auth_method` and
`login_method` refer to *authentication* methods — OTP vs SSO vs MFA — not to
`GET`/`POST`.)
— <https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/policies/methods/create/>

**Conclusion: "anyone may GET a story, only the owner may POST an import" cannot
be written as an Access policy. It has to be enforced in the Worker.** That is
what `entry.py` does.

## 4. A path *can* be made public inside a protected app — via Bypass

Relevant to the reader path, and worth knowing before dismissing Access entirely:

> "The **Bypass** action in Cloudflare Access disables Access enforcement for
> specific traffic."

> "Bypass does not enforce any Access security controls and requests are **not
> logged**."

> "For example, some applications have an endpoint under the `/admin` route that
> must be publicly routable. In this situation, you could create an Access
> application for the domain `test.example.com/admin/<your-url>` and add the
> Bypass policy […] Action `Bypass` | Rule type `Include` | Selector `Everyone`"
> — <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/>

Newer API surface exposes the same idea per-destination, which is the cleanest
machine-readable statement of it:

> `overrides: optional array of DestinationOverride { behavior, path_pattern }`
> — "Rules that override how Access handles requests to this destination. Each
> rule can make a matching path **public**, bypassing Access authentication."
> — <https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/create/>

So Access *can* express "these paths are public, those need a login". What it
still cannot express is "public for GET, private for POST" (§3), and it cannot
split `?login` from no-query (§2).

Also note a matching subtlety that differs from ordinary app paths: override
patterns "do not implicitly cover subpaths; to do that, use a wildcard."

## 5. The login page is brandable, not replaceable

> "Give the login page the look and feel of your organization by adding: Your
> organization's name / A logo / A custom header and footer / A preferred
> background color"
> — <https://developers.cloudflare.com/cloudflare-one/reusable-components/custom-pages/access-login-page/>

That is the whole documented customization surface. Custom HTML is documented
only for **block** pages, and only on Pay-as-you-go and Enterprise:

> "**Custom Page Template** - (Only available on Pay-as-you-go and Enterprise
> plans) Displays a custom HTML page hosted by Cloudflare."

The API schema *does* contain a custom-page `type` of `'login'` with a
`custom_html` field and a Liquid-template `contract_version` — but there is no
how-to, no template reference and no documented Liquid variables for it
anywhere in the Cloudflare One docs. **Treat it as "exists in the API schema,
undocumented as a supported path"**, not as "you can build a custom login UI".

We already have the Rail login screens built (`AuthFlow.tsx`, screenshots
04–07). Under Access they would be replaced by a logo-and-colours page.

## 6. The OTP itself needs no email provider

If you let Access own the login, you do not need Resend, SendGrid, or anything
else:

> "You can also send a one-time PIN (OTP) to approved email addresses. **No
> configuration needed** — simply add a user's email address to an Access policy
> and to the group that allows your team to reach the application."
> — <https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/>

Cloudflare sends the mail (from `noreply@notify.cloudflare.com`). PIN rules:

> "OTP codes are single-use and expire 10 minutes after the initial request."
> "Requesting a new PIN invalidates the previous PIN."
> "By design, blocked users will not receive an email. The login page will
> always say **A code has been emailed to you**, regardless of whether or not an
> email was sent."

**NOT DOCUMENTED:** any price for OTP, and any maximum-attempt / lockout
threshold. I searched the One-time PIN page and the Access account-limits table;
neither states one. So do **not** claim OTP is free — that claim has no citation.
(The nearest documented abuse control is the `CF_Device` cookie: "used to
prevent abuse of one-time PIN and multi-factor authentication flows".)

Security footgun worth recording:

> "Adding `Login Methods: One-time PIN` as an Include rule without restricting
> email domains allows anyone with any email address to receive a code and
> access the application. Always pair OTP with specific email domains or an
> email list in the Include rule."

## 7. Correction: `Accept: application/json` giving a JSON 401 is NOT documented

I previously said Access returns a JSON 401 to `fetch()` when the request carries
`Accept: application/json`. **I could not find that documented anywhere**, and
the `#rest-api` section I remembered no longer exists on the
authorization-cookie page. Do not rely on it.

What *is* documented, for non-browser clients, is the opposite by default:

> "When you protect an application with Cloudflare Access, by default
> non-browser clients — such as CLIs, AI agents, SDKs, and scripts — cannot
> complete the browser-based login redirect. They receive a **`302` redirect**
> with no usable token or authorization endpoint."
> — <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/>

This is exactly the failure mode flagged in `UploadInline.tsx`: a `fetch()` POST
that gets 302'd to a login page is followed as a GET, the body is dropped, and
the import silently does nothing.

The documented ways to get a 401 instead:

| Trigger | Mechanism | Citation |
|---|---|---|
| Managed OAuth enabled | 401 + `WWW-Authenticate` + `resource_metadata` JSON body | [managed-oauth](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/) |
| Strict service-token auth | "Access always returns `401` or `403` instead of redirecting the client to the login page with `302`" | [service-tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/) |
| Expired session, AJAX | add header `X-Requested-With: XMLHttpRequest` | [authorization-cookie](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/) |
| Account setting for the Cloudflare One Client | 401 for non-browser traffic | [access-settings](https://developers.cloudflare.com/cloudflare-one/access-controls/access-settings/session-management/) |

The AJAX one is notable: it is gated on `X-Requested-With`, **not** on `Accept`.

## 8. Correction: `ctx.access` in Python Workers is undocumented

The `ctx.access` API is documented **only for JavaScript**:

> "When Cloudflare Access authenticates a request that directly invokes your
> Worker, the Worker can read the signed-in user's identity — including email,
> groups, device posture, and more identity fields — through `ctx.access`. No
> extra configuration or JWT parsing is required."
> "`ctx.access` is `undefined` if Access did not authenticate the request."
> — <https://developers.cloudflare.com/workers/configuration/cloudflare-access/>

Two attribution traps:

1. The **Context API page does not document `ctx.access`** — its headings are
   only `props`, `exports`, `tracing`, `waitUntil`, `passThroughOnException`.
   Cloudflare's own Access changelog links to `context/#access`, an anchor that
   no longer resolves. Cite the configuration page instead.
   <https://developers.cloudflare.com/workers/runtime-apis/context/>
2. Every `ctx.access` example in the docs is JS, and the token appears on 5
   Workers pages, **none of them a Python page**.

What *is* documented for Python is that the ctx object exists:

> "Context is exposed via the following places: […] As a class property of the
> `WorkerEntrypoint` class (`this.ctx`)"

and the docs' own Python Cache API example uses `self.ctx.waitUntil(...)`. So
`self.ctx.access` may well work — but that is **my inference, not a citation**.

**Consequence for this repo:** `CloudflareAccessIdentity` in `auth.py` relies on
exactly this undocumented behaviour. It fails closed (no `ctx.access` → no
identity → 401), which is the right direction, but a fail-closed system that
fails *always* is indistinguishable from a broken one. Hence
`GET /api/health`, which reports `context_available` and `access_available` so
the deployed Worker can answer the question empirically:

```bash
curl https://akshayprabhakant.com/api/health
```

Also documented, and relevant if the Worker is ever split via service bindings:

> "Cloudflare Access does not propagate `ctx.access` through Service Binding HTTP
> requests or remote procedure call (RPC) invocations."
> "Workers with Static Assets execute behind an internal router Worker. […]
> the router does not pass `ctx.access` to the user Worker."

## 9. One more constraint: the cookie is per-hostname

> "Users who log in to `example.com` will be issued a cookie for `example.com`.
> When the user's browser requests `api.mysite.com`, Cloudflare Access looks for
> a cookie specific to `api.mysite.com`."
> — <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/>

This is an argument for the one-domain topology already chosen (`/api/*` and
`/chat-archives/*` on the same hostname) rather than `api.akshayprabhakant.com`
plus `akshayprabhakant.com`. A cross-subdomain split would need a second login.

---

## What this means for us

The three rules we settled on —

| Action | Identity required |
|---|---|
| read one story by `share-id` | no |
| list a shelf | yes |
| import a share link | yes |

— **cannot be delegated to Access**, because the middle row and the third row are
the same path prefix and differ only by method. So they are enforced in
`entry.py`, and Access is optional.

That leaves one genuinely open decision, and it is a UX decision rather than a
technical one:

**Option A — Access owns the login.**
Delete `AuthFlow.tsx`. `/api/sessions` and `/api/ingest` go behind Access with
`/api/sessions/*` bypassed (or on a separate path). Cloudflare emails the OTP;
`ctx.access` supplies the email for per-email scoping. No email provider, no
auth code, no screenshots 04–07. Cost: the login page is Cloudflare's, branded
with a logo and colours at best; and we must first verify that Python actually
exposes `ctx.access`, or the whole thing 401s.

**Option B — we own the login.**
`AuthFlow.tsx` becomes real. We need an email sender (Resend's free tier is the
usual pick) because the Worker must mail the code. Access cannot authenticate
those routes, so it is dropped from them. Cost: a third-party dependency and a
small amount of auth code; benefit: the Rail login screens are actually used, and
no undocumented Python Access API is on the critical path.

Either way, **per-email scoping stays our code**: Access can gate a path, but it
can never filter a `SELECT` by owner. That is `conversations.owner_email`.

## Sources

- Access application paths / wildcards / query strings — <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/>
- Access policies, actions, the full selector list, Bypass — <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/>
- Access application creation (self-hosted public app) — <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/>
- Authorization cookie, JWT validation — <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/>
- Managed OAuth (the documented 401-vs-302 behaviour) — <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/>
- One-time PIN — <https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/>
- Service tokens — <https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/>
- Access login page branding — <https://developers.cloudflare.com/cloudflare-one/reusable-components/custom-pages/access-login-page/>
- Workers + Access (`ctx.access`) — <https://developers.cloudflare.com/workers/configuration/cloudflare-access/>
- Context API — <https://developers.cloudflare.com/workers/runtime-apis/context/>
- Access applications API schema (`overrides`, `custom_html`, `type`) — <https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/create/>
- Access policies API schema (complete rule-variant union) — <https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/policies/methods/create/>

*Method note: the "NOT DOCUMENTED" claims were checked by full-text search over
the complete Cloudflare One (742 pages) and Workers (420 pages) documentation
sets fetched as markdown, not by sampling.*

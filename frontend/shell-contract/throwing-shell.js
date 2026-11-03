// Test only: a site-shell whose compose() always rejects. Never deployed.
import { WorkerEntrypoint } from 'cloudflare:workers';

export default class ThrowingShell extends WorkerEntrypoint {
  async fetch() {
    return new Response('Not Found', { status: 404, headers: { 'cache-control': 'no-store' } });
  }

  async compose() {
    throw new Error('fault: compose threw');
  }
}

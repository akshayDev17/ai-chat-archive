/** @type {import('next').NextConfig} */
const nextConfig = {
  // The backend (Python Worker) is a separate origin; the UI talks to it via
  // NEXT_PUBLIC_API_BASE (see lib/api.ts).

  // Off because the floating badge sits over the bottom-left of the page — on a
  // layout this deliberate it covers content, and it ends up baked into any
  // screenshot taken from the dev server.
  devIndicators: false,

  // Static export: the archive is served as plain files from a Worker at
  // /chat-archives/*, so there is no Next.js server. This also means no image
  // optimization endpoint, hence `images.unoptimized`.
  output: 'export',
  images: { unoptimized: true },

  // Where Next writes its build output.
  //
  // `next dev` and `next build` both own this directory and both rewrite the
  // webpack chunk graph in it. Running them against the SAME directory at the
  // same time corrupts it: the build replaces the chunks the dev server's
  // `webpack-runtime.js` is still pointing at, and the dev server then dies
  // with "Cannot find module './36.js'" on every request.
  //
  // So a verification build gets its own directory. `npm run build:check` sets
  // NEXT_DIST_DIR, which makes it safe to run at any time — including while the
  // dev server is live, which is exactly when you want to check your work.
  distDir: process.env.NEXT_DIST_DIR ?? '.next',
};

export default nextConfig;

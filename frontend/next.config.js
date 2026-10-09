/** @type {import('next').NextConfig} */
const nextConfig = {
  // The backend (Python Worker) is a separate origin; the UI talks to it via
  // NEXT_PUBLIC_API_BASE (see lib/api.ts).
  //
  // Off because the floating badge sits over the bottom-left of the page — on a
  // layout this deliberate it covers content, and it ends up baked into any
  // screenshot taken from the dev server.
  devIndicators: false,
};

export default nextConfig;

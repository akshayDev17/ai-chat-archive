/** @type {import('next').NextConfig} */
const nextConfig = {
  // The backend (Python Worker) is a separate origin; the UI talks to it via
  // NEXT_PUBLIC_API_BASE (see lib/api.ts).
};

export default nextConfig;

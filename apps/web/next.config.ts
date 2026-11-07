import type { NextConfig } from "next";

// API_ORIGIN is server-only. The browser always calls the same-origin /api/v1.
const apiOrigin = new URL(process.env.API_ORIGIN ?? "http://127.0.0.1:8000");
if (!['http:', 'https:'].includes(apiOrigin.protocol) || apiOrigin.username || apiOrigin.password) {
  throw new Error("API_ORIGIN must be an HTTP(S) origin without credentials.");
}
if (apiOrigin.pathname !== "/" || apiOrigin.search || apiOrigin.hash) {
  throw new Error("API_ORIGIN must contain only a scheme, host and optional port.");
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiOrigin.origin}/api/v1/:path*` }];
  },
};

export default nextConfig;

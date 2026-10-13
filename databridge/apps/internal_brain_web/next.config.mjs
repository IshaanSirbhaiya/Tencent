/**
 * Walnut Internal Brain web.
 *
 * The browser only ever talks to same-origin `/api/ib/*`. That path is served by a route handler
 * (app/api/ib/[...path]/route.ts) which either proxies to the IB API (IB_API_URL, read at RUNTIME on
 * the server, never shipped to the browser) or, in mock mode, serves fixtures. The httpOnly
 * `ib_session` cookie is relayed untouched; no token is ever readable from JavaScript.
 */
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  distDir: process.env.NEXT_DIST_DIR || '.next',
  output: process.env.NEXT_OUTPUT === 'standalone' ? 'standalone' : undefined,
  eslint: { ignoreDuringBuilds: true },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'same-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
        ],
      },
    ];
  },
};

export default nextConfig;

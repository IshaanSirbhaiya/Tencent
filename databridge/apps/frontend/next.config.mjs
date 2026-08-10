/** @type {import('next').NextConfig} */
// Airgapped (D-FE-3): no CDN, no external fetch. System-font stack (no Google Fonts), telemetry
// disabled via env. The gateway is proxied through NEXT_PUBLIC_GATEWAY_URL (loopback only).
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;

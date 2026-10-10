import type { NextConfig } from "next";

// Same-origin API access. In production the edge proxy (SaaS-OS single-origin
// runtime) routes /v1 and /auth to the backend. For `next dev` without that
// proxy, set LEADSTACK_DEV_BACKEND_URL (e.g. http://localhost:8000) to rewrite
// those two prefixes to the backend. Never enabled in production builds.
const devBackend = process.env.NODE_ENV === "development" ? process.env.LEADSTACK_DEV_BACKEND_URL : undefined;

const isDev = process.env.NODE_ENV === "development";

// Next.js emits inline bootstrap scripts, hence 'unsafe-inline' for scripts
// (a nonce-based policy needs middleware and is planned with hardening).
// 'unsafe-eval' is only needed by the development server.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  // Browsers ignore HSTS over plain HTTP, so this is inert in local development.
  ...(isDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The container image builds a self-contained server (deploy/Dockerfile.frontend):
  // the configuration is compiled in, so the runtime needs neither TypeScript
  // nor the full node_modules. Local builds are unchanged.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // Confirmation and unsubscribe links carry a token in the path: these
      // pages never send it on as a referrer (later entries override).
      { source: "/:kind(c|u)/:token", headers: [{ key: "Referrer-Policy", value: "no-referrer" }] },
    ];
  },
  async rewrites() {
    if (!devBackend) return [];
    return [
      { source: "/v1/:path*", destination: `${devBackend}/v1/:path*` },
      { source: "/auth/:path*", destination: `${devBackend}/auth/:path*` },
    ];
  },
};

export default nextConfig;

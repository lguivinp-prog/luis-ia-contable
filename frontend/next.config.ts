import type { NextConfig } from "next";

const backendInternalUrl = process.env.BACKEND_INTERNAL_URL ?? "http://backend:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendInternalUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

import type { NextConfig } from "next";

const backendInternalUrl = process.env.BACKEND_INTERNAL_URL ?? "http://backend:8000";

const nextConfig: NextConfig = {
  // El servidor de desarrollo se inicia como `localhost`; sin esto bloquea los recursos de desarrollo (HMR/hidratacion)
  // cuando se abre la aplicacion por la IP de loopback `127.0.0.1` y los formularios dejan de funcionar.
  allowedDevOrigins: ["127.0.0.1"],
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

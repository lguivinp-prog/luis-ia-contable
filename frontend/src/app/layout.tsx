import type { Metadata, Viewport } from "next";

import { ServiceWorkerRegister } from "@/components/ServiceWorkerRegister";

import "@/components/ui/ui.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Luis IA Contable",
  description: "Control contable, tributario, documental y de honorarios para un contador independiente peruano.",
  manifest: "/manifest.json",
};

export const viewport: Viewport = {
  themeColor: "#1d4e89",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body>
        {children}
        <ServiceWorkerRegister />
      </body>
    </html>
  );
}

import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AutoExtract",
  description: "Self-improving extraction service",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

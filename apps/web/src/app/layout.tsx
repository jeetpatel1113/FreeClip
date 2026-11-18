import type { Metadata } from "next";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "FreeClip: AI vertical clips",
  description: "Turn long videos into captioned vertical clips and publish them automatically.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen font-sans">
        <Nav />
        <main className="container py-8">{children}</main>
      </body>
    </html>
  );
}

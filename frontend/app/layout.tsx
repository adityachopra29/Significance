import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Announcement Intelligence Engine",
  description: "Ranked NSE/BSE corporate-announcement signals for traders",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

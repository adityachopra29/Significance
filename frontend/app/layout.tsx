import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Significance",
  description: "Ranked NSE/BSE corporate announcement signals for traders",
  icons: {
    icon: "/logo.png",
    apple: "/logo.png",
  },
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

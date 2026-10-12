import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Emerging Market Radar",
  description: "公開ニュースから新興市場の形成兆候を発見する",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}

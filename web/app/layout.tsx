import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Header } from "../components/Header.tsx";
import { Inspector } from "../components/Inspector.tsx";
import { Nav } from "../components/Nav.tsx";
import { GameProvider } from "../lib/stream.tsx";
import "./globals.css";

export const metadata: Metadata = {
  title: "Bazaar Agent Live",
  description: "What our Bazaar agent is doing, tick by tick",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <GameProvider>
          <Header />
          <Nav />
          <main className="page">{children}</main>
          <Inspector />
        </GameProvider>
      </body>
    </html>
  );
}

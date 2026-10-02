"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export const ROUTES = [
  { href: "/", label: "Agent", hint: "what our agent is doing" },
  { href: "/negotiations/", label: "Negotiations", hint: "our threads and duels" },
  { href: "/album/", label: "Album", hint: "pages and score" },
  { href: "/market/", label: "Market", hint: "everyone else" },
  { href: "/debug/", label: "Debug", hint: "raw event stream" },
];

export function Nav() {
  const path = usePathname();
  const norm = (p: string) => (p.endsWith("/") ? p : `${p}/`);
  return (
    <nav className="nav" aria-label="Screens">
      {ROUTES.map((r) => (
        <Link key={r.href} href={r.href} title={r.hint} className={norm(path) === norm(r.href) ? "on" : undefined}>
          {r.label}
        </Link>
      ))}
    </nav>
  );
}

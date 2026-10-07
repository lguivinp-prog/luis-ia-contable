"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import { useCurrentUser } from "@/lib/currentUserContext";
import { activeNavHref, buildNavGroups } from "@/lib/navigation";

interface SidebarProps {
  open: boolean; // solo aplica al cajon movil
  onClose: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const pathname = usePathname();
  const { user } = useCurrentUser();
  const groups = buildNavGroups(user);
  const activeHref = activeNavHref(pathname, groups);
  const navRef = useRef<HTMLElement>(null);

  // Cajon movil: se cierra al navegar y con Escape; al abrirse, el foco pasa al primer enlace.
  useEffect(() => {
    onClose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    navRef.current?.querySelector<HTMLElement>("a")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {open && <div className="app-nav-backdrop" onClick={onClose} aria-hidden="true" />}
      <nav
        id="app-navigation"
        ref={navRef}
        aria-label="Navegación principal"
        className={`app-nav${open ? " app-nav-open" : ""}`}
      >
        {groups.map((group) => (
          <div key={group.id} className="app-nav-group">
            <div className="app-nav-heading">{group.label}</div>
            {group.items.map((item) => {
              const active = item.href === activeHref;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`app-nav-link${active ? " app-nav-link-active" : ""}`}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
    </>
  );
}

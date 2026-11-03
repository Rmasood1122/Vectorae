"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useAuthStore } from "@/lib/stores/authStore";
import { LogoMark } from "@/components/ui/Logo";
import { ConfigWarningsBanner } from "@/components/admin/ConfigWarningsBanner";
import { adminApi } from "@/lib/api/admin";
import { currentPath, rememberReturnTo } from "@/lib/returnTo";
import {
  Users, AlertCircle, Shield, BookOpen, Heart, Zap, List,
  GraduationCap, LifeBuoy, KeyRound, SlidersHorizontal, ShieldAlert, ShieldCheck, UserPlus, TrendingUp
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/admin/users",            label: "Users",             icon: Users      },
  // Who signed up and from where, plus refused attempts (signup_watch.py).
  { href: "/admin/signups",          label: "Sign-ups",          icon: UserPlus   },
  // Section B: onboarding location mismatches awaiting a human decision.
  { href: "/admin/identity-reviews", label: "Identity Reviews",  icon: ShieldAlert },
  { href: "/admin/task-errors",      label: "Task Errors",       icon: AlertCircle},
  { href: "/admin/circuit-breakers", label: "Circuit Breakers",  icon: Zap        },
  { href: "/admin/suppression-list", label: "Suppression List",  icon: List       },
  { href: "/admin/playbook",         label: "Playbook Scores",   icon: BookOpen   },
  { href: "/admin/support-tickets",  label: "Support Tickets",   icon: LifeBuoy   },
  { href: "/admin/tutorials",        label: "Tutorials",         icon: GraduationCap },
  { href: "/admin/health",           label: "System Health",     icon: Heart      },
  // Feature expansion: deployment-wide API keys (TokenStore, never env vars)
  // and the feature switches/limits the new features read.
  { href: "/admin/integrations",     label: "Integrations",      icon: KeyRound   },
  { href: "/admin/system-settings",  label: "System Settings",   icon: SlidersHorizontal },
  // Feature 8: send windows, caps, consent and bounce thresholds per
  // workspace / region / channel, resolved fail-closed at send time.
  { href: "/admin/compliance-rules", label: "Compliance Rules",  icon: ShieldCheck },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuthStore();
  const router = useRouter();
  // Admin > Growth is limited to named addresses (GROWTH_DASHBOARD_VIEWERS);
  // the link appears only when the server says this account is one of them.
  const { data: growthAccess } = useQuery({
    queryKey: ["admin", "growth-access", user?.email],
    queryFn: adminApi.growthAccess,
    enabled: Boolean(user?.is_admin),
    retry: false,
    staleTime: 5 * 60_000,
  });
  const navItems = growthAccess?.allowed
    ? [{ href: "/admin/growth", label: "Growth", icon: TrendingUp }, ...NAV_ITEMS]
    : NAV_ITEMS;

  useEffect(() => {
    if (isLoading) return;
    if (!user) {
      // Signed out: sign in, then come straight back to this admin page.
      rememberReturnTo(currentPath());
      router.replace("/login");
    } else if (!user.is_admin) {
      router.replace("/");
    }
  }, [user, isLoading, router]);

  if (isLoading || !user?.is_admin) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-muted-foreground">Checking access…</div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <aside className="w-56 border-r bg-muted/30 flex flex-col p-4 gap-1 shrink-0">
        {/* The admin shell had no brand anchor at all; the mark ties it back
            to the main app shell without competing with the section label. */}
        <div className="flex items-center gap-2 mb-4 px-2">
          <LogoMark size={20} />
          <span className="font-semibold text-sm text-muted-foreground uppercase tracking-widest">
            Admin Panel
          </span>
        </div>
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            <Icon className="w-4 h-4" />
            {label}
          </Link>
        ))}
      </aside>
      <main className="flex-1 overflow-auto p-6">
        <ConfigWarningsBanner />
        {children}
      </main>
    </div>
  );
}

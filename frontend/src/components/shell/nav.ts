import {
  BarChart3,
  Building2,
  CalendarDays,
  DollarSign,
  GraduationCap,
  Inbox,
  KanbanSquare,
  LayoutGrid,
  Megaphone,
  Network,
  Route,
  Settings,
  UserCircle,
  Users,
  Video,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

/** Nav sections per project knowledge section H. */
export const NAV: NavItem[] = [
  { href: "/pipeline", label: "Pipeline", icon: KanbanSquare },
  // Part 1 Feature 6: every reply on every channel, threaded by person.
  // Second, next to Pipeline, because those are the two screens opened every
  // morning -- one is "who should I contact", the other is "who is waiting on
  // me". The mobile bar scrolls horizontally, so an eleventh entry costs a
  // swipe rather than a hidden destination.
  { href: "/inbox", label: "Inbox", icon: Inbox },
  // M9. Sits next to Pipeline because they are the same data seen two
  // ways -- the kanban for working a handful of leads by hand, the CRM
  // for the dashboard view and for editing hundreds at a time. Pipeline
  // stays first: it is the screen people open every morning.
  { href: "/crm", label: "CRM", icon: LayoutGrid },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  // Engagement Hub. Placed immediately after Campaigns and in this order
  // because that is the order the work happens in: a campaign produces a
  // booking, a booking produces a meeting. Both sit ABOVE Strategies, which is
  // a screen you visit while setting up and rarely afterwards, whereas these
  // two are opened on the days you have calls.
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/meetings", label: "Meetings", icon: Video },
  { href: "/strategies", label: "Strategies", icon: Route },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  // Private LinkedIn network (Growth+). The page itself explains the upgrade.
  { href: "/network", label: "Network", icon: Network },
  // Feature 2. Sits before Settings, not after: Settings is the conventional
  // last item, and burying the tutorials below it is the fastest way to make
  // sure nobody finds them. On mobile this is the 6th of 7 bottom tabs --
  // CRM made it seven, which is tight at 360px but still tappable.
  { href: "/learn", label: "Learn", icon: GraduationCap },
  // Feature Group 8: members, approvals, white label.
  { href: "/team", label: "Team", icon: Users },
  // Part 1 Feature 11: the agency's clients -- separate reporting, separate
  // sending-domain pools, separate invoices. Next to Team because both are
  // "who is this account for", not "what is it doing today".
  { href: "/clients", label: "Clients", icon: Building2 },
  { href: "/usage", label: "Usage", icon: DollarSign },
  { href: "/profile", label: "Profile", icon: UserCircle },
  { href: "/settings", label: "Settings", icon: Settings },
];

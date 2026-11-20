import {
  Bot, Boxes, Brain, CircleStop, CircleX, Clock, Cloud, Code, Cpu, CreditCard, Database, FileText, GitBranch, Globe, HardDrive, ListOrdered, Mail, MessageSquare, Play, Repeat, Search, ShieldCheck, ShoppingCart, Terminal, type LucideIcon, Users, Webhook, Wrench, Zap,
} from "lucide-react";

/** Backend icon/category names -> icon + accent color. */
const ICONS: Record<string, { icon: LucideIcon; color: string }> = {
  webhook: { icon: Webhook, color: "#7c3aed" },
  schedule: { icon: Clock, color: "#7c3aed" },
  queue: { icon: ListOrdered, color: "#7c3aed" },
  terminal: { icon: Terminal, color: "#7c3aed" },
  event: { icon: Zap, color: "#7c3aed" },
  play: { icon: Play, color: "#16a34a" },
  stop: { icon: CircleStop, color: "#dc2626" },
  decision: { icon: GitBranch, color: "#d97706" },
  loop: { icon: Repeat, color: "#0891b2" },
  error: { icon: CircleX, color: "#dc2626" },
  agent: { icon: Bot, color: "#4f46e5" },
  llm: { icon: Brain, color: "#4f46e5" },
  tool: { icon: Wrench, color: "#64748b" },
  database: { icon: Database, color: "#2563eb" },
  cache: { icon: HardDrive, color: "#dc2626" },
  storage: { icon: HardDrive, color: "#0891b2" },
  cloud: { icon: Cloud, color: "#0891b2" },
  payment: { icon: CreditCard, color: "#059669" },
  commerce: { icon: ShoppingCart, color: "#65a30d" },
  chat: { icon: MessageSquare, color: "#db2777" },
  email: { icon: Mail, color: "#ea580c" },
  http: { icon: Globe, color: "#0d9488" },
  docs: { icon: FileText, color: "#475569" },
  devtools: { icon: Code, color: "#334155" },
  crm: { icon: Users, color: "#0f766e" },
  auth: { icon: ShieldCheck, color: "#b45309" },
  search: { icon: Search, color: "#ca8a04" },
  ml: { icon: Cpu, color: "#7c3aed" },
  code: { icon: Code, color: "#475569" },
};

export function iconFor(name: string) {
  return ICONS[name] ?? { icon: Boxes, color: "#475569" };
}

/** Soft, distinct colors for folder groups in the file graph and map. */
const PALETTE = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6",
                 "#14b8a6", "#f97316", "#84cc16", "#64748b"];

export interface FolderGroups {
  names: string[];                       // largest group first
  colorOf: (path: string) => string;
  colorOfGroup: (name: string) => string;
  groupOf: (path: string) => string;
}

/**
 * Group files by their first folder *below* the shared prefix, so a repo where
 * everything lives in "src/" still gets meaningful colors (src/api, src/db, ...).
 */
export function folderGroups(paths: string[]): FolderGroups {
  const dirs = paths.map((p) => p.split("/").slice(0, -1));
  let depth = 0;
  while (dirs.length > 0 && dirs.every((d) => d.length > depth && d[depth] === dirs[0][depth])) depth++;

  const groupOf = (path: string) => {
    const parts = path.split("/");
    return parts.length - 1 > depth ? parts[depth] : "(root)";
  };
  const counts = new Map<string, number>();
  for (const p of paths) counts.set(groupOf(p), (counts.get(groupOf(p)) ?? 0) + 1);
  const names = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
  const colorOfGroup = (name: string) => PALETTE[Math.max(names.indexOf(name), 0) % PALETTE.length];
  return { names, groupOf, colorOfGroup, colorOf: (path: string) => colorOfGroup(groupOf(path)) };
}

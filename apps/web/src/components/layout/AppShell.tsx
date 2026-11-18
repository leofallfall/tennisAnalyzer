import {
  BarChart3,
  Columns2,
  LayoutDashboard,
  ListVideo,
  Menu,
  Route,
  Server,
  UserRound,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/analyses', label: 'My Analyses', icon: ListVideo },
  { to: '/progress', label: 'Progress', icon: BarChart3 },
  { to: '/comparisons', label: 'Comparisons', icon: Columns2 },
  { to: '/improvement-paths', label: 'Improvement Paths', icon: Route },
  { to: '/profile', label: 'Player Profile', icon: UserRound },
]

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <img src="/favicon.svg" alt="" className="size-7" />
      <div className="leading-tight">
        <div className="text-sm font-semibold text-white">Tennis Performance</div>
        <div className="text-[11px] text-sidebar-muted">Analyzer · v0.1</div>
      </div>
    </div>
  )
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-sidebar-foreground/85 hover:bg-sidebar-active hover:text-white',
              isActive && 'bg-sidebar-active font-medium text-white',
            )
          }
        >
          <Icon className="size-4" aria-hidden />
          {label}
        </NavLink>
      ))}
    </nav>
  )
}

function SystemLink({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <NavLink
      to="/system"
      onClick={onNavigate}
      className="flex items-center gap-2 rounded-md px-2.5 py-2 text-xs text-sidebar-muted hover:text-white"
    >
      <Server className="size-3.5" aria-hidden /> System status
    </NavLink>
  )
}

export function AppShell() {
  const [open, setOpen] = useState(false)
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[232px_1fr]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh flex-col justify-between bg-sidebar p-3 lg:flex">
        <div className="flex flex-col gap-6">
          <div className="pt-2">
            <Brand />
          </div>
          <NavItems />
        </div>
        <SystemLink />
      </aside>

      {/* Mobile / tablet top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between bg-sidebar px-3 py-2 lg:hidden">
        <Brand />
        <button
          type="button"
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="rounded-md p-2 text-sidebar-foreground hover:bg-sidebar-active"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </header>
      {open && (
        <div className="fixed inset-x-0 top-[52px] z-20 border-b bg-sidebar p-3 lg:hidden">
          <NavItems onNavigate={() => setOpen(false)} />
          <SystemLink onNavigate={() => setOpen(false)} />
        </div>
      )}

      <main className="min-w-0 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
        <div className="mx-auto max-w-[1400px]">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

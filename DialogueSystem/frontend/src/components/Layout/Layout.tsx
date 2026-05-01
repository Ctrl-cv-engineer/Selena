import React from 'react';
import { Outlet, NavLink } from 'react-router-dom';
import { MessageSquare, Bug, Database, Calendar, Settings2, GitBranch, Cpu, Hammer, Zap } from 'lucide-react';
import clsx from 'clsx';

const navItems = [
  { path: '/', icon: MessageSquare, label: 'Chat' },
  { path: '/workbench', icon: Hammer, label: 'Workbench' },
  { path: '/debug', icon: Bug, label: 'Debug' },
  { path: '/llm-inspector', icon: Cpu, label: 'LLM Inspector' },
  { path: '/atm-inspector', icon: Zap, label: 'ATM Inspector' },
  { path: '/IntentionSelection', icon: GitBranch, label: 'Intention' },
  { path: '/data', icon: Database, label: 'Data' },
  { path: '/schedule', icon: Calendar, label: 'Schedule' },
  { path: '/config', icon: Settings2, label: 'Config' },
];

export default function Layout() {
  return (
    <div className="flex h-screen min-h-0 w-full bg-slate-50 text-slate-900">
      {/* Sidebar Navigation */}
      <nav className="flex h-full min-h-0 w-64 flex-col overflow-hidden border-r border-slate-200 bg-white p-4">
        <div className="mb-8 flex shrink-0 items-center space-x-3 px-2">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-visible">
            <img
              src="/selena-iris.png"
              alt=""
              aria-hidden="true"
              className="h-11 w-11 max-w-none select-none object-contain"
              draggable={false}
            />
          </div>
          <span className="text-xl font-bold text-slate-800">Selena</span>
        </div>

        <div className="flex min-h-0 flex-1 flex-col space-y-2 overflow-y-scroll pr-1 [scrollbar-gutter:stable]">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                clsx(
                  'flex items-center space-x-3 px-3 py-2 rounded-lg transition-colors',
                  isActive
                    ? 'bg-blue-50 text-blue-700 font-medium'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                )
              }
            >
              <item.icon className="w-5 h-5" />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}

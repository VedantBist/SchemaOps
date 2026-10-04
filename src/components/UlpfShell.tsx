import React from 'react';
import { ulpf } from '../api/ulpf';
import { useApi } from '../hooks/useApi';
import { useAuth } from '../context/AppContext';
import { NAV } from './AppShell';
import { Badge, fmtNum } from './ui';
import { LOG_PRODUCT_NAME } from '../config/uiMode';

/** The original AppShell stays intact and is not mounted in ULPF demo mode. */
export const UlpfShell: React.FC<{
  page: string; navigate: (path: string) => void; title: string; children: React.ReactNode;
}> = ({ page, navigate, title, children }) => {
  const stats = useApi(() => ulpf.stats(), [], 10000);
  const { user, authDisabled, logout } = useAuth();
  const data = stats.error ? null : stats.data;
  const items = NAV.flatMap((section) => section.items).filter((item) => item.page.startsWith('log-'));

  return (
    <div className="min-h-screen bg-[#F7F7F5] text-[#171A19] flex font-sans">
      <aside className="fixed left-0 top-0 bottom-0 w-[224px] bg-[#F7F7F5] border-r border-[#D9DCD8] z-50 flex flex-col">
        <button className="h-14 px-4 border-b border-[#D9DCD8] flex flex-col justify-center text-left shrink-0"
                onClick={() => navigate('/log-sources')}>
          <span className="font-bold text-[14px] tracking-wider">{LOG_PRODUCT_NAME}</span>
          <span className="text-[10px] text-[#858C87]">Universal Log Pre-processing Framework</span>
        </button>
        <nav aria-label="Log pipeline" className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
          <div className="px-2 py-1 text-[10px] text-[#858C87] font-semibold uppercase">Log pipeline</div>
          {items.map((item) => (
            <button key={item.page} onClick={() => navigate(`/${item.page}`)}
                    aria-current={page === item.page ? 'page' : undefined}
                    className={`w-full h-8 px-2 rounded-[3px] text-[12px] text-left transition-colors ${
                      page === item.page ? 'bg-[#E9EDE9] text-[#286B78] font-semibold border-l-2 border-[#286B78]'
                        : 'text-[#5E6561] hover:bg-[#EAECE8] hover:text-[#171A19]'}`}>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="p-3 border-t border-[#D9DCD8] bg-[#F1F2F0] space-y-2 text-[11px]">
          <div className="font-semibold text-[#5E6561]">Log intake</div>
          {data ? <>
            <div className="flex justify-between"><span>Sources</span><span>{fmtNum(data.sources)}</span></div>
            <div className="flex justify-between"><span>Workers alive</span><span>{data.workers.filter((w) => w.alive).length} / {data.workers.length}</span></div>
            <div className="flex justify-between"><span>In flight</span><span>{fmtNum(data.conservation.inFlight)}</span></div>
            <div className="flex justify-between"><span>Event accounting</span><Badge tone={data.conservation.balanced ? 'good' : 'bad'}>{data.conservation.balanced ? 'Balanced' : 'Unbalanced'}</Badge></div>
          </> : <div role="status" className="text-[#858C87]">{stats.error ? 'Intake status unavailable' : 'Loading intake status…'}</div>}
        </div>
      </aside>
      <div className="pl-[224px] flex-1 flex flex-col min-w-0">
        <header className="fixed top-0 left-[224px] right-0 h-14 bg-white border-b border-[#D9DCD8] z-40 flex items-center justify-between px-4 gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-[11px] tracking-wider text-[#858C87] font-semibold">{LOG_PRODUCT_NAME}</span>
            <span className="text-[#D9DCD8]">/</span>
            <span className="font-semibold text-[13px] truncate">{title}</span>
          </div>
          {user && !authDisabled && <button onClick={logout} className="text-[11px] text-[#286B78]">Sign out</button>}
        </header>
        <main className="pt-14 flex-1 flex flex-col">{children}</main>
      </div>
    </div>
  );
};

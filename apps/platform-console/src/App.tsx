import { NavLink, Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api, auth } from './api';
import { SignIn, useAdmin, useCan, usePhase } from './auth';
import { label } from './format';
import { AuditPage } from './pages/Audit';
import { BillingPage } from './pages/Billing';
import { DashboardPage } from './pages/Dashboard';
import { ModulesPage } from './pages/Modules';
import { PlansPage } from './pages/Plans';
import { SecurityPage } from './pages/Security';
import { SubscriberDetailPage } from './pages/SubscriberDetail';
import { SubscribersPage } from './pages/Subscribers';
import { TeamPage } from './pages/Team';
import { Loading } from './ui';

function Layout() {
  const admin = useAdmin()!;
  const can = useCan();
  const summary = useQuery({ queryKey: ['security-summary'], queryFn: api.alertSummary, enabled: can.readAudit, refetchInterval: 60_000 });
  const badge = (summary.data?.pending_approvals ?? 0) + (summary.data?.open_alerts.critical ?? 0);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Platform Console<small>Restaurant ERP</small></div>
        <nav className="nav" aria-label="Main">
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/subscribers">Subscribers</NavLink>
          <NavLink to="/billing">Billing</NavLink>
          <NavLink to="/plans">Plans</NavLink>
          <NavLink to="/modules">Modules</NavLink>
          <h3 className="group">Security</h3>
          {can.readAudit && <NavLink to="/security">Approvals & alerts{badge > 0 && <span className="badge bad" style={{ marginLeft: 6 }}>{badge}</span>}</NavLink>}
          {can.readAudit && <NavLink to="/audit">Audit log</NavLink>}
          <NavLink to="/team">{can.superAdmin ? 'Team & sessions' : 'My sessions'}</NavLink>
        </nav>
        <div className="me">
          <div><strong>{admin.full_name}</strong></div>
          <div className="muted">{label(admin.role)}</div>
          <button className="btn small" style={{ marginTop: 8 }} onClick={() => void auth.logout()}>Sign out</button>
        </div>
      </aside>
      <main className="main"><Outlet /></main>
    </div>
  );
}

export function App() {
  const phase = usePhase();
  const admin = useAdmin();
  const can = useCan();
  if (phase === 'resuming') return <Loading />;
  if (!admin) return <SignIn />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="subscribers" element={<SubscribersPage />} />
        <Route path="subscribers/:id" element={<SubscriberDetailPage />} />
        <Route path="billing" element={<BillingPage />} />
        <Route path="plans" element={<PlansPage />} />
        <Route path="modules" element={<ModulesPage />} />
        <Route path="security" element={can.readAudit ? <SecurityPage /> : <Navigate to="/" replace />} />
        <Route path="audit" element={can.readAudit ? <AuditPage /> : <Navigate to="/" replace />} />
        <Route path="team" element={<TeamPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

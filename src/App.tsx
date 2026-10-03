import React from 'react';
import { AuthProvider, EnvironmentProvider, useAuth, useEnv, useRoute } from './context/AppContext';
import { AppShell, NAV } from './components/AppShell';
import { Empty, Loading, Page, Panel, Button } from './components/ui';
import { Overview } from './pages/Overview';
import { Services, Topology } from './pages/Services';
import { IncidentDetail, IncidentList } from './pages/Incidents';
import { Remediation } from './pages/Remediation';
import { Calibration, Predictions, Simulation } from './pages/Intelligence';
import { Logs, Metrics, Traces } from './pages/Observability';
import { Changes, FaultLab } from './pages/Operations';
import { Setup } from './pages/Setup';
import { EventExplorer, LogSources } from './pages/LogPipeline';
import { Entities, PackList, ParserStudio } from './pages/LogStudio';
import { ChangePassword, Login, Users } from './pages/Auth';

/** One failing page must not blank the whole console; the boundary resets on navigation. */
class PageBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return <Page title="This page failed to render"><Panel><Empty title={this.state.error.message}>
        The rest of the console keeps working; reload or pick another page.</Empty></Panel></Page>;
    }
    return this.props.children;
  }
}

const TITLES: Record<string, string> = Object.fromEntries(NAV.flatMap((s) => s.items.map((i) => [i.page, i.label])));

const Routes: React.FC = () => {
  const [route, navigate] = useRoute();
  const { environments, loaded, error } = useEnv();
  const { can } = useAuth();
  const { page, id, query } = route;

  if (loaded && !error && environments.length === 0 && page !== 'setup') {
    return (
      <Page title="Welcome to CausalOps">
        <Panel><Empty title="No environment yet">Connect your first system: CausalOps reads its OpenTelemetry data, discovers the topology and starts learning.</Empty>
          {can('ADMIN') && <div className="text-center"><Button variant="primary" onClick={() => navigate('/setup')}>Open the setup wizard</Button></div>}</Panel>
      </Page>
    );
  }
  switch (page) {
    case 'overview': return <Overview navigate={navigate} />;
    case 'topology': return <Topology navigate={navigate} />;
    case 'services': return <Services navigate={navigate} id={id} />;
    case 'incidents': return id ? <IncidentDetail id={id} navigate={navigate} /> : <IncidentList navigate={navigate} state="active" />;
    case 'history': return <IncidentList navigate={navigate} state="resolved" />;
    case 'remediation': return <Remediation navigate={navigate} />;
    case 'predictions': return <Predictions />;
    case 'simulation': return <Simulation />;
    case 'calibration': return <Calibration />;
    case 'metrics': return <Metrics />;
    case 'logs': return <Logs query={query} />;
    case 'traces': return <Traces id={id} />;
    case 'faults': return <FaultLab />;
    case 'changes': return <Changes />;
    case 'setup': return <Setup navigate={navigate} />;
    case 'users': return <Users />;
    case 'log-sources': return <LogSources navigate={navigate} />;
    case 'log-events': return <EventExplorer navigate={navigate} id={id} query={query} />;
    case 'log-entities': return <Entities navigate={navigate} query={query} />;
    case 'log-studio': return <ParserStudio navigate={navigate} query={query} />;
    case 'log-packs': return <PackList navigate={navigate} />;
    default: return <Page title="Not found"><Empty title={`No page "${page}"`} /></Page>;
  }
};

const Authenticated: React.FC = () => {
  const { user, checking } = useAuth();
  const [route, navigate] = useRoute();
  if (checking) return <Loading label="Checking session…" />;
  if (!user) return <Login />;
  if (user.mustChangePassword) return <ChangePassword forced />;
  const title = route.page === 'incidents' && route.id ? 'Incident' : route.page === 'log-events' && route.id ? 'Event lineage' : TITLES[route.page] ?? 'CausalOps';
  return (
    <EnvironmentProvider>
      <AppShell page={route.page} navigate={navigate} title={title}>
        <PageBoundary key={`${route.page}/${route.id ?? ''}`}><Routes /></PageBoundary>
      </AppShell>
    </EnvironmentProvider>
  );
};

export default function App() {
  return (
    <AuthProvider>
      <Authenticated />
    </AuthProvider>
  );
}

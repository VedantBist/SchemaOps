import React, { useState } from 'react';
import { OCSF_CLASSES, ulpf, type LosslessProof, type UlpfEventDetail, type VaultVerify } from '../api/ulpf';
import { useApi } from '../hooks/useApi';
import {
  Async, Badge, Button, Empty, Field, KeyValue, Page, Panel, Stat, Table, Td, ago, fmtNum, fmtTime, inputClass,
} from '../components/ui';

type Navigate = (path: string) => void;

const statusTone = (s: string) => (s === 'NORMALIZED' ? 'good' : s === 'PARTIAL' ? 'warn' : 'bad');
const bytes = (n: number) => (n > 2 ** 30 ? `${(n / 2 ** 30).toFixed(2)} GB` : n > 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB` : `${(n / 1024).toFixed(1)} KB`);

/** Pipeline-wide numbers: intake, storage, losslessness and the conservation check. */
const PipelineStats: React.FC = () => {
  const stats = useApi(() => ulpf.stats(), [], 5000);
  return (
    <Async state={stats}>{(s) => (
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
        <Stat label="Received" value={s.received.toLocaleString()} hint={Object.entries(s.receivedByTransport).map(([k, v]) => `${k} ${v.toLocaleString()}`).join(' · ') || 'no intake yet'} />
        <Stat label="Stored" value={s.stored.toLocaleString()} hint={`${s.eventsPerSecond}/s last minute`} />
        <Stat label="In flight" value={s.backlog.toLocaleString()} hint="queued in the stream" tone={s.backlog > 5000 ? 'warn' : undefined} />
        <Stat label="Conservation" value={s.conservation.balanced ? 'balanced' : `${s.conservation.unaccounted} off`}
              hint="received = stored + in flight" tone={s.conservation.balanced ? 'good' : 'warn'} />
        <Stat label="Normalized" value={`${fmtNum(100 * s.normalized / Math.max(s.stored, 1), 1)}%`} hint={`${s.partial.toLocaleString()} partial · ${s.quarantined.toLocaleString()} quarantined`} />
        <Stat label="Lossless proven" value={`${fmtNum(100 * s.losslessVerified / Math.max(s.stored, 1), 2)}%`} hint="rebuilt from OCSF = vault SHA-256" tone={s.losslessVerified === s.stored ? 'good' : 'warn'} />
        <Stat label="Sources" value={s.sources} hint={bytes(s.bytes) + ' raw'} />
        <Stat label="Workers" value={`${s.workers.filter((w) => w.alive).length} / ${s.workers.length}`}
              hint={s.workers.map((w) => `${w.name.slice(0, 8)} ${w.alive ? 'up' : 'down'}`).join(' · ')}
              tone={s.workers.some((w) => !w.alive) ? 'bad' : 'good'} />
      </div>
    )}</Async>
  );
};

export const LogSources: React.FC<{ navigate: Navigate }> = ({ navigate }) => {
  const sources = useApi(() => ulpf.sources(), [], 10000);
  return (
    <Page title="Log sources" subtitle="Every device sending logs, discovered automatically from intake (syslog UDP/TCP/TLS, HTTP).">
      <PipelineStats />
      <VaultPanel />
      <Panel title="Sources" dense>
        <Async state={sources} empty={(d) => (d.length ? null : <Empty title="No logs received yet">Send syslog to port 5514 (UDP/TCP) or 6514 (TLS), or POST lines to /api/ulpf/ingest.</Empty>)}>
          {(rows) => (
            <Table head={['Source', 'Vendor / product', 'Format', 'Pack', 'Transport', 'Events', 'Normalized', 'Fill', 'Lossless', 'Raw bytes', 'Last event', '']}>
              {rows.map((r) => (
                <tr key={r.id} className="hover:bg-[#F7F8F6]">
                  <Td mono>{r.id}</Td>
                  <Td>{[r.vendor, r.product].filter(Boolean).join(' · ') || '—'}</Td>
                  <Td mono>{r.format}</Td>
                  <Td mono>{r.pack_id ?? <Button variant="ghost" onClick={() => navigate(`/log-studio?source=${encodeURIComponent(r.id)}`)}>Onboard →</Button>}</Td>
                  <Td>{r.transport ?? '—'}</Td>
                  <Td mono>{r.events.toLocaleString()}</Td>
                  <Td><Badge tone={r.normalizedPct >= 99 ? 'good' : r.normalizedPct >= 80 ? 'warn' : 'bad'}>{fmtNum(r.normalizedPct, 1)}%</Badge></Td>
                  <Td>{r.fillPct == null ? '—' : <Badge tone={r.fillPct >= 99 ? 'good' : r.fillPct >= 80 ? 'warn' : 'bad'}>{fmtNum(r.fillPct, 1)}%</Badge>}</Td>
                  <Td><Badge tone={r.losslessPct >= 100 ? 'good' : 'bad'}>{fmtNum(r.losslessPct, 2)}%</Badge></Td>
                  <Td mono>{bytes(r.bytes)}</Td>
                  <Td>{ago(r.last_seen)}</Td>
                  <Td><Button variant="ghost" onClick={() => navigate(`/log-events?source=${encodeURIComponent(r.id)}`)}>Events</Button></Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

export const EventExplorer: React.FC<{ navigate: Navigate; id?: string; query?: URLSearchParams }> = ({ navigate, id, query }) => {
  if (id) return <EventLineage uid={id} navigate={navigate} />;
  return <EventSearch navigate={navigate} query={query} />;
};

const EventSearch: React.FC<{ navigate: Navigate; query?: URLSearchParams }> = ({ navigate, query }) => {
  const sources = useApi(() => ulpf.sources(), []);
  const [form, setForm] = useState({
    source: query?.get('source') ?? '', classUid: query?.get('classUid') ?? '', status: query?.get('status') ?? '',
    ip: query?.get('ip') ?? '', user: query?.get('user') ?? '', q: '',
  });
  const [submitted, setSubmitted] = useState(form);
  const events = useApi(() => ulpf.events({
    source: submitted.source || undefined, classUid: submitted.classUid === '' ? undefined : Number(submitted.classUid),
    status: submitted.status || undefined, ip: submitted.ip || undefined, user: submitted.user || undefined,
    q: submitted.q || undefined, limit: 200,
  }), [JSON.stringify(submitted)], 5000);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <Page title="Event explorer" subtitle="Normalized OCSF events. Open any event to see its raw bytes, its OCSF record and the lossless proof.">
      <PipelineStats />
      <Panel>
        <form className="grid grid-cols-2 md:grid-cols-7 gap-2 items-end" onSubmit={(e) => { e.preventDefault(); setSubmitted(form); }}>
          <Field label="Source">
            <select className={`${inputClass} w-full`} value={form.source} onChange={set('source')}>
              <option value="">all</option>
              {(sources.data ?? []).map((s) => <option key={s.id}>{s.id}</option>)}
            </select>
          </Field>
          <Field label="OCSF class">
            <select className={`${inputClass} w-full`} value={form.classUid} onChange={set('classUid')}>
              <option value="">all</option>
              {Object.entries(OCSF_CLASSES).map(([k, v]) => <option key={k} value={k}>{k} {v}</option>)}
            </select>
          </Field>
          <Field label="Status">
            <select className={`${inputClass} w-full`} value={form.status} onChange={set('status')}>
              <option value="">all</option>
              {['NORMALIZED', 'PARTIAL', 'QUARANTINED'].map((s) => <option key={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="IP (src or dst)"><input className={`${inputClass} w-full`} value={form.ip} onChange={set('ip')} placeholder="10.20.1.11" /></Field>
          <Field label="User"><input className={`${inputClass} w-full`} value={form.user} onChange={set('user')} /></Field>
          <Field label="Message contains"><input className={`${inputClass} w-full`} value={form.q} onChange={set('q')} /></Field>
          <Button variant="primary" type="submit">Search</Button>
        </form>
      </Panel>
      <Panel title="Events (newest first)" dense>
        <Async state={events} empty={(d) => (d.length ? null : <Empty title="No events match" />)}>
          {(rows) => (
            <Table head={['Received', 'Source', 'OCSF class', 'Status', 'Source IP', 'Destination IP', 'User', 'Action', 'Lossless', 'Format']}>
              {rows.map((r) => (
                <tr key={r.uid} className="hover:bg-[#F7F8F6] cursor-pointer" onClick={() => navigate(`/log-events/${encodeURIComponent(r.uid)}`)}>
                  <Td mono>{fmtTime(r.received_at)}</Td>
                  <Td mono>{r.source_id}</Td>
                  <Td>{r.class_uid} {r.class_name}</Td>
                  <Td><Badge tone={statusTone(r.status)}>{r.status}</Badge></Td>
                  <Td mono>{r.src_ip ?? '—'}</Td>
                  <Td mono>{r.dst_ip ?? '—'}</Td>
                  <Td>{r.user_name ?? '—'}</Td>
                  <Td>{r.action ?? '—'}</Td>
                  <Td>{r.lossless ? <Badge tone="good">yes</Badge> : <Badge tone="bad">no</Badge>}</Td>
                  <Td mono>{r.format}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Panel>
    </Page>
  );
};

/** Raw bytes ↔ OCSF record, with every extracted field highlighted and the lossless proof. */
const EventLineage: React.FC<{ uid: string; navigate: Navigate }> = ({ uid, navigate }) => {
  const detail = useApi(() => ulpf.event(uid), [uid]);
  const [proof, setProof] = useState<LosslessProof | null>(null);
  const [error, setError] = useState<string | null>(null);
  const verify = () => { setError(null); ulpf.verify(uid).then(setProof).catch((e) => setError(e.message)); };
  return (
    <Page title={`Event ${uid}`} subtitle="Lineage: the vault record, the fields extracted from it, and the normalized OCSF event."
          actions={<><Button onClick={() => navigate('/log-events')}>Back</Button><Button variant="primary" onClick={verify}>Verify lossless</Button></>}>
      <Async state={detail}>{(d) => (
        <>
          {(proof || error) && (
            <Panel title="Lossless proof">
              {error ? <div className="text-[#B83A3A] text-[12px]">{error}</div> : proof && (
                <KeyValue items={[
                  ['Result', proof.lossless ? <Badge tone="good">LOSSLESS: rebuilt event is byte-identical</Badge> : <Badge tone="bad">NOT PROVEN</Badge>],
                  ['Vault SHA-256', proof.sha256],
                  ['Rebuilt from OCSF', proof.rebuiltSha256 ?? 'could not rebuild'],
                  ['Vault bytes match hash', proof.checks.vaultHashMatches ? 'yes' : 'no'],
                  ['Vault record is this event (uid + chain)', proof.checks.vaultRecordIsThisEvent ? 'yes' : 'no'],
                  ['Rebuilt from normalized record matches', proof.checks.rebuiltFromNormalizedMatches ? 'yes' : 'no'],
                ]} />
              )}
            </Panel>
          )}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <Panel title="Raw event (from the vault)">
              <RawHighlighted detail={d} />
              <div className="mt-3">
                <KeyValue items={[
                  ['Source', d.sourceId], ['Received', d.receivedAt], ['Vault segment', d.vault.segment],
                  ['Offset', String(d.vault.offset)], ['SHA-256', d.vault.sha256], ['Chain hash', d.vault.chain],
                ]} />
              </div>
            </Panel>
            <Panel title="Normalized OCSF event">
              <pre className="font-code text-[11px] leading-[1.45] bg-[#F7F8F6] border border-[#E6E8E4] rounded-[3px] p-2 overflow-auto max-h-[620px]">
                {JSON.stringify(stripInternals(d.event), null, 2)}
              </pre>
            </Panel>
          </div>
          <Panel title="Field lineage (where every extracted value lives in the OCSF record)" dense>
            <Table head={['Field', 'OCSF location', 'Value']}>
              {Object.entries(d.event.ulpf.fields).map(([name, loc]) => (
                <tr key={name}>
                  <Td mono>{name}</Td>
                  <Td mono>{loc.startsWith('unmapped:') ? <span className="text-[#9A6412]">unmapped.{loc.slice(9)}</span> : loc}</Td>
                  <Td mono>{valueAt(d.event, loc)}</Td>
                </tr>
              ))}
            </Table>
          </Panel>
        </>
      )}</Async>
    </Page>
  );
};

const RawHighlighted: React.FC<{ detail: UlpfEventDetail }> = ({ detail }) => {
  const parts = detail.event.ulpf.skeleton;
  return (
    <div className="font-code text-[11.5px] leading-[1.6] bg-[#F7F8F6] border border-[#E6E8E4] rounded-[3px] p-2 break-all whitespace-pre-wrap">
      {parts.map((p, i) => typeof p === 'string'
        ? <span key={i} className="text-[#858C87]">{p}</span>
        : <span key={i} title={p[0]} className="bg-[#DCEBEE] text-[#1F4F59] rounded-[2px]">{valueAt(detail.event, detail.event.ulpf.fields[p[0]])}</span>)}
    </div>
  );
};

function valueAt(event: Record<string, unknown>, loc: string | undefined): string {
  if (!loc) return '';
  if (loc.startsWith('unmapped:')) return String((event.unmapped as Record<string, unknown> | undefined)?.[loc.slice(9)] ?? '');
  let cur: unknown = event;
  for (const part of loc.split('.')) cur = (cur as Record<string, unknown> | undefined)?.[part];
  return cur === undefined || cur === null ? '' : String(cur);
}

function stripInternals(event: Record<string, unknown>) {
  const { ulpf: meta, ...rest } = event as Record<string, unknown> & { ulpf: Record<string, unknown> };
  const { skeleton: _s, fields: _f, ...lineage } = meta;
  return { ...rest, ulpf: lineage };
}

export const VaultPanel: React.FC = () => {
  const [result, setResult] = useState<VaultVerify | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Panel title="Raw vault integrity" actions={<Button disabled={busy} onClick={() => { setBusy(true); ulpf.verifyVault().then(setResult).finally(() => setBusy(false)); }}>{busy ? 'Verifying…' : 'Verify whole vault'}</Button>}>
      {result ? (
        <KeyValue items={[
          ['Result', result.ok ? <Badge tone="good">every hash and chain link verified</Badge> : <Badge tone="bad">tampering or corruption detected</Badge>],
          ['Records checked', result.records.toLocaleString()], ['Time', `${result.seconds} s`],
          ...result.writers.map((w) => [`Writer ${w.writer}`, `${w.segments} segments, ${w.records.toLocaleString()} records ${w.ok ? 'OK' : w.errors.join('; ')}`] as [string, string]),
        ]} />
      ) : <div className="text-[12px] text-[#5E6561]">Recomputes the SHA-256 of every stored raw event and the hash chain across all segments.</div>}
    </Panel>
  );
};

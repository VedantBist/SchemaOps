import React, { useState, useEffect, useCallback, useRef } from 'react';
import { AppPage } from '../components/layout/AppShell';
import {
  causalOpsApi,
  CausalCounterfactualResponse,
  SimulationTimeFrame,
} from '../api/client';
import { useDemoState } from '../context/DemoStateContext';

export type SimulationState =
  | 'IDLE'
  | 'RUNNING'
  | 'READY'
  | 'PLAYING'
  | 'PAUSED'
  | 'COMPLETED'
  | 'ERROR'
  | 'STALE';

export interface CandidateScenario {
  id: string;
  label: string;
  description: string;
  modeledComponent: string;
  interventionMagnitude?: number;
  riskLabel: string;
}

export const CANONICAL_INTERVENTIONS: CandidateScenario[] = [
  {
    id: 'nominal-restoration',
    label: 'Nominal Restor.',
    description: 'Restore root cause to nominal healthy baseline (do(root_cause = nominal))',
    modeledComponent: 'inventory-db',
    riskLabel: 'Low (Ideal SCM Target)',
  },
  {
    id: 'reduce-latency-70',
    label: '-70% Latency',
    description: 'Reduce query latency by 70% via terminating blocking lock holder',
    modeledComponent: 'inventory-db',
    interventionMagnitude: 0.7,
    riskLabel: 'Low (Safe Intervention)',
  },
  {
    id: 'reduce-latency-50',
    label: '-50% Latency',
    description: 'Conservative 50% latency attenuation via query kill & connection throttle',
    modeledComponent: 'inventory-db',
    interventionMagnitude: 0.5,
    riskLabel: 'Low (Conservative)',
  },
  {
    id: 'reduce-latency-90',
    label: '-90% Latency',
    description: 'Aggressive 90% latency reduction via instant transaction termination',
    modeledComponent: 'inventory-db',
    interventionMagnitude: 0.9,
    riskLabel: 'Medium (Transaction Aborts)',
  },
  {
    id: 'expand-pool-100',
    label: '+100 Pool',
    description: 'Expand connection pool ceiling from 200 to 300 leases',
    modeledComponent: 'inventory-service',
    interventionMagnitude: 0.6,
    riskLabel: 'Medium (RAM Overhead)',
  },
];

interface SimulationViewProps {
  onNavigate: (page: AppPage) => void;
}

export const SimulationView: React.FC<SimulationViewProps> = ({ onNavigate }) => {
  const { currentIncident } = useDemoState();

  const [simState, setSimState] = useState<SimulationState>('IDLE');
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>('reduce-latency-70');
  const [currentTimeSec, setCurrentTimeSec] = useState<number>(0);
  const [playbackSpeed, setPlaybackSpeed] = useState<number>(1.0);
  const [simulationData, setSimulationData] = useState<CausalCounterfactualResponse | null>(null);
  const [simulatedScenarioId, setSimulatedScenarioId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [selectedMetric, setSelectedMetric] = useState<'gateway_latency' | 'root_cause'>('gateway_latency');
  const [engineHealth, setEngineHealth] = useState<'HEALTHY' | 'UNAVAILABLE' | 'CHECKING'>('CHECKING');

  useEffect(() => {
    let active = true;
    causalOpsApi
      .aiHealth()
      .then((res) => {
        if (active) {
          setEngineHealth(res.counterfactual_engine === 'HEALTHY' ? 'HEALTHY' : 'UNAVAILABLE');
        }
      })
      .catch(() => {
        if (active) setEngineHealth('UNAVAILABLE');
      });
    return () => {
      active = false;
    };
  }, []);

  const activeScenario =
    CANONICAL_INTERVENTIONS.find((s) => s.id === selectedScenarioId) || CANONICAL_INTERVENTIONS[1];

  const rootTarget = currentIncident?.rootCauseCandidate || 'inventory-db';

  // Core Simulation Trigger Function
  const handleRunSimulation = useCallback(
    async (scenarioOverride?: CandidateScenario) => {
      const targetScenario = scenarioOverride || activeScenario;
      if (scenarioOverride) {
        setSelectedScenarioId(scenarioOverride.id);
      }

      setSimState('RUNNING');
      setErrorMessage(null);

      try {
        const response = await causalOpsApi.runCounterfactualSimulation({
          incident_id: currentIncident?.id || 'EXP-015',
          root_cause: rootTarget,
          intervention: targetScenario.id,
          intervention_magnitude: targetScenario.interventionMagnitude,
          simulation_resolution: 1.0,
        });

        setSimulationData(response);
        setSimulatedScenarioId(targetScenario.id);
        setCurrentTimeSec(0);
        setSimState('READY');
      } catch (err: unknown) {
        setSimulationData(null); // Condition 10: failed request does not display stale data
        const msg = err instanceof Error ? err.message : 'Counterfactual simulation failed';
        setErrorMessage(msg);
        setSimState('ERROR');
      }
    },
    [activeScenario, currentIncident, rootTarget]
  );

  // Mark simulation as STALE when user switches intervention scenario
  const handleSelectScenario = (scenarioId: string) => {
    setSelectedScenarioId(scenarioId);
    if (simulationData) {
      if (scenarioId !== simulatedScenarioId) {
        setSimState('STALE');
      } else {
        setSimState('READY');
      }
    }
  };

  // Playback timer loop
  const totalHorizon = simulationData?.total_horizon_seconds ?? 40;
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (simState === 'PLAYING') {
      const stepMs = Math.max(100, Math.round(500 / playbackSpeed));
      timerRef.current = setInterval(() => {
        setCurrentTimeSec((prev) => {
          if (prev >= totalHorizon - 1) {
            setSimState('COMPLETED');
            return totalHorizon - 1;
          }
          return prev + 1;
        });
      }, stepMs);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [simState, playbackSpeed, totalHorizon]);

  // Play / Pause toggle
  const togglePlayPause = () => {
    if (simState === 'IDLE' || simState === 'RUNNING' || simState === 'ERROR') return;
    if (simState === 'PLAYING') {
      setSimState('PAUSED');
    } else if (simState === 'COMPLETED') {
      setCurrentTimeSec(0);
      setSimState('PLAYING');
    } else {
      // READY, PAUSED, STALE
      if (currentTimeSec >= totalHorizon - 1) {
        setCurrentTimeSec(0);
      }
      setSimState('PLAYING');
    }
  };

  // Reset to T0
  const handleReset = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setCurrentTimeSec(0);
    if (simState === 'PLAYING' || simState === 'COMPLETED') {
      setSimState('READY');
    }
  };

  // Scrubber change
  const handleScrubberChange = (val: number) => {
    setCurrentTimeSec(val);
    if (simState === 'COMPLETED') {
      setSimState('PAUSED');
    }
  };

  // Safe frame extraction
  const currentFrame: SimulationTimeFrame | null =
    simulationData?.timeline?.[currentTimeSec] ?? simulationData?.timeline?.[0] ?? null;

  // Metric series coordinates computation for SVG
  const viz = simulationData?.visualization_data;
  const seriesObs =
    selectedMetric === 'gateway_latency'
      ? viz?.gateway_latency_series?.observed ?? []
      : viz?.root_cause_series?.observed ?? [];
  const seriesCf =
    selectedMetric === 'gateway_latency'
      ? viz?.gateway_latency_series?.counterfactual ?? []
      : viz?.root_cause_series?.counterfactual ?? [];

  const allVals = [...seriesObs, ...seriesCf];
  const rawMin = allVals.length > 0 ? Math.min(...allVals) : 0;
  const rawMax = allVals.length > 0 ? Math.max(...allVals) : 100;
  const valMin = Math.max(0, Math.floor(rawMin * 0.9));
  const valMax = Math.max(valMin + 10, Math.ceil(rawMax * 1.15));

  const svgWidth = 880;
  const svgHeight = 280;
  const svgLeft = 70;
  const svgTop = 50;
  const numSteps = Math.max(1, (simulationData?.total_horizon_seconds ?? 40) - 1);

  const generateSvgPath = (points: number[]) => {
    if (!points || points.length === 0) return '';
    const range = valMax - valMin || 1;
    return points
      .map((val, i) => {
        const x = svgLeft + (i / numSteps) * svgWidth;
        const y = svgTop + svgHeight - ((val - valMin) / range) * svgHeight;
        return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  };

  const generateRibbonPoints = (points: number[]) => {
    if (!points || points.length === 0) return '';
    const range = valMax - valMin || 1;
    const topPoints: string[] = [];
    const bottomPoints: string[] = [];

    for (let i = 0; i < points.length; i++) {
      const x = svgLeft + (i / numSteps) * svgWidth;
      const upper = Math.min(valMax, points[i] + Math.max(4, points[i] * 0.1));
      const lower = Math.max(valMin, points[i] - Math.max(4, points[i] * 0.1));
      const yUpper = svgTop + svgHeight - ((upper - valMin) / range) * svgHeight;
      const yLower = svgTop + svgHeight - ((lower - valMin) / range) * svgHeight;
      topPoints.push(`${x.toFixed(1)},${yUpper.toFixed(1)}`);
      bottomPoints.unshift(`${x.toFixed(1)},${yLower.toFixed(1)}`);
    }
    return [...topPoints, ...bottomPoints].join(' ');
  };

  const baselinePath = generateSvgPath(seriesObs);
  const counterfactualPath = generateSvgPath(seriesCf);
  const ribbonPoints = generateRibbonPoints(seriesCf);

  const startStep = simulationData?.intervention_metadata?.intervention_start_step ?? 5;
  const interventionMarkerX = svgLeft + (startStep / numSteps) * svgWidth;
  const playheadX = svgLeft + (currentTimeSec / numSteps) * svgWidth;

  // Current metric values at playhead
  const currentObsVal =
    selectedMetric === 'gateway_latency'
      ? currentFrame?.gateway_latency_observed ?? 0
      : currentFrame?.root_cause_observed ?? 0;
  const currentCfVal =
    selectedMetric === 'gateway_latency'
      ? currentFrame?.gateway_latency_counterfactual ?? 0
      : currentFrame?.root_cause_counterfactual ?? 0;
  const currentDeltaVal = currentCfVal - currentObsVal;

  // Services state counting
  const servicesList = currentFrame?.services ? Object.entries(currentFrame.services) : [];
  const operationalCount = servicesList.filter(([, s]) => s.state === 'OPERATIONAL').length;
  const totalServiceCount = servicesList.length || 5;

  // Convergence calculation
  let convergenceStep: number | null = null;
  if (simulationData?.timeline) {
    for (let t = startStep; t < simulationData.timeline.length; t++) {
      const f = simulationData.timeline[t];
      const allOk = Object.values(f.services).every((s) => s.state === 'OPERATIONAL');
      if (allOk) {
        convergenceStep = t;
        break;
      }
    }
  }

  // Peak impact calculation
  const peakObsLatency = seriesObs.length ? Math.max(...seriesObs) : 0;
  const peakCfLatency = seriesCf.length ? Math.max(...seriesCf) : 0;
  const peakLatencyReductionPct =
    peakObsLatency > 0 ? (((peakObsLatency - peakCfLatency) / peakObsLatency) * 100).toFixed(1) : '0';

  return (
    <div className="flex flex-col w-full font-sans text-[#171A19] p-4 bg-[#F7F7F5] select-text">
      {/* 1. WORKSPACE SUB-HEADER / INCIDENT & INTERVENTION SELECTOR STRIP */}
      <section className="bg-white p-3 mb-3 shadow-xs border border-[#D9DCD8] rounded-[3px]">
        <div className="flex flex-col xl:flex-row xl:items-center justify-between gap-3">
          {/* Incident Identification & Telemetry Context */}
          <div className="flex flex-col gap-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-code text-[11px] px-2 py-0.5 rounded-[2px] bg-[#EC4899]/10 text-[#EC4899] font-semibold border border-[#EC4899]/30 uppercase tracking-wider">
                SIMULATE · COUNTERFACTUAL SCM ENGINE
              </span>
              <h1 className="text-[15px] font-bold text-[#171A19] tracking-tight">
                Counterfactual Simulation
              </h1>
              <span className="font-code text-[10px] text-[#5E6561] font-medium px-1.5 py-0.5 bg-[#F1F2F0] rounded-[2px]">
                TARGET: {currentIncident?.id || 'EXP-015'} ({rootTarget})
              </span>
              {/* Visual State Machine Badge */}
              <span
                className={`font-code text-[10px] font-semibold px-2 py-0.5 rounded-[2px] border ${
                  simState === 'IDLE'
                    ? 'bg-[#EAECE8] text-[#5E6561] border-[#D9DCD8]'
                    : simState === 'RUNNING'
                      ? 'bg-[#00535f]/15 text-[#00535f] border-[#00535f]/30 animate-pulse'
                      : simState === 'READY'
                        ? 'bg-[#2E7D32]/10 text-[#2E7D32] border-[#2E7D32]/30'
                        : simState === 'PLAYING'
                          ? 'bg-[#00535f] text-white border-[#00535f]'
                          : simState === 'PAUSED'
                            ? 'bg-[#F1F2F0] text-[#171A19] border-[#D9DCD8]'
                            : simState === 'COMPLETED'
                              ? 'bg-[#1565C0]/10 text-[#1565C0] border-[#1565C0]/30'
                              : simState === 'STALE'
                                ? 'bg-[#EF6C00]/15 text-[#EF6C00] border-[#EF6C00]/40 font-bold'
                                : 'bg-[#B83A3A]/15 text-[#B83A3A] border-[#B83A3A]/30'
                }`}
              >
                STATE: {simState}
              </span>
              {/* Diagnostic Health Indicator */}
              <span
                className={`font-code text-[10px] font-semibold px-2 py-0.5 rounded-[2px] border ${
                  engineHealth === 'HEALTHY'
                    ? 'bg-[#2E7D32]/10 text-[#2E7D32] border-[#2E7D32]/30'
                    : engineHealth === 'CHECKING'
                      ? 'bg-[#F1F2F0] text-[#5E6561] border-[#D9DCD8]'
                      : 'bg-[#B83A3A]/10 text-[#B83A3A] border-[#B83A3A]/30'
                }`}
              >
                Counterfactual Engine: {engineHealth}
              </span>
            </div>
            <p className="text-[11.5px] text-[#5E6561] mt-0.5">
              Primary question: &ldquo;What would telemetry look like under counterfactual
              do(root_cause = nominal)?&rdquo;
            </p>
            <div className="flex items-center gap-x-3 gap-y-1 font-code text-[11px] text-[#5E6561] flex-wrap mt-0.5">
              <span className="flex items-center gap-1">
                <span className="text-[#171A19] font-medium">Root Cause:</span>
                <span className="text-[#00535f] font-semibold">{rootTarget}</span>
              </span>
              <span className="text-[#D9DCD8]">/</span>
              <span className="flex items-center gap-1">
                <span className="text-[#171A19] font-medium">SCM Methodology:</span>
                <span className="text-[#171A19]">Topology-Constrained Lagged VAR(5)</span>
              </span>
              <span className="text-[#D9DCD8]">/</span>
              <span className="flex items-center gap-1">
                <span className="text-[#171A19] font-medium">Resolution:</span>
                <span>1.0s / step</span>
              </span>
              <span className="text-[#D9DCD8]">/</span>
              <span className="flex items-center gap-1">
                <span className="text-[#171A19] font-medium">Horizon:</span>
                <span className="font-semibold text-[#00535f]">{totalHorizon}s</span>
              </span>
            </div>
          </div>

          {/* Intervention Specifier & Execution Hub */}
          <div className="flex items-center gap-2 flex-wrap self-start xl:self-center">
            {/* Active Specifier Info */}
            <div className="flex items-center bg-[#F7F7F5] px-2.5 py-1.5 rounded-[2px] gap-2 border border-[#D9DCD8]">
              <div className="w-2 h-2 rounded-full bg-[#00535f]"></div>
              <div className="flex flex-col">
                <span className="font-code text-[11px] text-[#171A19] font-medium flex items-center gap-1.5">
                  <span className="text-[#00535f] font-semibold">[INTERVENTION SPEC]</span>
                  {activeScenario.label}: {activeScenario.description}
                </span>
                <span className="font-code text-[9.5px] text-[#70797B]">
                  Target: {activeScenario.modeledComponent} · Magnitude:{' '}
                  {activeScenario.interventionMagnitude
                    ? `${(activeScenario.interventionMagnitude * 100).toFixed(0)}%`
                    : '100% (Nominal)'}
                </span>
              </div>
            </div>

            {/* Quick Switch Selector Tabs */}
            <div className="flex items-center bg-[#F1F2F0] rounded-[2px] p-0.5 border border-[#D9DCD8] overflow-x-auto max-w-full">
              {CANONICAL_INTERVENTIONS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => handleSelectScenario(s.id)}
                  className={`px-2.5 py-1 text-[11px] font-code rounded-[2px] transition-colors cursor-pointer whitespace-nowrap ${
                    selectedScenarioId === s.id
                      ? 'bg-white text-[#171A19] font-semibold shadow-xs border border-[#D9DCD8]'
                      : 'text-[#5E6561] hover:text-[#171A19]'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>

            {/* Primary Run Button */}
            <button
              onClick={() => handleRunSimulation()}
              disabled={simState === 'RUNNING'}
              className={`h-8 px-3 text-white font-code text-[11px] rounded-[2px] flex items-center gap-1.5 transition-all cursor-pointer font-medium tracking-wide uppercase disabled:opacity-70 shadow-xs ${
                simState === 'STALE'
                  ? 'bg-[#EF6C00] hover:bg-[#E65100] ring-2 ring-[#EF6C00]/40'
                  : 'bg-[#00535f] hover:bg-[#286B78]'
              }`}
            >
              <span
                className={`material-symbols-outlined text-[15px] ${
                  simState === 'RUNNING' ? 'animate-spin' : ''
                }`}
              >
                {simState === 'RUNNING' ? 'sync' : 'play_arrow'}
              </span>
              <span>{simState === 'RUNNING' ? 'SIMULATING SCM...' : 'Run Simulation'}</span>
            </button>
          </div>
        </div>

        {/* Stale Warning Banner */}
        {simState === 'STALE' && (
          <div className="mt-2.5 p-2 bg-[#FFF3E0] border border-[#FFE082] rounded-[2px] flex items-center justify-between text-[11.5px] font-code text-[#B78103]">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px]">warning</span>
              <span>
                <strong>Intervention changed:</strong> Current simulation results are out-of-date
                for &ldquo;{activeScenario.label}&rdquo;. Run simulation again to compute real SCM
                counterfactual rollout.
              </span>
            </div>
            <button
              onClick={() => handleRunSimulation()}
              className="px-2.5 py-0.5 bg-[#EF6C00] hover:bg-[#E65100] text-white rounded-[2px] font-semibold text-[10px] cursor-pointer"
            >
              RERUN NOW
            </button>
          </div>
        )}

        {/* Error Warning Banner */}
        {simState === 'ERROR' && errorMessage && (
          <div className="mt-2.5 p-2 bg-[#FFEBEE] border border-[#FFCDD2] rounded-[2px] flex items-center justify-between text-[11.5px] font-code text-[#B71C1C]">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-[16px]">error</span>
              <span>
                <strong>Simulation Error:</strong> {errorMessage}
              </span>
            </div>
            <button
              onClick={() => handleRunSimulation()}
              className="px-2.5 py-0.5 bg-[#B83A3A] hover:bg-[#992E2E] text-white rounded-[2px] font-semibold text-[10px] cursor-pointer"
            >
              RETRY
            </button>
          </div>
        )}
      </section>

      {/* 2. CORE WORKSPACE: 2-COLUMN BALANCED ANALYTICAL ARCHITECTURE */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 w-full">
        {/* LEFT COLUMN: SIMULATION TRAJECTORY & TOPOLOGY PROPAGATION (8 of 12 cols ≈ 66%) */}
        <div className="lg:col-span-8 flex flex-col gap-3">
          {/* A. DUAL TRAJECTORY SIMULATION CHART */}
          <section className="bg-white p-3 rounded-[3px] shadow-xs border border-[#D9DCD8] flex flex-col gap-2.5">
            {/* Header & Metric Selector */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-1 gap-1 border-b border-[#D9DCD8]">
              <div className="flex flex-col">
                <span className="font-section text-[10.5px] text-[#70797B] uppercase tracking-wider">
                  Comparative Dynamics · Causal Rollout
                </span>
                <h2 className="text-[15px] text-[#171A19] font-semibold tracking-tight">
                  Incident Impact Trajectory: Baseline vs Counterfactual
                </h2>
              </div>
              <div className="flex items-center gap-2 font-code text-[10px]">
                <div className="flex items-center bg-[#F1F2F0] rounded-[2px] p-0.5 border border-[#D9DCD8]">
                  <button
                    onClick={() => setSelectedMetric('gateway_latency')}
                    className={`px-2 py-0.5 rounded-[2px] cursor-pointer transition-colors ${
                      selectedMetric === 'gateway_latency'
                        ? 'bg-white text-[#00535f] font-semibold shadow-xs'
                        : 'text-[#5E6561]'
                    }`}
                  >
                    Gateway P99 Latency (ms)
                  </button>
                  <button
                    onClick={() => setSelectedMetric('root_cause')}
                    className={`px-2 py-0.5 rounded-[2px] cursor-pointer transition-colors ${
                      selectedMetric === 'root_cause'
                        ? 'bg-white text-[#00535f] font-semibold shadow-xs'
                        : 'text-[#5E6561]'
                    }`}
                  >
                    Root Cause ({rootTarget})
                  </button>
                </div>
                <span className="px-2 py-0.5 bg-[#F7F7F5] rounded-[2px] border border-[#D9DCD8] font-medium text-[#00535f]">
                  Horizon {totalHorizon}s · Step 1.0s
                </span>
              </div>
            </div>

            {/* Legend & Scrubber Delta Header */}
            <div className="flex items-center justify-between bg-[#F7F7F5] px-3 py-1.5 rounded-[2px] text-[10.5px] font-code flex-wrap gap-2 border border-[#D9DCD8]">
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-1.5">
                  <span className="w-3.5 h-[2.5px] bg-[#B83A3A] inline-block"></span>
                  <span className="font-medium text-[#171A19]">Baseline (No Intervention)</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-3.5 h-[2.5px] bg-[#286B78] inline-block"></span>
                  <span className="font-medium text-[#171A19]">
                    Counterfactual ({activeScenario.label})
                  </span>
                </div>
                <div className="flex items-center gap-1 text-[#5E6561]">
                  <span className="w-3 h-2 bg-[#286B78]/25 border border-[#286B78]/40 rounded-[1px] inline-block"></span>
                  <span>Uncertainty Range (±10% CI)</span>
                </div>
              </div>
              <div className="flex items-center gap-1.5 px-2 py-0.5 bg-white rounded-[2px] text-[#171A19] font-medium border border-[#D9DCD8]">
                <span className="text-[#5E6561]">SIMULATION TIME T+{currentTimeSec}s:</span>
                {simulationData ? (
                  <>
                    <span className="text-[#B83A3A] font-semibold">
                      {currentObsVal.toFixed(1)}
                      {selectedMetric === 'gateway_latency' ? 'ms' : ''}
                    </span>
                    <span className="text-[#5E6561]">vs</span>
                    <span className="text-[#00535f] font-semibold">
                      {currentCfVal.toFixed(1)}
                      {selectedMetric === 'gateway_latency' ? 'ms' : ''}
                    </span>
                    <span className="px-1.5 py-0.2 bg-[#00535f]/10 text-[#00535f] font-semibold rounded-[2px]">
                      Δ {currentDeltaVal > 0 ? '+' : ''}
                      {currentDeltaVal.toFixed(1)}
                      {selectedMetric === 'gateway_latency' ? 'ms' : ''}
                    </span>
                  </>
                ) : (
                  <span className="text-[#70797B] italic">Awaiting simulation run</span>
                )}
              </div>
            </div>

            {/* SVG Chart Area */}
            <div className="relative w-full aspect-[21/9] min-h-[290px] max-h-[380px] bg-white overflow-hidden select-none border border-[#D9DCD8] rounded-[2px] flex items-center justify-center">
              {simState === 'IDLE' ? (
                <div className="flex flex-col items-center justify-center p-6 text-center max-w-md">
                  <span className="material-symbols-outlined text-[44px] text-[#00535f]/40 mb-2">
                    science
                  </span>
                  <h3 className="text-[14px] font-bold text-[#171A19]">No Active Simulation</h3>
                  <p className="text-[11.5px] text-[#5E6561] mt-1 mb-3">
                    Select an intervention above and click RUN SIMULATION to execute the
                    Topology-Constrained Lagged SCM counterfactual rollout.
                  </p>
                  <button
                    onClick={() => handleRunSimulation()}
                    className="px-3.5 py-1.5 bg-[#00535f] hover:bg-[#286B78] text-white font-code text-[11px] rounded-[2px] cursor-pointer flex items-center gap-1.5"
                  >
                    <span className="material-symbols-outlined text-[15px]">play_arrow</span>
                    <span>Run Simulation</span>
                  </button>
                </div>
              ) : simState === 'RUNNING' ? (
                <div className="flex flex-col items-center justify-center p-6 text-center">
                  <span className="material-symbols-outlined text-[40px] text-[#00535f] animate-spin mb-2">
                    sync
                  </span>
                  <div className="font-code text-[12px] font-semibold text-[#171A19]">
                    Simulating Structural Causal Model...
                  </div>
                  <div className="font-code text-[10px] text-[#5E6561] mt-1">
                    Executing Pearl abduction &amp; counterfactual rollout on 5-node microservice
                    graph
                  </div>
                </div>
              ) : (
                <svg
                  className="w-full h-full text-[#BFC8CB]"
                  preserveAspectRatio="none"
                  viewBox="0 0 1000 420"
                >
                  {/* Horizontal Gridlines */}
                  <line
                    stroke="currentColor"
                    strokeDasharray="2 4"
                    strokeOpacity="0.45"
                    x1={svgLeft}
                    x2={svgLeft + svgWidth}
                    y1={svgTop}
                    y2={svgTop}
                  ></line>
                  <line
                    stroke="currentColor"
                    strokeDasharray="2 4"
                    strokeOpacity="0.45"
                    x1={svgLeft}
                    x2={svgLeft + svgWidth}
                    y1={svgTop + svgHeight * 0.25}
                    y2={svgTop + svgHeight * 0.25}
                  ></line>
                  <line
                    stroke="currentColor"
                    strokeDasharray="2 4"
                    strokeOpacity="0.45"
                    x1={svgLeft}
                    x2={svgLeft + svgWidth}
                    y1={svgTop + svgHeight * 0.5}
                    y2={svgTop + svgHeight * 0.5}
                  ></line>
                  <line
                    stroke="currentColor"
                    strokeDasharray="2 4"
                    strokeOpacity="0.45"
                    x1={svgLeft}
                    x2={svgLeft + svgWidth}
                    y1={svgTop + svgHeight * 0.75}
                    y2={svgTop + svgHeight * 0.75}
                  ></line>
                  <line
                    stroke="currentColor"
                    strokeOpacity="0.9"
                    x1={svgLeft}
                    x2={svgLeft + svgWidth}
                    y1={svgTop + svgHeight}
                    y2={svgTop + svgHeight}
                  ></line>

                  {/* Vertical Gridlines */}
                  {[0, 0.2, 0.4, 0.6, 0.8, 1.0].map((frac, idx) => {
                    const xPos = svgLeft + frac * svgWidth;
                    return (
                      <line
                        key={idx}
                        stroke="currentColor"
                        strokeDasharray="2 4"
                        strokeOpacity="0.25"
                        x1={xPos}
                        x2={xPos}
                        y1={svgTop}
                        y2={svgTop + svgHeight}
                      ></line>
                    );
                  })}

                  {/* Y Axis Labels */}
                  <text
                    className="fill-[#70797B] text-[11px] font-code"
                    textAnchor="end"
                    x={svgLeft - 10}
                    y={svgTop + 4}
                  >
                    {valMax}
                  </text>
                  <text
                    className="fill-[#70797B] text-[11px] font-code"
                    textAnchor="end"
                    x={svgLeft - 10}
                    y={svgTop + svgHeight * 0.25 + 4}
                  >
                    {(valMin + (valMax - valMin) * 0.75).toFixed(0)}
                  </text>
                  <text
                    className="fill-[#70797B] text-[11px] font-code"
                    textAnchor="end"
                    x={svgLeft - 10}
                    y={svgTop + svgHeight * 0.5 + 4}
                  >
                    {(valMin + (valMax - valMin) * 0.5).toFixed(0)}
                  </text>
                  <text
                    className="fill-[#70797B] text-[11px] font-code"
                    textAnchor="end"
                    x={svgLeft - 10}
                    y={svgTop + svgHeight * 0.75 + 4}
                  >
                    {(valMin + (valMax - valMin) * 0.25).toFixed(0)}
                  </text>
                  <text
                    className="fill-[#70797B] text-[11px] font-code"
                    textAnchor="end"
                    x={svgLeft - 10}
                    y={svgTop + svgHeight + 4}
                  >
                    {valMin}
                  </text>

                  {/* X Axis Labels */}
                  {[0, 0.2, 0.4, 0.6, 0.8, 1.0].map((frac, idx) => {
                    const xPos = svgLeft + frac * svgWidth;
                    const timeLabel = Math.round(frac * (totalHorizon - 1));
                    return (
                      <text
                        key={idx}
                        className="fill-[#70797B] text-[11px] font-code"
                        textAnchor="middle"
                        x={xPos}
                        y={svgTop + svgHeight + 22}
                      >
                        {timeLabel === 0 ? 'T0' : `T+${timeLabel}s`}
                      </text>
                    );
                  })}

                  {/* Intervention Marker Line */}
                  <line
                    stroke="#00535f"
                    strokeDasharray="3 3"
                    strokeWidth="1.5"
                    x1={interventionMarkerX}
                    x2={interventionMarkerX}
                    y1={svgTop - 15}
                    y2={svgTop + svgHeight}
                  ></line>
                  <rect
                    fill="#ADEDFC"
                    height="20"
                    rx="2"
                    stroke="#00535f"
                    strokeWidth="0.5"
                    width="195"
                    x={Math.min(svgLeft + svgWidth - 200, interventionMarkerX + 4)}
                    y={svgTop - 15}
                  ></rect>
                  <text
                    className="text-[10px] font-code fill-[#001F25] font-semibold"
                    x={Math.min(svgLeft + svgWidth - 200, interventionMarkerX + 4) + 6}
                    y={svgTop - 1}
                  >
                    T+{startStep}s: INTERVENTION APPLIED
                  </text>

                  {/* Confidence Interval Ribbon */}
                  {ribbonPoints && (
                    <polygon fill="#286B78" fillOpacity="0.14" points={ribbonPoints}></polygon>
                  )}

                  {/* BASELINE TRAJECTORY: Red */}
                  {baselinePath && (
                    <path
                      d={baselinePath}
                      fill="none"
                      stroke="#B83A3A"
                      strokeLinecap="round"
                      strokeWidth="2.5"
                    ></path>
                  )}

                  {/* COUNTERFACTUAL TRAJECTORY: Teal */}
                  {counterfactualPath && (
                    <path
                      d={counterfactualPath}
                      fill="none"
                      stroke="#286B78"
                      strokeLinecap="round"
                      strokeWidth="2.5"
                    ></path>
                  )}

                  {/* Playhead Vertical Cursor & Dynamic Tooltip */}
                  <line
                    stroke="#161D1A"
                    strokeDasharray="4 2"
                    strokeOpacity="0.85"
                    strokeWidth="1.2"
                    x1={playheadX}
                    x2={playheadX}
                    y1={svgTop - 25}
                    y2={svgTop + svgHeight}
                  ></line>
                  <circle cx={playheadX} cy={svgTop + svgHeight} fill="#161D1A" r="4"></circle>
                  <g>
                    <rect
                      fill="#161D1A"
                      height="20"
                      rx="2"
                      width="350"
                      x={Math.max(svgLeft, Math.min(svgLeft + svgWidth - 350, playheadX - 175))}
                      y={svgTop - 35}
                    ></rect>
                    <text
                      className="text-[10px] font-code fill-[#FFFFFF] font-medium"
                      textAnchor="middle"
                      x={
                        Math.max(svgLeft, Math.min(svgLeft + svgWidth - 350, playheadX - 175)) + 175
                      }
                      y={svgTop - 21}
                    >
                      T+{currentTimeSec}s · Baseline: {currentObsVal.toFixed(1)} vs Counterfactual:{' '}
                      {currentCfVal.toFixed(1)} (Δ {currentDeltaVal > 0 ? '+' : ''}
                      {currentDeltaVal.toFixed(1)})
                    </text>
                  </g>

                  {/* End Callouts */}
                  {seriesObs.length > 0 && (
                    <text
                      className="text-[10px] font-code fill-[#B83A3A] font-semibold"
                      textAnchor="end"
                      x={svgLeft + svgWidth}
                      y={svgTop + 14}
                    >
                      BASELINE: {seriesObs[seriesObs.length - 1].toFixed(1)}
                      {selectedMetric === 'gateway_latency' ? 'ms' : ''}
                    </text>
                  )}
                  {seriesCf.length > 0 && (
                    <text
                      className="text-[10px] font-code fill-[#286B78] font-semibold"
                      textAnchor="end"
                      x={svgLeft + svgWidth}
                      y={svgTop + svgHeight - 8}
                    >
                      COUNTERFACTUAL: {seriesCf[seriesCf.length - 1].toFixed(1)}
                      {selectedMetric === 'gateway_latency' ? 'ms' : ''}
                    </text>
                  )}
                </svg>
              )}
            </div>

            {/* Playback Controls & Slider */}
            <div className="flex items-center justify-between bg-[#F7F7F5] px-3 py-2 rounded-[2px] gap-3 flex-wrap border border-[#D9DCD8]">
              {/* Buttons */}
              <div className="flex items-center gap-1.5">
                <button
                  onClick={togglePlayPause}
                  disabled={!simulationData || simState === 'RUNNING'}
                  className="w-8 h-8 flex items-center justify-center rounded-[2px] bg-white text-[#171A19] hover:bg-[#EAECE8] transition-colors cursor-pointer border border-[#D9DCD8] disabled:opacity-50"
                  title={simState === 'PLAYING' ? 'Pause' : 'Play'}
                >
                  <span className="material-symbols-outlined text-[18px]">
                    {simState === 'PLAYING' ? 'pause' : 'play_arrow'}
                  </span>
                </button>

                <button
                  onClick={handleReset}
                  disabled={!simulationData || simState === 'RUNNING'}
                  className="w-8 h-8 flex items-center justify-center rounded-[2px] bg-white text-[#171A19] hover:bg-[#EAECE8] transition-colors cursor-pointer border border-[#D9DCD8] disabled:opacity-50"
                  title="Reset to T0 (0s)"
                >
                  <span className="material-symbols-outlined text-[18px]">restart_alt</span>
                </button>

                <span className="ml-1.5 font-code text-[11px] text-[#5E6561] uppercase font-medium">
                  PLAYBACK
                </span>
              </div>

              {/* Timeline Slider */}
              <div className="flex-1 max-w-md flex items-center gap-2 mx-2">
                <span className="font-code text-[11px] text-[#171A19] font-semibold">T0</span>
                <input
                  type="range"
                  min="0"
                  max={totalHorizon - 1}
                  value={currentTimeSec}
                  onChange={(e) => handleScrubberChange(Number(e.target.value))}
                  disabled={!simulationData || simState === 'RUNNING'}
                  className="w-full h-1.5 bg-[#D9DCD8] rounded-full accent-[#00535f] cursor-pointer disabled:opacity-50"
                />
                <span className="font-code text-[11px] text-[#171A19] font-semibold">
                  T+{totalHorizon - 1}s
                </span>
              </div>

              {/* Time and Speed Indicators */}
              <div className="flex items-center gap-2.5 font-code text-[11px]">
                <div className="px-2.5 py-0.5 rounded-[2px] bg-[#EAECE8] text-[#171A19] font-medium border border-[#D9DCD8]">
                  TIME: <span className="font-semibold text-[#00535f]">T+{currentTimeSec}s</span>
                </div>

                <div className="flex items-center gap-1 bg-white px-1.5 py-0.5 rounded-[2px] border border-[#D9DCD8]">
                  <span className="text-[#5E6561]">SPEED:</span>
                  {[0.5, 1.0, 2.0].map((s) => (
                    <button
                      key={s}
                      onClick={() => setPlaybackSpeed(s)}
                      className={`px-1 rounded-[1px] cursor-pointer font-semibold ${
                        playbackSpeed === s
                          ? 'bg-[#00535f] text-white'
                          : 'text-[#5E6561] hover:text-[#171A19]'
                      }`}
                    >
                      {s}x
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </section>

          {/* B. TOPOLOGY STATE COMPARISON */}
          <section className="bg-white p-3 rounded-[3px] shadow-xs border border-[#D9DCD8] flex flex-col gap-2.5">
            <div className="flex items-center justify-between pb-1 border-b border-[#D9DCD8]">
              <div>
                <span className="font-section text-[10.5px] text-[#70797B] uppercase tracking-wider">
                  Topology State Comparison
                </span>
                <h3 className="text-[15px] text-[#171A19] font-semibold tracking-tight">
                  Dynamic Topology State Comparison at T+{currentTimeSec}s
                </h3>
              </div>
              <span className="font-code text-[10px] text-[#5E6561] bg-[#F1F2F0] px-2 py-0.5 rounded-[2px] border border-[#D9DCD8]">
                REAL-TIME SCM SLICE
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Left Sub-Panel: Baseline Future (No Intervention) */}
              <div className="bg-[#111416] p-3 rounded-[3px] text-white flex flex-col justify-between min-h-[360px] border border-[#252A2E]">
                <div>
                  <div className="flex items-center justify-between pb-2 border-b border-[#252A2E]">
                    <span className="font-code text-[11px] text-[#FFA4A4] font-semibold tracking-wide flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-[#B83A3A] animate-pulse"></span>
                      BASELINE FUTURE (NO INTERVENTION)
                    </span>
                    <span className="font-code text-[10px] text-[#8E9599]">UNMITIGATED</span>
                  </div>
                  <div className="mt-2 font-code text-[10.5px] text-[#FFDAD6] font-medium flex items-center justify-between">
                    <span>OBSERVED CASCADE AT T+{currentTimeSec}s</span>
                    <span className="text-[#B83A3A] font-semibold uppercase text-[10px]">
                      {currentFrame?.services?.['api-gateway']?.state || 'CRITICAL'}
                    </span>
                  </div>

                  {/* Directed Chain Visualization */}
                  <div className="mt-3 flex flex-col gap-1.5 font-code text-[11px]">
                    {['inventory-db', 'inventory-service', 'order-service', 'api-gateway', 'payment-service'].map(
                      (node, idx) => {
                        const svcData = currentFrame?.services?.[node]?.observed;
                        const p99 = svcData?.p99_latency ?? 0;
                        const err = svcData?.error_rate ?? 0;
                        const isCrit = p99 > 800 || err > 5;
                        const isDegr = p99 > 200 || err > 1;

                        return (
                          <React.Fragment key={node}>
                            <div className="p-2 rounded bg-[#1E2327] border-l-2 border-[#B83A3A] flex items-center justify-between">
                              <div>
                                <div className="text-white font-semibold">{node}</div>
                                <div className="text-[#FFB4AB] text-[10px]">
                                  P99 {p99.toFixed(0)}ms · ERR {err.toFixed(2)}%
                                </div>
                              </div>
                              <span
                                className={`px-1.5 py-0.5 rounded font-medium text-[10px] ${
                                  isCrit
                                    ? 'bg-[#B83A3A]/40 text-[#FFB4AB]'
                                    : isDegr
                                      ? 'bg-[#EF6C00]/30 text-[#FFE082]'
                                      : 'bg-[#2E7D32]/30 text-[#C8E6C9]'
                                }`}
                              >
                                {isCrit ? 'CRITICAL' : isDegr ? 'DEGRADED' : 'OPERATIONAL'}
                              </span>
                            </div>
                            {idx < 4 && (
                              <div className="flex justify-center text-[#FF897D] text-[10px] -my-1">
                                ↓ propagation
                              </div>
                            )}
                          </React.Fragment>
                        );
                      }
                    )}
                  </div>
                </div>

                <div className="pt-2 mt-2 border-t border-[#252A2E] flex items-center justify-between font-code text-[10px] text-[#8E9599]">
                  <span>CASCADE STATUS: EXPANDING</span>
                  <span className="text-[#B83A3A] font-medium">UNCONTROLLED LATENCY</span>
                </div>
              </div>

              {/* Right Sub-Panel: Counterfactual Future (Intervened) */}
              <div className="bg-[#111416] p-3 rounded-[3px] text-white flex flex-col justify-between min-h-[360px] border border-[#252A2E]">
                <div>
                  <div className="flex items-center justify-between pb-2 border-b border-[#252A2E]">
                    <span className="font-code text-[11px] text-[#ADEDFC] font-semibold tracking-wide flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-[#286B78] animate-pulse"></span>
                      COUNTERFACTUAL FUTURE ({activeScenario.label})
                    </span>
                    <span className="font-code text-[10px] text-[#ADEDFC] font-semibold">
                      {operationalCount}/{totalServiceCount} RECOVERED
                    </span>
                  </div>
                  <div className="mt-2 font-code text-[10.5px] text-[#91D0DF] font-medium flex items-center justify-between">
                    <span>SIMULATED OUTCOME AT T+{currentTimeSec}s</span>
                    <span className="text-[#ADEDFC] font-semibold uppercase text-[10px]">
                      {operationalCount === totalServiceCount ? 'FULLY RECOVERED' : 'MITIGATING'}
                    </span>
                  </div>

                  {/* Directed Recovered Chain */}
                  <div className="mt-3 flex flex-col gap-1.5 font-code text-[11px]">
                    {['inventory-db', 'inventory-service', 'order-service', 'api-gateway', 'payment-service'].map(
                      (node, idx) => {
                        const svcData = currentFrame?.services?.[node]?.counterfactual;
                        const effectData = currentFrame?.services?.[node]?.effect_delta;
                        const p99 = svcData?.p99_latency ?? 0;
                        const err = svcData?.error_rate ?? 0;
                        const deltaP99 = effectData?.p99_latency ?? 0;
                        const state = currentFrame?.services?.[node]?.state || 'OPERATIONAL';

                        return (
                          <React.Fragment key={node}>
                            <div className="p-2 rounded bg-[#152427] border-l-2 border-[#ADEDFC] flex items-center justify-between">
                              <div>
                                <div className="text-white font-semibold">{node}</div>
                                <div className="text-[#91D0DF] text-[10px]">
                                  P99 {p99.toFixed(0)}ms · ERR {err.toFixed(2)}% (Δ{' '}
                                  {deltaP99 > 0 ? `-${deltaP99.toFixed(0)}ms` : '0ms'})
                                </div>
                              </div>
                              <span
                                className={`px-1.5 py-0.5 rounded font-medium text-[10px] ${
                                  state === 'OPERATIONAL'
                                    ? 'bg-[#00535f] text-white'
                                    : state === 'DEGRADED'
                                      ? 'bg-[#EF6C00]/40 text-[#FFE082]'
                                      : 'bg-[#B83A3A]/40 text-[#FFB4AB]'
                                }`}
                              >
                                {state}
                              </span>
                            </div>
                            {idx < 4 && (
                              <div className="flex justify-center text-[#91D0DF] text-[10px] -my-1">
                                ↓ attenuated
                              </div>
                            )}
                          </React.Fragment>
                        );
                      }
                    )}
                  </div>
                </div>

                <div className="pt-2 mt-2 border-t border-[#252A2E] flex items-center justify-between font-code text-[10px] text-[#91D0DF]">
                  <span>RECOVERY: {((operationalCount / totalServiceCount) * 100).toFixed(0)}%</span>
                  <span className="text-[#ADEDFC] font-medium">
                    CONVERGENCE: {convergenceStep !== null ? `T+${convergenceStep}s` : 'In Progress'}
                  </span>
                </div>
              </div>
            </div>
          </section>
        </div>

        {/* RIGHT COLUMN: OUTCOME & ATTENUATION (4 of 12 cols ≈ 34%) */}
        <div className="lg:col-span-4 flex flex-col gap-3">
          {/* A. MODEL-ESTIMATED OUTCOME SUMMARY */}
          <section className="bg-white p-3 rounded-[3px] shadow-xs border border-[#D9DCD8] flex flex-col gap-2">
            <div className="flex items-center justify-between pb-1 border-b border-[#D9DCD8]">
              <div>
                <span className="font-section text-[10px] text-[#70797B] uppercase tracking-wider">
                  Intervention Analysis
                </span>
                <h3 className="text-[14px] text-[#171A19] font-semibold tracking-tight">
                  MODEL-ESTIMATED OUTCOME
                </h3>
              </div>
              <span className="px-1.5 py-0.5 rounded-[2px] bg-[#F1F2F0] text-[#00535f] font-code text-[9.5px] font-semibold border border-[#D9DCD8]">
                {simulationData?.validity_metadata?.causal_validation_status === 'PASS'
                  ? 'SCM VALIDATED'
                  : 'SCM ESTIMATE'}
              </span>
            </div>

            {simulationData ? (
              <div className="flex flex-col gap-2 mt-1">
                {/* Metric 1: Peak Impact Reduction */}
                <div className="p-2 bg-[#F7F7F5] rounded-[2px] flex items-center justify-between border border-[#D9DCD8]/60">
                  <div className="flex flex-col font-code">
                    <span className="text-[9.5px] text-[#70797B] font-medium uppercase">
                      PEAK GATEWAY LATENCY
                    </span>
                    <div className="flex items-center gap-1 text-[13px] text-[#171A19] mt-0.5">
                      <span className="text-[#B83A3A] line-through font-semibold">
                        {peakObsLatency.toFixed(0)}ms
                      </span>
                      <span>→</span>
                      <span className="text-[#00535f] font-bold">
                        {peakCfLatency.toFixed(0)}ms
                      </span>
                    </div>
                  </div>
                  <span className="font-code text-[11px] px-2 py-0.5 bg-white text-[#00535f] font-semibold rounded-[2px] border border-[#D9DCD8]">
                    -{peakLatencyReductionPct}%
                  </span>
                </div>

                {/* Metric 2: Services Recovered at T */}
                <div className="p-2 bg-[#F7F7F5] rounded-[2px] flex items-center justify-between border border-[#D9DCD8]/60">
                  <div className="flex flex-col font-code">
                    <span className="text-[9.5px] text-[#70797B] font-medium uppercase">
                      SERVICES RECOVERED
                    </span>
                    <div className="text-[13px] text-[#171A19] font-semibold mt-0.5">
                      {operationalCount} / {totalServiceCount} Healthy
                    </div>
                  </div>
                  <span className="font-code text-[11px] px-2 py-0.5 bg-white text-[#00535f] font-semibold rounded-[2px] border border-[#D9DCD8]">
                    {((operationalCount / totalServiceCount) * 100).toFixed(0)}% @ T+{currentTimeSec}s
                  </span>
                </div>

                {/* Metric 3: Current Gateway Latency at T */}
                <div className="p-2 bg-[#F7F7F5] rounded-[2px] flex items-center justify-between border border-[#D9DCD8]/60">
                  <div className="flex flex-col font-code">
                    <span className="text-[9.5px] text-[#70797B] font-medium uppercase">
                      GATEWAY P99 AT T+{currentTimeSec}s
                    </span>
                    <div className="flex items-center gap-1 text-[13px] text-[#171A19] mt-0.5">
                      <span className="text-[#B83A3A] line-through font-medium">
                        {(currentFrame?.gateway_latency_observed ?? 0).toFixed(0)}ms
                      </span>
                      <span>→</span>
                      <span className="text-[#00535f] font-bold">
                        {(currentFrame?.gateway_latency_counterfactual ?? 0).toFixed(0)}ms
                      </span>
                    </div>
                  </div>
                  <span className="font-code text-[11px] px-2 py-0.5 bg-white text-[#00535f] font-semibold rounded-[2px] border border-[#D9DCD8]">
                    Δ{' '}
                    {(
                      (currentFrame?.gateway_latency_counterfactual ?? 0) -
                      (currentFrame?.gateway_latency_observed ?? 0)
                    ).toFixed(0)}
                    ms
                  </span>
                </div>

                {/* Metric 4: Gateway Error Rate at T */}
                <div className="p-2 bg-[#F7F7F5] rounded-[2px] flex items-center justify-between border border-[#D9DCD8]/60">
                  <div className="flex flex-col font-code">
                    <span className="text-[9.5px] text-[#70797B] font-medium uppercase">
                      GATEWAY ERROR RATE AT T+{currentTimeSec}s
                    </span>
                    <div className="flex items-center gap-1 text-[13px] text-[#171A19] mt-0.5">
                      <span className="text-[#B83A3A] line-through font-medium">
                        {(currentFrame?.gateway_error_rate_observed ?? 0).toFixed(2)}%
                      </span>
                      <span>→</span>
                      <span className="text-[#00535f] font-bold">
                        {(currentFrame?.gateway_error_rate_counterfactual ?? 0).toFixed(2)}%
                      </span>
                    </div>
                  </div>
                  <span className="font-code text-[11px] px-2 py-0.5 bg-white text-[#00535f] font-semibold rounded-[2px] border border-[#D9DCD8]">
                    Δ{' '}
                    {(
                      (currentFrame?.gateway_error_rate_counterfactual ?? 0) -
                      (currentFrame?.gateway_error_rate_observed ?? 0)
                    ).toFixed(2)}
                    %
                  </span>
                </div>
              </div>
            ) : (
              <div className="p-4 bg-[#F7F7F5] rounded-[2px] text-center font-code text-[11px] text-[#70797B] my-2">
                Awaiting simulation run — metrics will be populated directly from causal rollout.
              </div>
            )}

            <p className="font-code text-[10px] text-[#70797B] mt-1 leading-normal italic">
              * Values computed directly from SCM counterfactual trajectory under intervention
              do({activeScenario.modeledComponent}).
            </p>
          </section>

          {/* B. CAUSAL CASCADE ATTENUATION SEQUENCE */}
          <section className="bg-white p-3 rounded-[3px] shadow-xs border border-[#D9DCD8] flex flex-col gap-1.5">
            <div className="flex items-center justify-between pb-1 border-b border-[#D9DCD8]">
              <div>
                <span className="font-section text-[10px] text-[#70797B] uppercase tracking-wider">
                  Path Mechanics
                </span>
                <h3 className="text-[14px] text-[#171A19] font-semibold tracking-tight">
                  Causal Cascade Attenuation
                </h3>
              </div>
              <span className="material-symbols-outlined text-[18px] text-[#70797B]">
                account_tree
              </span>
            </div>

            {simulationData ? (
              <div className="flex flex-col gap-1.5 mt-1 font-code">
                {/* Step 1: Root Cause Node */}
                <div className="flex items-start gap-2 p-2 bg-[#F7F7F5] rounded-[2px] border border-[#D9DCD8]/60">
                  <span className="w-5 h-5 flex items-center justify-center bg-[#00535f] text-white text-[10.5px] font-semibold rounded-full mt-0.5">
                    1
                  </span>
                  <div className="flex flex-col flex-1">
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-semibold text-[#171A19]">{rootTarget}</span>
                      <span className="text-[#00535f] font-semibold">
                        Δ{' '}
                        {(
                          currentFrame?.services?.[rootTarget]?.effect_delta?.db_latency ??
                          currentFrame?.services?.[rootTarget]?.effect_delta?.p99_latency ??
                          0
                        ).toFixed(0)}
                        ms
                      </span>
                    </div>
                    <span className="text-[10px] text-[#5E6561] mt-0.5">
                      Target intervention applied at T+{startStep}s
                    </span>
                  </div>
                </div>
                <div className="flex justify-center text-[#70797B] text-[10px] -my-1">↓</div>

                {/* Step 2: Immediate neighbor */}
                <div className="flex items-start gap-2 p-2 bg-[#F7F7F5] rounded-[2px] border border-[#D9DCD8]/60">
                  <span className="w-5 h-5 flex items-center justify-center bg-[#286B78] text-white text-[10.5px] font-semibold rounded-full mt-0.5">
                    2
                  </span>
                  <div className="flex flex-col flex-1">
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-semibold text-[#171A19]">inventory-service</span>
                      <span className="text-[#00535f] font-semibold">
                        Δ{' '}
                        {(
                          currentFrame?.services?.['inventory-service']?.effect_delta
                            ?.p99_latency ?? 0
                        ).toFixed(0)}
                        ms P99
                      </span>
                    </div>
                    <span className="text-[10px] text-[#5E6561] mt-0.5">
                      Connection pool acquisition unblocked
                    </span>
                  </div>
                </div>
                <div className="flex justify-center text-[#70797B] text-[10px] -my-1">↓</div>

                {/* Step 3: Upstream service */}
                <div className="flex items-start gap-2 p-2 bg-[#F7F7F5] rounded-[2px] border border-[#D9DCD8]/60">
                  <span className="w-5 h-5 flex items-center justify-center bg-[#286B78] text-white text-[10.5px] font-semibold rounded-full mt-0.5">
                    3
                  </span>
                  <div className="flex flex-col flex-1">
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-semibold text-[#171A19]">order-service</span>
                      <span className="text-[#00535f] font-semibold">
                        Δ{' '}
                        {(
                          currentFrame?.services?.['order-service']?.effect_delta?.p99_latency ?? 0
                        ).toFixed(0)}
                        ms P99
                      </span>
                    </div>
                    <span className="text-[10px] text-[#5E6561] mt-0.5">
                      Synchronous RPC pipeline latency restored
                    </span>
                  </div>
                </div>
                <div className="flex justify-center text-[#70797B] text-[10px] -my-1">↓</div>

                {/* Step 4: Edge gateway */}
                <div className="flex items-start gap-2 p-2 bg-[#F7F7F5] rounded-[2px] border border-[#D9DCD8]/60">
                  <span className="w-5 h-5 flex items-center justify-center bg-[#605889] text-white text-[10.5px] font-semibold rounded-full mt-0.5">
                    4
                  </span>
                  <div className="flex flex-col flex-1">
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-semibold text-[#171A19]">api-gateway</span>
                      <span className="text-[#00535f] font-semibold">
                        Δ{' '}
                        {(
                          currentFrame?.services?.['api-gateway']?.effect_delta?.p99_latency ?? 0
                        ).toFixed(0)}
                        ms
                      </span>
                    </div>
                    <span className="text-[10px] text-[#5E6561] mt-0.5">
                      Edge ingress 504 timeouts mitigated
                    </span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="p-4 bg-[#F7F7F5] rounded-[2px] text-center font-code text-[11px] text-[#70797B] my-2">
                Awaiting simulation run — cascade path deltas will update dynamically.
              </div>
            )}
          </section>

          {/* C. CANDIDATE COMPARISON */}
          <section className="bg-white p-3 rounded-[3px] shadow-xs border border-[#D9DCD8] flex flex-col gap-1.5">
            <div className="flex items-center justify-between pb-1 border-b border-[#D9DCD8]">
              <div>
                <span className="font-section text-[10px] text-[#70797B] uppercase tracking-wider">
                  Candidate Comparison
                </span>
                <h3 className="text-[14px] text-[#171A19] font-semibold tracking-tight">
                  Alternative Interventions
                </h3>
              </div>
              <span className="font-code text-[10px] text-[#70797B]">
                {CANONICAL_INTERVENTIONS.length} CANDIDATES
              </span>
            </div>

            <div className="flex flex-col gap-2 mt-1">
              {CANONICAL_INTERVENTIONS.map((s) => (
                <div
                  key={s.id}
                  onClick={() => handleSelectScenario(s.id)}
                  className={`p-2 rounded-[2px] flex flex-col gap-1 cursor-pointer transition-colors border ${
                    selectedScenarioId === s.id
                      ? 'bg-[#E9EDE9] border-[#00535f]'
                      : 'bg-[#F7F7F5] hover:bg-[#F1F2F0] border-[#D9DCD8]/60'
                  }`}
                >
                  <div className="flex items-center justify-between font-code text-[11px]">
                    <span className="font-semibold text-[#171A19]">{s.description}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRunSimulation(s);
                      }}
                      className={`px-2 py-0.5 text-[9.5px] font-semibold rounded-[2px] transition-colors cursor-pointer ${
                        selectedScenarioId === s.id && simulatedScenarioId === s.id
                          ? 'bg-[#00535f] text-white'
                          : 'bg-white border border-[#D9DCD8] text-[#171A19] hover:bg-[#EAECE8]'
                      }`}
                    >
                      {selectedScenarioId === s.id && simulatedScenarioId === s.id
                        ? 'ACTIVE'
                        : 'SIMULATE'}
                    </button>
                  </div>
                  <div className="flex items-center justify-between font-code text-[10px] text-[#5E6561]">
                    <span>
                      Target: <strong className="text-[#00535f]">{s.modeledComponent}</strong>
                    </span>
                    <span>
                      Risk: <span className="text-[#B83A3A] font-medium">{s.riskLabel}</span>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* D. ASSUMPTIONS & BOUNDS */}
          <section className="bg-white p-3 rounded-[3px] shadow-xs border border-[#D9DCD8] flex flex-col gap-1.5">
            <div className="flex items-center justify-between pb-1 border-b border-[#D9DCD8]">
              <div>
                <span className="font-section text-[10px] text-[#70797B] uppercase tracking-wider">
                  Epistemic Credibility
                </span>
                <h3 className="text-[14px] text-[#171A19] font-semibold tracking-tight">
                  Assumptions &amp; Bounds
                </h3>
              </div>
              <span className="font-code text-[10px] text-[#00535f] font-semibold">
                {simulationData?.validity_metadata?.extrapolation_status || 'IN_DOMAIN'}
              </span>
            </div>
            <div className="p-2 bg-[#F7F7F5] rounded-[2px] font-code text-[10.5px] text-[#5E6561] flex flex-col gap-1.5 border border-[#D9DCD8]/60">
              <div className="flex items-start gap-1.5">
                <span className="text-[#00535f] font-bold">•</span>
                <span>
                  <strong>Causal status:</strong>{' '}
                  {simulationData?.validity_metadata?.causal_validation_status ?? 'READY'} ·
                  Physical Bounds: {simulationData?.validity_metadata?.physical_validity ?? 'PASS'}
                </span>
              </div>
              <div className="flex items-start gap-1.5">
                <span className="text-[#00535f] font-bold">•</span>
                <span>
                  <strong>Topology invariance:</strong> Physical service dependency graph DAG
                  remains invariant during rollout.
                </span>
              </div>
              <div className="flex items-start gap-1.5">
                <span className="text-[#00535f] font-bold">•</span>
                <span>
                  <strong>Non-linear risk:</strong>{' '}
                  {simulationData?.validity_metadata?.nonlinear_risk || 'low'}
                </span>
              </div>
            </div>
          </section>

          {/* E. OPERATIONAL ACTION FOOTER */}
          <section className="bg-white p-3 rounded-[3px] shadow-xs border border-[#D9DCD8] flex flex-col gap-2 border-t-2 border-t-[#00535f]">
            <div className="flex items-center justify-between font-code text-[11px]">
              <span className="text-[#171A19] font-semibold uppercase tracking-wider flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-[#00535f]"></span>
                SIMULATION STATUS: {simState}
              </span>
              <span className="text-[#5E6561] font-medium">SAFETY GATE: ACTIVE</span>
            </div>
            <div className="grid grid-cols-2 gap-1.5 font-code text-[10.5px]">
              <button
                onClick={() => handleRunSimulation()}
                disabled={simState === 'RUNNING'}
                className="h-7 bg-[#F7F7F5] hover:bg-[#EAECE8] text-[#171A19] font-medium rounded-[2px] flex items-center justify-center gap-1 transition-colors cursor-pointer border border-[#D9DCD8] disabled:opacity-50"
              >
                <span className="material-symbols-outlined text-[14px]">refresh</span>
                <span>Re-run Simulation</span>
              </button>
              <button
                onClick={() => onNavigate('root-cause')}
                className="h-7 bg-[#F7F7F5] hover:bg-[#EAECE8] text-[#171A19] font-medium rounded-[2px] flex items-center justify-center gap-1 transition-colors cursor-pointer border border-[#D9DCD8]"
              >
                <span className="material-symbols-outlined text-[14px]">account_tree</span>
                <span>View Root Cause</span>
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
};

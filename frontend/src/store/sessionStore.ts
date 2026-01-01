import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ModelSummary, Modification, SimulationResult, SimulationRequest, Constraint } from '@/api/client'

export type SimConfig = Omit<SimulationRequest, 'modifications' | 'constraints'>

const DEFAULT_CONFIG: SimConfig = {
  sim_days: 30,
  warmup_days: 7,
  num_runs: 2,
  random_seed: 42,
  enable_resource_contention: true,
  resource_routing: 'probabilistic',
}

interface SessionState {
  sessionId: string | null
  modelSummary: ModelSummary | null
  modifications: Modification[]
  constraints: Constraint[]
  baselineResult: SimulationResult | null
  scenarioResult: SimulationResult | null
  simConfig: SimConfig
  setSessionId: (id: string) => void
  setModelSummary: (s: ModelSummary) => void
  addModification: (m: Modification | Modification[]) => void
  removeModification: (idx: number) => void
  clearModifications: () => void
  addConstraint: (c: Constraint) => void
  removeConstraint: (idx: number) => void
  clearConstraints: () => void
  setBaselineResult: (r: SimulationResult) => void
  setScenarioResult: (r: SimulationResult | null) => void
  setSimConfig: (c: Partial<SimConfig>) => void
  reset: () => void
}

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      sessionId: null,
      modelSummary: null,
      modifications: [],
      constraints: [],
      baselineResult: null,
      scenarioResult: null,
      simConfig: DEFAULT_CONFIG,
      setSessionId: (id) => set({ sessionId: id }),
      setModelSummary: (s) => set({ modelSummary: s }),
      addModification: (m) => set((state) => ({
        modifications: state.modifications.concat(Array.isArray(m) ? m : [m]),
      })),
      removeModification: (idx) => set((state) => ({
        modifications: state.modifications.filter((_, i) => i !== idx),
      })),
      clearModifications: () => set({ modifications: [] }),
      addConstraint: (c) => set((state) => ({ constraints: [...state.constraints, c] })),
      removeConstraint: (idx) => set((state) => ({
        constraints: state.constraints.filter((_, i) => i !== idx),
      })),
      clearConstraints: () => set({ constraints: [] }),
      setBaselineResult: (r) => set({ baselineResult: r }),
      setScenarioResult: (r) => set({ scenarioResult: r }),
      setSimConfig: (c) => set((state) => ({ simConfig: { ...state.simConfig, ...c } })),
      reset: () => set({
        sessionId: null, modelSummary: null, modifications: [],
        constraints: [], baselineResult: null, scenarioResult: null, simConfig: DEFAULT_CONFIG,
      }),
    }),
    {
      name: 'bps-session',
      partialize: (state) => ({
        sessionId: state.sessionId,
        modelSummary: state.modelSummary,
        modifications: state.modifications,
        constraints: state.constraints,
        simConfig: state.simConfig,
      }),
    }
  )
)

import { useEffect, useMemo, useState } from "react";
import { Activity, ClipboardCheck, Database, FileDown, FileText, RefreshCw, ShieldCheck, Wand2 } from "lucide-react";
import { createRoot } from "react-dom/client";
import { BehaviorHistoryChart } from "./components/BehaviorHistoryChart";
import { ClinicalDisclaimer } from "./components/ClinicalDisclaimer";
import { RiskBadge } from "./components/RiskBadge";
import { api } from "./services/api";
import type { ABCAssociationsResponse, Appointment, Behavior, BehaviorRecord, NoteExtraction, NoteFeaturesResponse, Patient, Prediction, TrainResult } from "./types/api";
import "./styles.css";

export default function App() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [behaviors, setBehaviors] = useState<Behavior[]>([]);
  const [patientId, setPatientId] = useState("");
  const [behaviorName, setBehaviorName] = useState("");
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [appointmentId, setAppointmentId] = useState("");
  const [records, setRecords] = useState<BehaviorRecord[]>([]);
  const [trainResult, setTrainResult] = useState<TrainResult | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [noteExtraction, setNoteExtraction] = useState<NoteExtraction | null>(null);
  const [noteFeatures, setNoteFeatures] = useState<NoteFeaturesResponse | null>(null);
  const [abcAssociations, setAbcAssociations] = useState<ABCAssociationsResponse | null>(null);
  const [history, setHistory] = useState<Prediction[]>([]);
  const [status, setStatus] = useState("Pronto para sincronizar dados mock.");
  const [busy, setBusy] = useState(false);

  async function loadPatients() {
    const loadedPatients = await api.patients();
    setPatients(loadedPatients);
    if (!patientId && loadedPatients.length) setPatientId(loadedPatients[0].patient_id);
  }

  useEffect(() => {
    loadPatients().catch(() => undefined);
    api.predictionHistory().then(setHistory).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!patientId) return;
    api.behaviors(patientId).then((items) => {
      setBehaviors(items);
      if (!behaviorName && items.length) setBehaviorName(items[0].behavior_name);
    });
    api.appointments(patientId).then((items) => {
      setAppointments(items);
      setAppointmentId((current) => current || items[0]?.appointment_id || "");
    }).catch(() => {
      setAppointments([]);
      setAppointmentId("");
    });
  }, [patientId]);

  useEffect(() => {
    if (!patientId || !behaviorName) return;
    api.history(patientId, behaviorName).then(setRecords).catch(() => setRecords([]));
    api.noteFeatures(patientId, behaviorName).then(setNoteFeatures).catch(() => setNoteFeatures(null));
  }, [patientId, behaviorName]);

  const probabilityLabel = useMemo(() => {
    if (!prediction) return "0%";
    return `${Math.round(prediction.probability * 100)}%`;
  }, [prediction]);

  async function run<T>(label: string, task: () => Promise<T>, after?: (result: T) => void) {
    setBusy(true);
    setStatus(label);
    try {
      const result = await task();
      after?.(result);
      setStatus("Operacao concluida.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Erro inesperado.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="appShell">
      <header className="topBar">
        <div>
          <h1>bHave Behavioral Prediction</h1>
          <p>Previsao de risco para proxima sessao com dados comportamentais anonimizados.</p>
        </div>
        <ClinicalDisclaimer />
      </header>

      <section className="toolbar">
        <button disabled={busy} onClick={() => run("Sincronizando dados mock...", api.syncMock, () => loadPatients())}>
          <Database size={18} /> Sincronizar mock
        </button>
        <label>
          Paciente
          <select value={patientId} onChange={(event) => setPatientId(event.target.value)}>
            {patients.map((patient) => <option key={patient.patient_id}>{patient.patient_id}</option>)}
          </select>
        </label>
        <label>
          Comportamento
          <select value={behaviorName} onChange={(event) => setBehaviorName(event.target.value)}>
            {behaviors.map((behavior) => <option key={behavior.behavior_name}>{behavior.behavior_name}</option>)}
          </select>
        </label>
        <label>
          Atendimento
          <select value={appointmentId} onChange={(event) => setAppointmentId(event.target.value)}>
            {appointments.map((appointment) => (
              <option key={appointment.appointment_id} value={appointment.appointment_id}>
                {appointment.appointment_date || "sem data"} - {appointment.appointment_id}
              </option>
            ))}
          </select>
        </label>
        <button disabled={busy || !behaviorName} onClick={() => run("Treinando modelos...", () => api.train(patientId, behaviorName), setTrainResult)}>
          <Wand2 size={18} /> Treinar
        </button>
        <button disabled={busy || !behaviorName} onClick={() => run("Gerando previsao...", () => api.predict(patientId, behaviorName), (result) => {
          setPrediction(result);
          api.predictionHistory().then(setHistory);
        })}>
          <RefreshCw size={18} /> Prever
        </button>
      </section>

      <div className="statusLine">{status}</div>

      <section className="grid">
        <article className="panel predictionPanel">
          <div className="panelHeader">
            <Activity size={20} />
            <h2>Previsao atual</h2>
          </div>
          <div className="probability">{probabilityLabel}</div>
          {prediction ? (
            <>
              <RiskBadge risk={prediction.risk} />
              <p className="muted">Modelo: {prediction.model} | Janela: {prediction.prediction_window}</p>
              <div className="predictionMeta">
                <Metric label="Baseline pessoal" value={formatPercent(prediction.personal_baseline ?? prediction.baseline_probability)} />
                <Metric label="Qualidade do dado" value={prediction.data_quality ?? "nao informado"} />
                <Metric label="Abstencao" value={prediction.abstain ? prediction.abstain_reason ?? "sim" : "nao"} />
                <Metric label="Incerteza" value={formatUncertainty(prediction.uncertainty)} />
              </div>
              <p className="warningLine">Associacao nao implica funcao ou causa. A previsao nao autoriza intervencao automatica.</p>
              <ul className="factorList">
                {prediction.top_factors.map((factor) => (
                  <li key={factor.factor}>
                    <span>{factor.factor}</span>
                    <strong>{String(factor.value)}</strong>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">Sincronize, selecione um comportamento e gere uma previsao.</p>
          )}
        </article>

        <article className="panel">
          <div className="panelHeader">
            <ShieldCheck size={20} />
            <h2>Metricas do treino</h2>
          </div>
          {trainResult ? (
            <div className="metricsGrid">
              <Metric label="Modelo" value={trainResult.selected_model} />
              <Metric label="Amostras" value={trainResult.samples.toString()} />
              <Metric label="Taxa historica" value={`${Math.round(trainResult.event_rate * 100)}%`} />
              <Metric label="Recall RF" value={formatMetric(trainResult.metrics.random_forest?.recall)} />
              <Metric label="F1 RF" value={formatMetric(trainResult.metrics.random_forest?.f1_score)} />
              <Metric label="Brier RF" value={formatMetric(trainResult.metrics.random_forest?.brier_score)} />
            </div>
          ) : (
            <p className="muted">As metricas aparecem depois do treinamento.</p>
          )}
        </article>

        <article className="panel wide">
          <div className="panelHeader">
            <Activity size={20} />
            <h2>Historico comportamental</h2>
          </div>
          <BehaviorHistoryChart records={records} />
        </article>

        <article className="panel wide">
          <div className="panelHeader">
            <FileText size={20} />
            <h2>Anotacoes de atendimento</h2>
          </div>
          <div className="noteActions">
            <button disabled={busy || !appointmentId} onClick={() => run("Extraindo anotacao...", () => api.extractAppointmentNotes(appointmentId), (result) => {
              setNoteExtraction(result);
              if (patientId && behaviorName) api.noteFeatures(patientId, behaviorName).then(setNoteFeatures);
            })}>
              <FileText size={18} /> Extrair anotacao
            </button>
            <button disabled={busy || !patientId} onClick={() => run("Extraindo anotacoes do paciente...", () => api.batchExtractNotes(patientId), () => {
              if (patientId && behaviorName) api.noteFeatures(patientId, behaviorName).then(setNoteFeatures);
            })}>
              <RefreshCw size={18} /> Extrair lote
            </button>
            <button disabled={busy || !appointmentId || !noteExtraction} onClick={() => noteExtraction && run("Confirmando extracao...", () => api.confirmNoteExtraction(appointmentId, noteExtraction), setNoteExtraction)}>
              <ClipboardCheck size={18} /> Confirmar revisao
            </button>
          </div>
          {noteExtraction ? (
            <>
              <div className="noteGrid">
                <Metric label="Confianca" value={`${Math.round(noteExtraction.extraction_confidence * 100)}%`} />
                <Metric label="Revisao humana" value={noteExtraction.requires_human_review ? "necessaria" : "baixa prioridade"} />
                <Metric label="Features textuais" value={String(noteFeatures?.samples ?? 0)} />
              </div>
              <table className="noteTable">
                <thead>
                  <tr>
                    <th>Comportamento extraido</th>
                    <th>Ocorrencia</th>
                    <th>Frequencia</th>
                    <th>Intensidade</th>
                  </tr>
                </thead>
                <tbody>
                  {noteExtraction.extracted_behaviors.map((behavior) => (
                    <tr key={behavior.behavior_name}>
                      <td>{behavior.behavior_name}</td>
                      <td>{behavior.occurred === null ? "ambiguo" : behavior.occurred ? "sim" : "nao"}</td>
                      <td>{behavior.frequency ?? noteExtraction.extracted_frequency ?? "-"}</td>
                      <td>{behavior.intensity ?? noteExtraction.extracted_intensity ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted">
                Antecedentes: {noteExtraction.extracted_antecedents.join(", ") || "-"} | Consequencias: {noteExtraction.extracted_consequences.join(", ") || "-"} | Contexto: {noteExtraction.extracted_context.join(", ") || "-"}
              </p>
            </>
          ) : (
            <p className="muted">Selecione um atendimento e extraia a anotacao para gerar sinais textuais usados no modelo.</p>
          )}
        </article>

        <article className="panel wide">
          <div className="panelHeader">
            <ShieldCheck size={20} />
            <h2>ABC fechado</h2>
          </div>
          <div className="noteActions">
            <button disabled={busy} onClick={() => run("Calculando associacoes ABC...", () => api.abcAssociations(patientId), setAbcAssociations)}>
              <Activity size={18} /> Analisar ABC
            </button>
          </div>
          <p className="warningLine">As associacoes apresentadas nao confirmam causa ou funcao comportamental e precisam ser interpretadas por profissional qualificado.</p>
          {abcAssociations ? (
            <>
              <p className="muted">{abcAssociations.titulo}: X = {abcAssociations.eixo_x}; Y = {abcAssociations.eixo_y}; tamanho = {abcAssociations.tamanho_ponto}.</p>
              <table className="abcTable">
                <thead>
                  <tr>
                    <th>Associacao descritiva</th>
                    <th>P cond.</th>
                    <th>Lift</th>
                    <th>OR</th>
                    <th>Phi</th>
                    <th>n</th>
                    <th>Qualidade</th>
                  </tr>
                </thead>
                <tbody>
                  {abcAssociations.antecedente_comportamento.slice(0, 8).map((item) => (
                    <tr key={`${item.antecedente?.codigo}-${item.comportamento?.codigo}`}>
                      <td>{item.antecedente?.nome} {"->"} {item.comportamento?.nome}</td>
                      <td>{formatPercent(item.probabilidade_condicional)}</td>
                      <td>{formatMetric(item.lift)}</td>
                      <td>{formatMetric(item.odds_ratio)}</td>
                      <td>{formatMetric(item.phi)}</td>
                      <td>{item.intervalos_observados}</td>
                      <td>{item.qualidade_estimativa}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="muted">Registre intervalos ABC ou consulte dados existentes para visualizar associacoes descritivas.</p>
          )}
        </article>

        <article className="panel wide">
          <div className="panelHeader">
            <FileDown size={20} />
            <h2>Historico de previsoes</h2>
          </div>
          <table>
            <thead>
              <tr>
                <th>Paciente</th>
                <th>Comportamento</th>
                <th>Probabilidade</th>
                <th>Risco</th>
                <th>Modelo</th>
              </tr>
            </thead>
            <tbody>
              {history.slice(-8).reverse().map((item, index) => (
                <tr key={`${item.created_at}-${index}`}>
                  <td>{item.patient_id}</td>
                  <td>{item.behavior_name}</td>
                  <td>{Math.round(item.probability * 100)}%</td>
                  <td><RiskBadge risk={item.risk} /></td>
                  <td>{item.model}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatMetric(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function formatPercent(value: unknown) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-";
}

function formatUncertainty(value: Prediction["uncertainty"]) {
  if (!value || typeof value.lower !== "number" || typeof value.upper !== "number") return "-";
  return `${Math.round(value.lower * 100)}-${Math.round(value.upper * 100)}%`;
}

createRoot(document.getElementById("root")!).render(<App />);

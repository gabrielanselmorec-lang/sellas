export type Patient = {
  patient_id: string;
};

export type Behavior = {
  behavior_name: string;
};

export type BehaviorRecord = {
  patient_id: string;
  session_id: string;
  date: string;
  behavior_name: string;
  frequency: number;
  duration: number;
  intensity: number;
  antecedent?: string;
  consequence?: string;
  hypothesized_function?: string;
  environment?: string;
};

export type Appointment = {
  appointment_id: string;
  patient_id?: string;
  appointment_date?: string;
  raw_note_text?: string;
  notes?: string;
  behaviors?: string[];
  record_count?: number;
};

export type ExtractedBehavior = {
  behavior_name: string;
  occurred: boolean | null;
  possible_behavior?: boolean;
  frequency?: number | null;
  intensity?: number | null;
  duration_minutes?: number | null;
  evidence?: string[];
};

export type NoteExtraction = {
  patient_id?: string;
  appointment_id?: string;
  appointment_date?: string;
  raw_note_text?: string;
  cleaned_note_text?: string;
  extracted_behaviors: ExtractedBehavior[];
  binary_occurrences: Record<string, number>;
  extracted_antecedents: string[];
  extracted_consequences: string[];
  extracted_context: string[];
  extracted_intensity?: number | null;
  extracted_frequency?: number | null;
  extracted_duration?: number | null;
  extraction_confidence: number;
  extraction_method: string;
  requires_human_review: boolean;
  human_confirmed: boolean;
  human_corrected: boolean;
};

export type NoteFeaturesResponse = {
  patient_id: string;
  behavior_name: string;
  samples: number;
  features: Array<Record<string, string | number | boolean | null>>;
};

export type ABCAssociationMetric = {
  antecedente?: { codigo: string; nome: string };
  comportamento?: { codigo: string; nome: string };
  consequencia?: { codigo: string; nome: string };
  intervalos_observados: number;
  intervalos_com_ambos: number;
  probabilidade_baseline: number | null;
  probabilidade_condicional: number | null;
  diferenca_risco: number | null;
  lift: number | null;
  odds_ratio: number | null;
  phi: number | null;
  intervalo_confianca: { inferior: number | null; superior: number | null };
  qualidade_estimativa: string;
  interpretacao: string;
};

export type ABCAssociationsResponse = {
  titulo: string;
  eixo_x: string;
  eixo_y: string;
  tamanho_ponto: string;
  aviso: string;
  antecedente_comportamento: ABCAssociationMetric[];
  comportamento_consequencia: ABCAssociationMetric[];
};

export type Prediction = {
  patient_id: string | null;
  patient_token?: string | null;
  behavior_name: string;
  behavior_code?: string;
  landmark_ts?: string | null;
  prediction_window: string;
  horizon?: string;
  horizon_sessions?: number;
  target_definition?: Record<string, unknown>;
  probability: number;
  risk_probability?: number;
  baseline_probability?: number;
  personal_baseline?: number;
  uncertainty?: { type?: string; lower?: number; upper?: number; samples?: number };
  data_quality?: "adequate" | "limited" | "poor" | string;
  abstain?: boolean;
  abstain_reason?: string | null;
  risk: "baixo" | "moderado" | "alto";
  model: string;
  model_version_id?: string;
  model_version?: string;
  clinical_plan_ref?: string | null;
  audit_id?: string;
  calibration?: Record<string, unknown>;
  trained_at?: string;
  top_factors: Array<{ factor: string; value: string | number }>;
  associated_factors?: Array<{ factor: string; value: string | number }>;
  governance?: Record<string, unknown>;
  clinical_factor_summary?: string;
  clinical_disclaimer: string;
  created_at?: string;
};

export type TrainResult = {
  model_version_id?: string;
  selected_model: string;
  samples: number;
  event_rate: number;
  baseline_probability?: number;
  calibration?: Record<string, unknown>;
  target_definition?: Record<string, unknown>;
  feature_importance?: Array<Record<string, unknown>>;
  metrics: Record<string, Record<string, number | number[][] | null>>;
};

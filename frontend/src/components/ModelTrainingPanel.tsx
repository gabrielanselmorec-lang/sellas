import { Wand2 } from "lucide-react";

export function ModelTrainingPanel({ disabled, onTrain }: { disabled: boolean; onTrain: () => void }) {
  return (
    <button disabled={disabled} onClick={onTrain}>
      <Wand2 size={18} /> Treinar
    </button>
  );
}

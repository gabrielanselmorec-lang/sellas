import type { Behavior } from "../types/api";

export function BehaviorSelector({
  behaviors,
  value,
  onChange
}: {
  behaviors: Behavior[];
  value: string;
  onChange: (behaviorName: string) => void;
}) {
  return (
    <label>
      Comportamento
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {behaviors.map((behavior) => <option key={behavior.behavior_name}>{behavior.behavior_name}</option>)}
      </select>
    </label>
  );
}

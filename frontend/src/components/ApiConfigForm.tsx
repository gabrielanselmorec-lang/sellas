import { useState } from "react";

export function ApiConfigForm({ onSubmit }: { onSubmit: (payload: { base_url: string; api_token: string; use_mock: boolean }) => void }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [useMock, setUseMock] = useState(true);

  return (
    <form className="configForm" onSubmit={(event) => {
      event.preventDefault();
      onSubmit({ base_url: baseUrl, api_token: apiToken, use_mock: useMock });
    }}>
      <label>
        Base URL
        <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
      </label>
      <label>
        Token
        <input type="password" value={apiToken} onChange={(event) => setApiToken(event.target.value)} />
      </label>
      <label className="checkboxLine">
        <input type="checkbox" checked={useMock} onChange={(event) => setUseMock(event.target.checked)} />
        Usar mock
      </label>
      <button>Salvar</button>
    </form>
  );
}

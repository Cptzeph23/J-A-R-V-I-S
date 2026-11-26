import { useState } from "react";
import type { FormEvent } from "react";
import { asApiError } from "../../hooks/useApi";
import { useAuth } from "../../auth/AuthProvider";
import { ErrorNotice } from "../../components/Card";

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ReturnType<typeof asApiError> | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    login(email.trim(), password).catch((e: unknown) => {
      setError(asApiError(e));
      setBusy(false);
    });
  };

  return (
    <main className="login">
      <h1>J.A.R.V.I.S</h1>
      <form onSubmit={submit} aria-label="Sign in">
        <label>
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => { setEmail(e.target.value); }} />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => { setPassword(e.target.value); }} />
        </label>
        {error ? <ErrorNotice error={error} /> : null}
        <button type="submit" disabled={busy}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}

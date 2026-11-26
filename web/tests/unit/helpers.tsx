import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { ApiClient } from "../../src/api/client";
import { AuthProvider } from "../../src/auth/AuthProvider";
import { TokenStore } from "../../src/auth/tokenStore";

export const BASE = "http://jarvis.test/api/v1";

export function signedInClient(): ApiClient {
  const store = new TokenStore();
  store.set({
    access_token: "access-1",
    access_expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    refresh_token: "refresh-1",
  });
  return new ApiClient({ store, baseUrl: BASE });
}

export function anonymousClient(): ApiClient {
  return new ApiClient({ store: new TokenStore(), baseUrl: BASE });
}

export function renderWithApp(ui: ReactElement, client: ApiClient = signedInClient(), path = "/") {
  return {
    client,
    ...render(
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider client={client}>{ui}</AuthProvider>
      </MemoryRouter>,
    ),
  };
}

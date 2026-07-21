// auth.ts — Token storage and authed fetch wrapper.
//
// Token is stored in localStorage so the user stays logged in across
// page refreshes and tab switches. The `authedFetch` wrapper adds the
// `Authorization: Bearer <token>` header to every request and handles
// 401 by clearing the token (forcing re-login).

const TOKEN_KEY = "policy_app_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore quota errors */
  }
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export interface AuthedFetchOptions extends RequestInit {
  /** When true, do NOT add the Authorization header. Used for login. */
  skipAuth?: boolean;
}

export async function authedFetch(
  url: string,
  opts: AuthedFetchOptions = {}
): Promise<Response> {
  const headers = new Headers(opts.headers ?? {});
  if (!opts.skipAuth) {
    const token = getToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }
  const res = await fetch(url, { ...opts, headers });
  // Token expired or invalid → clear and let the caller redirect.
  if (res.status === 401 && !opts.skipAuth) {
    clearToken();
  }
  return res;
}

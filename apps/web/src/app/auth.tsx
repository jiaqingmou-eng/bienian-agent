import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, ApiError, describeError } from '../api/client';
import type { User } from '../api/types';

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string | null;
  login: (accountKey: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const expired = () => setUser(null);
    window.addEventListener('auth-expired', expired);
    void api<User>('/me').then((result) => { if (active) setUser(result); })
      .catch((err: unknown) => {
        if (active && !(err instanceof ApiError && err.status === 401)) setError(describeError(err));
      }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; window.removeEventListener('auth-expired', expired); };
  }, []);

  async function login(accountKey: string) {
    const result = await api<User>('/auth/demo-login', {
      method: 'POST', body: JSON.stringify({ account_key: accountKey }),
    });
    setError(null);
    setUser(result);
  }

  async function logout() {
    await api<void>('/auth/logout', { method: 'POST' });
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, error, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth requires AuthProvider');
  return value;
}

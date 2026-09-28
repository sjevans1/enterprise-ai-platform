import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import api from '../services/api'
import type { LoginRequest, LoginResponse, User, BootstrapStatus } from '../types'

interface AuthContextType {
  user: User | null
  loading: boolean
  login: (data: LoginRequest) => Promise<void>
  logout: () => void
  checkBootstrap: () => Promise<BootstrapStatus>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = localStorage.getItem('access_token')
    if (token) {
      api.get<User>('/auth/me')
        .then((res) => setUser(res.data))
        .catch(() => {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          localStorage.removeItem('conversationId')
        })
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }

    const handleLogout = () => {
      setUser(null)
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('conversationId')
    }
    window.addEventListener('auth:logout', handleLogout)
    return () => window.removeEventListener('auth:logout', handleLogout)
  }, [])

  const login = async (data: LoginRequest) => {
    const res = await api.post<LoginResponse>('/auth/login', data)
    localStorage.setItem('access_token', res.data.access_token)
    localStorage.setItem('refresh_token', res.data.refresh_token)
    localStorage.removeItem('conversationId')
    setUser(res.data.user)
  }

  const logout = () => {
    api.post('/auth/logout', {
      refresh_token: localStorage.getItem('refresh_token'),
    }).catch(() => {})
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('conversationId')
    setUser(null)
  }

  const checkBootstrap = async (): Promise<BootstrapStatus> => {
    const res = await api.get<BootstrapStatus>('/bootstrap/status')
    return res.data
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, checkBootstrap }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

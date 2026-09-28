import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../services/api'
import { Shield } from 'lucide-react'

export default function Bootstrap() {
  const [token, setToken] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const fetchToken = async () => {
    setLoading(true)
    try {
      const res = await api.post('/bootstrap/token')
      setToken(res.data.token || res.data.bootstrap_token || '')
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to fetch bootstrap token')
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      setError('')
      await api.post('/bootstrap/admin', {
        token,
        email,
        password,
        full_name: fullName,
      })
      setSuccess(true)
      setTimeout(() => navigate('/login'), 2000)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Bootstrap failed')
    } finally {
      setLoading(false)
    }
  }

  if (success) {
    return (
      <div className="h-screen w-full flex items-center justify-center bg-gray-50">
        <div className="bg-white p-8 rounded-lg shadow border border-gray-200 w-96 text-center">
          <div className="text-green-600 mb-4">
            <Shield className="w-8 h-8 mx-auto" />
          </div>
          <h2 className="text-xl font-bold mb-2">Admin Created</h2>
          <p className="text-gray-600 text-sm">Redirecting to login…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen w-full flex items-center justify-center bg-gray-50">
      <div className="bg-white p-8 rounded-lg shadow border border-gray-200 w-96">
        <div className="flex items-center gap-2 mb-6">
          <Shield className="w-5 h-5 text-primary" />
          <h1 className="text-xl font-bold">First-Time Setup</h1>
        </div>

        <p className="text-sm text-gray-600 mb-4">
          Create the first administrator account. You need the bootstrap token
          to proceed.
        </p>

        {!token && (
          <button
            onClick={fetchToken}
            disabled={loading}
            className="w-full px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover text-sm font-medium mb-4"
          >
            {loading ? 'Fetching…' : 'Get Bootstrap Token'}
          </button>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Bootstrap Token
            </label>
            <input
              type="text"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Full Name
            </label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Password (min 8 characters)
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover text-sm font-medium"
          >
            {loading ? 'Creating…' : 'Create Admin'}
          </button>
        </form>
      </div>
    </div>
  )
}

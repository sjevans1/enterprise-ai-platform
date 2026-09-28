import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../stores/auth'
import { LogIn, Shield } from 'lucide-react'

export default function Login() {
  const [email, setEmail] = useState('admin@enterprise-ai.com')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [showBootstrap, setShowBootstrap] = useState(false)
  const { login, checkBootstrap } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    checkBootstrap().then((status) => {
      if (status.bootstrap_required) {
        setShowBootstrap(true)
      }
    })
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      setError('')
      await login({ email, password })
      navigate('/')
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Login failed')
    }
  }

  if (showBootstrap) {
    return (
      <div className="h-screen w-full flex items-center justify-center bg-gray-50">
        <div className="bg-white p-8 rounded-lg shadow border border-gray-200 w-96">
          <div className="flex items-center gap-2 mb-4 text-blue-600">
            <Shield className="w-5 h-5" />
            <h1 className="text-xl font-bold">Bootstrap Required</h1>
          </div>
          <p className="text-sm text-gray-600 mb-4">
            No admin user has been created. Go to /bootstrap to set up the first
            administrator account.
          </p>
          <button
            onClick={() => navigate('/bootstrap')}
            className="w-full px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover text-sm font-medium"
          >
            Go to Bootstrap
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen w-full flex items-center justify-center bg-gray-50">
      <div className="bg-white p-8 rounded-lg shadow border border-gray-200 w-96">
        <div className="flex items-center gap-2 mb-6">
          <LogIn className="w-5 h-5 text-primary" />
          <h1 className="text-xl font-bold">Enterprise AI Platform</h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              required
              minLength={8}
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            className="w-full px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover text-sm font-medium"
          >
            Sign In
          </button>
        </form>
      </div>
    </div>
  )
}

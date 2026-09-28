import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../services/api'
import type { Job, CreateJobRequest } from '../types'
import { Play, RefreshCw, Trash2, Briefcase } from 'lucide-react'

export default function Jobs() {
  const [jobType, setJobType] = useState<CreateJobRequest['job_type']>('report_generation')
  const [payload, setPayload] = useState('')
  const queryClient = useQueryClient()

  const { data: jobs, isLoading } = useQuery({
    queryKey: ['jobs'],
    queryFn: async () => {
      const res = await api.get('/jobs/')
      return (Array.isArray(res.data) ? res.data : res.data.jobs || []) as Job[]
    },
  })

  const createMutation = useMutation({
    mutationFn: async (data: CreateJobRequest) => {
      const res = await api.post('/jobs/', data)
      return res.data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (jobId: string) => {
      await api.delete(`/jobs/${jobId}`)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })

  const handleSubmit = () => {
    const payloadObj = payload ? JSON.parse(payload) : {}
    createMutation.mutate({
      job_type: jobType,
      payload: payloadObj,
      priority: 1,
    })
    setPayload('')
  }

  const statusClass = (status: string) => {
    if (status === 'completed') return 'bg-green-100 text-green-800'
    if (status === 'failed') return 'bg-red-100 text-red-800'
    if (status === 'running') return 'bg-blue-100 text-blue-800'
    if (status === 'cancelled') return 'bg-gray-100 text-gray-800'
    return 'bg-yellow-100 text-yellow-800'
  }

  return (
    <div className="p-6 h-full overflow-y-auto">
      <div className="max-w-5xl mx-auto">
        {/* Submit job */}
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <Play className="w-5 h-5" />
            Submit Job
          </h2>
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Job Type
              </label>
              <select
                value={jobType}
                onChange={(e) => setJobType(e.target.value as CreateJobRequest['job_type'])}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="report_generation">Report Generation</option>
                <option value="document_ingest">Document Ingest</option>
              </select>
            </div>
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Payload (JSON)
              </label>
              <input
                type="text"
                value={payload}
                onChange={(e) => setPayload(e.target.value)}
                placeholder='{"name":"Sales Report","format":"markdown"}'
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <button
              onClick={handleSubmit}
              disabled={createMutation.isPending}
              className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover disabled:opacity-50 text-sm flex items-center gap-2"
            >
              <Play className="w-4 h-4" />
              Submit
            </button>
          </div>
        </div>

        {/* Jobs list */}
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="p-4 border-b border-gray-200 flex justify-between items-center">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Briefcase className="w-5 h-5" />
              Job Queue ({jobs?.length || 0})
            </h2>
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey: ['jobs'] })}
              className="p-2 text-gray-500 hover:bg-gray-100 rounded-lg"
              title="Refresh"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>

          {isLoading ? (
            <div className="p-4 text-gray-500">Loading jobs…</div>
          ) : !jobs || jobs.length === 0 ? (
            <div className="p-4 text-gray-500">No jobs in queue.</div>
          ) : (
            <div className="divide-y divide-gray-200">
              {jobs.map((job) => (
                <div key={job.id} className="p-4">
                  <div className="flex justify-between items-start">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{job.job_type}</span>
                        <span className={`px-2 py-0.5 rounded text-xs ${statusClass(job.status)}`}>
                          {job.status}
                        </span>
                        <span className="text-xs text-gray-400">attempts: {job.attempts}/{job.max_retries}</span>
                      </div>
                      {job.result && (
                        <pre className="mt-2 text-xs bg-gray-50 p-2 rounded overflow-x-auto">
                          {JSON.stringify(job.result, null, 2)}
                        </pre>
                      )}
                      {job.error_message && (
                        <p className="mt-1 text-sm text-red-600">{job.error_message}</p>
                      )}
                      <div className="text-xs text-gray-400 mt-1">
                        Created: {new Date(job.created_at).toLocaleString()}
                        {job.completed_at && ` · Completed: ${new Date(job.completed_at).toLocaleString()}`}
                      </div>
                    </div>
                    <button
                      onClick={() => deleteMutation.mutate(job.id)}
                      className="ml-2 p-2 text-red-600 hover:bg-red-50 rounded-lg"
                      title="Delete job"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

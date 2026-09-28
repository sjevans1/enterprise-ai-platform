import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../services/api'
import type { Document, CreateJobRequest, RetrievalResult } from '../types'
import { Upload, Search, FileText, Trash2, Database } from 'lucide-react'

export default function Documents() {
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [retrievalResults, setRetrievalResults] = useState<RetrievalResult[]>([])
  const queryClient = useQueryClient()

  const { data: documents, isLoading } = useQuery({
    queryKey: ['documents'],
    queryFn: async () => {
      const res = await api.get<{ documents: Document[] }>('/documents/')
      return res.data.documents || res.data
    },
  })

  const { data: jobs } = useQuery({
    queryKey: ['jobs'],
    queryFn: async () => {
      const res = await api.get('/jobs/')
      return Array.isArray(res.data) ? res.data : res.data.jobs || []
    },
  })

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData()
      formData.append('request_file', file)
      const res = await api.post('/documents/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      return res.data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      setUploadFile(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (docId: string) => {
      await api.delete(`/documents/${docId}`)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })

  const handleUpload = () => {
    if (uploadFile) uploadMutation.mutate(uploadFile)
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    const res = await api.post('/documents/retrieve', {
      query: searchQuery,
      top_k: 5,
      fetch_k: 20,
    })
    setRetrievalResults((res.data as { results: RetrievalResult[] }).results || [])
  }

  const handleIngestJob = (docId: string) => {
    const payload: CreateJobRequest = {
      job_type: 'document_ingest',
      payload: { document_id: docId },
      priority: 1,
    }
    api.post('/jobs/', payload).then(() => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    })
  }

  return (
    <div className="p-6 h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto">
        {/* Upload section */}
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h2 className="text-lg font-semibold mb-2 flex items-center gap-2">
            <Upload className="w-5 h-5" />
            Upload Document
          </h2>
          <div className="flex gap-3 items-center">
            <input
              type="file"
              accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.html"
              onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
              className="text-sm text-gray-600 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary file:text-white hover:file:bg-primary-hover"
            />
            {uploadFile && (
              <span className="text-sm text-gray-600">{uploadFile.name}</span>
            )}
            <button
              onClick={handleUpload}
              disabled={!uploadFile || uploadMutation.isPending}
              className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover disabled:opacity-50 text-sm flex items-center gap-2"
            >
              Upload
            </button>
          </div>
        </div>

        {/* Search section */}
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h2 className="text-lg font-semibold mb-2 flex items-center gap-2">
            <Search className="w-5 h-5" />
            Search Documents
          </h2>
          <div className="flex gap-2">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Enter search query..."
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
            <button
              onClick={handleSearch}
              className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover text-sm"
            >
              Search
            </button>
          </div>

          {retrievalResults.length > 0 && (
            <div className="mt-3 space-y-3">
              <h3 className="text-sm font-medium text-gray-700">
                {retrievalResults.length} results found
              </h3>
              {retrievalResults.map((res) => (
                <div key={res.chunk_id} className="border border-gray-200 rounded-lg p-3">
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>Relevance: {res.relevance_score.toFixed(3)}</span>
                    <span>MMR: {res.mmr_score.toFixed(3)}</span>
                  </div>
                  <p className="text-sm text-gray-800">{res.content}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Documents list */}
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="p-4 border-b border-gray-200 flex justify-between items-center">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <FileText className="w-5 h-5" />
              Documents ({documents?.length || 0})
            </h2>
          </div>
          {isLoading ? (
            <div className="p-4 text-gray-500">Loading…</div>
          ) : !documents || documents.length === 0 ? (
            <div className="p-4 text-gray-500">No documents uploaded yet.</div>
          ) : (
            <div className="divide-y divide-gray-200">
              {documents.map((doc: Document) => (
                <div key={doc.id} className="p-4 flex justify-between items-center">
                  <div className="flex-1">
                    <div className="font-medium">{doc.filename}</div>
                    <div className="text-sm text-gray-500 flex gap-4 mt-1">
                      <span>{doc.file_size} bytes</span>
                      <span className={doc.is_indexed ? 'text-green-600' : 'text-yellow-600'}>
                        {doc.is_indexed ? 'Indexed' : 'Processing'}
                      </span>
                      <span>{doc.chunk_count} chunks</span>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {!doc.is_indexed && (
                      <button
                        onClick={() => handleIngestJob(doc.id)}
                        className="p-2 text-sm bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100"
                      >
                        <Database className="w-4 h-4" />
                      </button>
                    )}
                    <button
                      onClick={() => deleteMutation.mutate(doc.id)}
                      className="p-2 text-sm bg-red-50 text-red-600 rounded-lg hover:bg-red-100"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Jobs list */}
        {jobs && jobs.length > 0 && (
          <div className="bg-white rounded-lg border border-gray-200 mt-4">
            <div className="p-4 border-b border-gray-200">
              <h2 className="text-lg font-semibold">Recent Jobs ({jobs.length})</h2>
            </div>
            <div className="divide-y divide-gray-200">
              {jobs.slice(0, 10).map((job: any) => (
                <div key={job.id || job.job_id} className="p-3 flex justify-between text-sm">
                  <div>
                    <span className="font-medium">{job.job_type}</span>
                    <span className={`ml-2 px-2 py-0.5 rounded text-xs ${
                      job.status === 'completed' ? 'bg-green-100 text-green-800' :
                      job.status === 'failed' ? 'bg-red-100 text-red-800' :
                      job.status === 'running' ? 'bg-blue-100 text-blue-800' :
                      'bg-gray-100 text-gray-800'
                    }`}>
                      {job.status}
                    </span>
                  </div>
                  <div className="text-gray-500">attempts: {job.attempts}/{job.max_retries}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

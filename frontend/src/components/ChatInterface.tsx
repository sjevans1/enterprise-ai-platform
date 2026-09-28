import { useState, useRef, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../services/api'
import type { ChatMessage, ChatResponse } from '../types'
import { Send, Loader2, Copy } from 'lucide-react'

export default function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [conversationId, setConversationId] = useState<string | null>(() => {
    return localStorage.getItem('conversationId')
  })
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // Persist conversationId to localStorage
  useEffect(() => {
    if (conversationId) {
      localStorage.setItem('conversationId', conversationId)
    }
  }, [conversationId])

  const sendMessage = useMutation({
    mutationFn: async (msg: string) => {
      const res = await api.post<ChatResponse>('/chat', {
        messages: [...messages, { role: 'user', content: msg }],
        stream: false,
        conversation_id: conversationId,
      })
      return res.data
    },
    onMutate: async (msg: string) => {
      const userMsg: ChatMessage = { role: 'user', content: msg }
      setMessages((prev) => [...prev, userMsg])
      setInput('')
    },
    onSuccess: (data) => {
      // Store conversation_id from response
      if (data.conversation_id) {
        setConversationId(data.conversation_id)
      }
      const assistantMsg: ChatMessage = {
        role: 'assistant',
        content: data.answer.final_answer || data.message.content,
      }
      setMessages((prev) => [...prev, assistantMsg])
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || sendMessage.isPending) return
    sendMessage.mutate(input.trim())
  }

  const handleCopy = (content: string) => {
    navigator.clipboard.writeText(content)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.length === 0 ? (
          <div className="text-center text-gray-500 mt-12">
            <p className="text-lg">Ask a question to get started.</p>
            <p className="text-sm mt-2">
              The assistant can answer questions about your documents and data.
            </p>
          </div>
        ) : (
          messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-3xl rounded-lg px-4 py-3 ${
                  msg.role === 'user'
                    ? 'bg-primary text-white'
                    : 'bg-white border border-gray-200'
                }`}
              >
                <div className="whitespace-pre-wrap text-sm leading-relaxed">{msg.content}</div>
                {msg.role === 'assistant' && (
                  <button
                    onClick={() => handleCopy(msg.content)}
                    className="mt-2 text-xs opacity-50 hover:opacity-100 transition-opacity"
                    title="Copy response"
                  >
                    <Copy className="w-3 h-3 inline" />
                  </button>
                )}
              </div>
            </div>
          ))
        )}

        {sendMessage.isPending && (
          <div className="flex justify-start">
            <div className="bg-white border border-gray-200 rounded-lg px-4 py-3">
              <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 p-4">
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question..."
            disabled={sendMessage.isPending}
            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent text-sm"
            maxLength={2000}
          />
          <button
            type="submit"
            disabled={!input.trim() || sendMessage.isPending}
            className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary-hover disabled:opacity-50 transition-colors flex items-center gap-2"
          >
            {sendMessage.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
            Send
          </button>
        </form>
      </div>
    </div>
  )
}

import { useState, useRef, useEffect } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { api } from '../../api/client';
import { t } from '../../utils/i18n';
import type { AISource, RAGDebug } from '../../types';

interface AIChatPanelProps {
  onClose: () => void;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  sources?: AISource[];
}

export function AIChatPanel({ onClose }: AIChatPanelProps) {
  const currentChat = useChatStore(s => s.currentChat);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [ragDebug, setRagDebug] = useState<RAGDebug | null>(null);
  const [showDebug, setShowDebug] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || !currentChat || loading) return;
    const question = input.trim();
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: question }]);
    setLoading(true);

    const history = messages.map(m => ({ role: m.role, content: m.content }));

    try {
      // Try streaming first
      const res = await api.askAIStream({ chat: currentChat, question, history });
      if (!res.ok) throw new Error('Stream failed');

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let fullAnswer = '';
      let metadata: { sources?: AISource[]; debug?: RAGDebug } | null = null;

      // Add empty assistant message
      setMessages(prev => [...prev, { role: 'assistant', content: '' }]);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value, { stream: true });
        for (const line of text.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;

          if (!metadata) {
            try {
              const parsed = JSON.parse(data);
              if (parsed.type === 'metadata') {
                metadata = parsed;
                if (parsed.debug) setRagDebug(parsed.debug);
                continue;
              }
            } catch { /* not JSON, it's a text chunk */ }
          }

          fullAnswer += data;
          setMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = {
              role: 'assistant',
              content: fullAnswer,
              sources: metadata?.sources,
            };
            return updated;
          });
        }
      }
    } catch {
      // Fallback to non-streaming
      try {
        const data = await api.askAI({ chat: currentChat, question, history });
        if (data.debug) setRagDebug(data.debug);
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.answer,
          sources: data.sources,
        }]);
      } catch (e) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `\u274C ${e instanceof Error ? e.message : t('error')}`,
        }]);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed bottom-24 end-6 w-96 max-h-[70vh] bg-white rounded-2xl shadow-2xl border border-gray-200 flex flex-col z-50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 bg-gradient-to-r from-[#0D9488] to-[#6366F1] text-white">
        <span className="text-lg">{t('aiTitle')}</span>
        <button onClick={onClose} className="ms-auto text-white/70 hover:text-white text-lg">\u2715</button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[200px]">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 text-sm py-8">
            {t('aiPlaceholder')}
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] px-3 py-2 rounded-xl text-sm ${
              msg.role === 'user'
                ? 'bg-[#0D9488] text-white'
                : 'bg-gray-100 text-gray-800'
            }`}>
              <div className="whitespace-pre-wrap">{msg.content}</div>
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-2 pt-2 border-t border-gray-200/50">
                  <div className="text-xs opacity-70 mb-1">{t('sources')}:</div>
                  {msg.sources.slice(0, 5).map((s, j) => (
                    <div key={j} className="text-xs opacity-60">
                      #{s.message_id} {s.sender} — {s.preview.slice(0, 40)}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 px-3 py-2 rounded-xl text-sm text-gray-400">
              {t('thinking')} <span className="animate-pulse">\u25CF\u25CF\u25CF</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* RAG Debug button */}
      {ragDebug && (
        <div className="px-4 pb-1">
          <button
            onClick={() => setShowDebug(!showDebug)}
            className="text-xs text-gray-400 hover:text-[#0D9488] transition-colors"
          >
            {t('whyAnswer')}
          </button>
          {showDebug && (
            <div className="mt-2 p-3 bg-gray-50 rounded-lg text-xs space-y-2 max-h-48 overflow-y-auto">
              <div className="font-bold">{t('ragDebugTitle')}</div>
              <div className="text-gray-500">{ragDebug.chunks_retrieved} {t('ragChunks')}</div>
              {ragDebug.chunks_detail.map((chunk, i) => (
                <div key={i} className="p-2 bg-white rounded border-s-2 border-[#0D9488]">
                  <div className="flex justify-between">
                    <span className="font-bold">{t('chunk')} #{i + 1}</span>
                    <span className="text-[#0D9488] font-bold">{t('score')}: {chunk.score.toFixed(1)}</span>
                  </div>
                  <div className="text-gray-400 mt-1">
                    #{chunk.start_message_id}–#{chunk.end_message_id} | {chunk.senders}
                  </div>
                  <div className="text-gray-500 mt-1 line-clamp-2">{chunk.preview}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Input */}
      <div className="p-3 border-t border-gray-200">
        <form onSubmit={e => { e.preventDefault(); sendMessage(); }} className="flex gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={t('aiPlaceholder')}
            className="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#0D9488]"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="px-4 py-2 bg-[#0D9488] text-white rounded-lg text-sm font-semibold disabled:opacity-50"
          >
            {t('send')}
          </button>
        </form>
      </div>
    </div>
  );
}

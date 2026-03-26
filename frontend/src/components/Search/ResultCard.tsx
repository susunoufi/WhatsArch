import { useState } from 'react';
import { api } from '../../api/client';
import { t } from '../../utils/i18n';
import { senderColor } from '../../utils/formatters';
import type { SearchResult, ContextResponse } from '../../types';

interface ResultCardProps {
  result: SearchResult;
  currentChat: string;
}

export function ResultCard({ result: r, currentChat }: ResultCardProps) {
  const [contextOpen, setContextOpen] = useState(false);
  const [context, setContext] = useState<ContextResponse | null>(null);

  const chat = r.chat_name || currentChat;
  const color = senderColor(r.sender);

  const toggleContext = async () => {
    if (contextOpen) {
      setContextOpen(false);
      return;
    }
    try {
      const data = await api.getContext(r.id, chat);
      setContext(data);
      setContextOpen(true);
    } catch { /* ignore */ }
  };

  // Type badges
  const badges: string[] = [];
  if (r.has_transcription) badges.push('🎙️');
  if (r.has_visual) badges.push('🖼️');
  if (r.has_video_transcription) badges.push('🎬');
  if (r.has_pdf) badges.push('📄');
  if (r.media_type === 'audio') badges.push('🔊');
  else if (r.media_type === 'image') badges.push('📷');
  else if (r.media_type === 'video') badges.push('🎥');

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden hover:shadow-md transition-shadow">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-100">
        <span
          className="px-2 py-0.5 rounded-md text-white text-xs font-bold"
          style={{ backgroundColor: color }}
        >
          {r.sender}
        </span>
        <span className="text-xs text-gray-400">{r.datetime?.slice(0, 16)}</span>
        {badges.map((b, i) => (
          <span key={i} className="text-xs opacity-70">{b}</span>
        ))}
        {r.chat_name && (
          <span className="text-xs px-2 py-0.5 bg-[#6366F1]/10 text-[#6366F1] rounded-md">
            {r.chat_name}
          </span>
        )}
        <button
          onClick={toggleContext}
          className="ms-auto text-xs text-gray-400 hover:text-[#0D9488] transition-colors"
        >
          {t('context')} ↕
        </button>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2">
        {/* Text */}
        {r.text_snippet ? (
          <div className="text-sm" dangerouslySetInnerHTML={{ __html: r.text_snippet }} />
        ) : r.text ? (
          <div className="text-sm">{r.text}</div>
        ) : null}

        {/* Transcription */}
        {(r.transcription_snippet || r.transcription) && (
          <div className="text-xs px-3 py-2 bg-green-50 border-s-2 border-green-400 rounded">
            🎙️ {r.transcription_snippet ? (
              <span dangerouslySetInnerHTML={{ __html: r.transcription_snippet }} />
            ) : r.transcription}
          </div>
        )}

        {/* Visual description */}
        {(r.visual_description_snippet || r.visual_description) && (
          <div className="text-xs px-3 py-2 bg-blue-50 border-s-2 border-blue-400 rounded">
            🖼️ {r.visual_description_snippet ? (
              <span dangerouslySetInnerHTML={{ __html: r.visual_description_snippet }} />
            ) : r.visual_description}
          </div>
        )}

        {/* PDF text */}
        {(r.pdf_text_snippet || r.pdf_text) && (
          <div className="text-xs px-3 py-2 bg-red-50 border-s-2 border-red-400 rounded">
            📄 {r.pdf_text_snippet ? (
              <span dangerouslySetInnerHTML={{ __html: r.pdf_text_snippet }} />
            ) : r.pdf_text?.slice(0, 200)}
          </div>
        )}

        {/* Media */}
        {r.attachment && r.media_type === 'image' && (
          <img
            src={`/media/${chat}/${r.attachment}`}
            alt=""
            className="max-w-xs rounded-lg cursor-pointer hover:opacity-90"
            loading="lazy"
          />
        )}
        {r.attachment && r.media_type === 'audio' && (
          <audio controls className="w-full max-w-sm" preload="none">
            <source src={`/media/${chat}/${r.attachment}`} />
          </audio>
        )}
        {r.attachment && r.media_type === 'video' && (
          <video controls className="max-w-xs rounded-lg" preload="none"
                 poster={`/api/thumbnail/${chat}/${r.attachment}`}>
            <source src={`/media/${chat}/${r.attachment}`} />
          </video>
        )}
      </div>

      {/* Context */}
      {contextOpen && context && (
        <div className="border-t border-gray-100 bg-gray-50 px-4 py-3 space-y-2">
          {context.messages.map(m => (
            <div
              key={m.id}
              className={`text-xs p-2 rounded ${
                m.id === context.focus_id ? 'bg-blue-50 border border-blue-200' : ''
              }`}
            >
              <span className="font-bold" style={{ color: senderColor(m.sender) }}>
                {m.sender}
              </span>
              <span className="text-gray-400 ms-2">{m.datetime?.slice(0, 16)}</span>
              <div className="mt-1">{m.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

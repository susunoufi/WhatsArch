import { useEffect, useState } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { api } from '../../api/client';
import { t } from '../../utils/i18n';
import { formatNumber, formatEta } from '../../utils/formatters';
import type { ProcessingStatus, TaskProgress } from '../../types';

export function ManagementTab() {
  const { chats, currentChat } = useChatStore();
  const [statuses, setStatuses] = useState<Record<string, ProcessingStatus>>({});
  const [activeTask, setActiveTask] = useState<TaskProgress | null>(null);
  const [uploading, setUploading] = useState(false);

  const readyChats = chats.filter(c => c.ready || true); // Show all chats

  useEffect(() => {
    readyChats.forEach(chat => {
      api.getProcessStatus(chat.name)
        .then(s => setStatuses(prev => ({ ...prev, [chat.name]: s })))
        .catch(() => {});
    });
  }, [chats]);

  // Poll progress
  useEffect(() => {
    if (!currentChat || !activeTask) return;
    const interval = setInterval(async () => {
      const progress = await api.getProgress(currentChat);
      if (progress.status === 'idle' || progress.status === 'done' || progress.status === 'error') {
        setActiveTask(null);
        // Reload status
        api.getProcessStatus(currentChat)
          .then(s => setStatuses(prev => ({ ...prev, [currentChat]: s })));
      } else {
        setActiveTask(progress);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [currentChat, activeTask]);

  const startTask = async (chat: string, task: string) => {
    try {
      await api.startProcess(chat, task);
      setActiveTask({ status: 'running', task });
    } catch { /* ignore */ }
  };

  const stopTask = async (chat: string) => {
    await api.stopProcess(chat);
    setActiveTask(null);
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await api.uploadZip(file);
      useChatStore.getState().loadChats();
    } catch { /* ignore */ }
    setUploading(false);
  };

  const TASKS = [
    { id: 'transcribe', labelKey: 'transcribeAudio', icon: '\uD83C\uDF99\uFE0F' },
    { id: 'images', labelKey: 'processImages', icon: '\uD83D\uDDBC\uFE0F' },
    { id: 'videos', labelKey: 'processVideos', icon: '\uD83C\uDFAC' },
    { id: 'pdfs', labelKey: 'extractPdf', icon: '\uD83D\uDCC4' },
    { id: 'index', labelKey: 'updateSearch', icon: '\uD83D\uDD0D' },
    { id: 'embeddings', labelKey: 'smartSearch', icon: '\uD83E\uDDE0' },
  ];

  return (
    <div className="py-4 space-y-4">
      {/* Upload */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <h3 className="font-bold text-sm mb-3">{t('upload')}</h3>
        <p className="text-xs text-gray-500 mb-3">{t('uploadDesc')}</p>
        <label className="inline-flex items-center gap-2 px-4 py-2 bg-[#0D9488] text-white rounded-lg text-sm font-semibold cursor-pointer hover:bg-[#0F766E]">
          <input type="file" accept=".zip" onChange={handleUpload} className="hidden" />
          {uploading ? '...' : t('upload')}
        </label>
      </div>

      {/* Per-chat status */}
      {readyChats.map(chat => {
        const status = statuses[chat.name];
        const isActive = currentChat === chat.name && activeTask;

        return (
          <div key={chat.name} className="bg-white rounded-xl border border-gray-200 p-4">
            <div className="flex items-center gap-2 mb-3">
              <h3 className="font-bold text-sm">{chat.name}</h3>
              {chat.platform && (
                <span className="text-xs px-2 py-0.5 bg-gray-100 rounded">
                  {chat.platform}
                </span>
              )}
              {!chat.ready && (
                <span className="text-xs px-2 py-0.5 bg-amber-100 text-amber-700 rounded">
                  {t('notIndexed')}
                </span>
              )}
              {chat.total_messages && (
                <span className="text-xs text-gray-400 ms-auto">
                  {formatNumber(chat.total_messages)} {t('messages')}
                </span>
              )}
            </div>

            {/* Status bars */}
            {status && (
              <div className="space-y-2 mb-3 text-xs">
                {[
                  { label: '\uD83C\uDF99\uFE0F', total: status.audio.total, done: status.audio.processed },
                  { label: '\uD83D\uDDBC\uFE0F', total: status.images.total, done: status.images.processed },
                  { label: '\uD83C\uDFAC', total: status.videos.total, done: status.videos.described },
                  { label: '\uD83D\uDCC4', total: status.pdfs.total, done: status.pdfs.processed },
                ].map((item, i) => item.total > 0 && (
                  <div key={i} className="flex items-center gap-2">
                    <span>{item.label}</span>
                    <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-[#0D9488] rounded-full transition-all"
                        style={{ width: `${item.total > 0 ? (item.done / item.total) * 100 : 0}%` }}
                      />
                    </div>
                    <span className="text-gray-400 w-16 text-end">{item.done}/{item.total}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Active task progress */}
            {isActive && activeTask && (
              <div className="mb-3 p-2 bg-amber-50 rounded-lg text-xs">
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-amber-500 border-t-transparent rounded-full animate-spin" />
                  <span>{activeTask.task}: {activeTask.processed}/{activeTask.total}</span>
                  {activeTask.eta_seconds != null && activeTask.eta_seconds > 0 && (
                    <span className="text-gray-400">— {t('remaining')} ~{formatEta(activeTask.eta_seconds)}</span>
                  )}
                  <button
                    onClick={() => stopTask(chat.name)}
                    className="ms-auto px-2 py-0.5 bg-red-100 text-red-600 rounded text-xs"
                  >
                    {t('stop')}
                  </button>
                </div>
              </div>
            )}

            {/* Action buttons */}
            <div className="flex flex-wrap gap-2">
              {TASKS.map(task => (
                <button
                  key={task.id}
                  onClick={() => startTask(chat.name, task.id)}
                  disabled={!!isActive}
                  className="px-3 py-1.5 border border-gray-200 rounded-lg text-xs font-medium hover:bg-gray-50 disabled:opacity-40 transition-colors"
                >
                  {task.icon} {t(task.labelKey)}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

import { useEffect, useState } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { api } from '../../api/client';
import { t } from '../../utils/i18n';
import type { MediaFile } from '../../types';

export function GalleryTab() {
  const currentChat = useChatStore(s => s.currentChat);
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState('all');
  const [lightbox, setLightbox] = useState<string | null>(null);

  const load = async (reset = true) => {
    if (!currentChat || currentChat === '__all__') return;
    const p = reset ? 1 : page;
    if (reset) { setPage(1); setFiles([]); }
    const data = await api.getMediaList(currentChat, filter, p);
    setFiles(prev => reset ? data.files : [...prev, ...data.files]);
    setTotal(data.total);
  };

  useEffect(() => { load(true); }, [currentChat, filter]);

  if (!currentChat || currentChat === '__all__') {
    return <div className="py-12 text-center text-gray-400">{t('selectChat')}</div>;
  }

  return (
    <div className="py-4">
      {/* Filters */}
      <div className="flex gap-2 mb-4">
        {['all', 'image', 'video'].map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              filter === f ? 'bg-[#0D9488] text-white' : 'bg-white border border-gray-200 hover:bg-gray-50'
            }`}
          >
            {f === 'all' ? t('all') : f === 'image' ? t('images') : t('videos')}
          </button>
        ))}
        <span className="ms-auto text-sm text-gray-400 self-center">{total} {t('results')}</span>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-2">
        {files.map((file, i) => (
          <div
            key={i}
            className="relative aspect-square bg-gray-100 rounded-lg overflow-hidden cursor-pointer hover:opacity-90 transition-opacity group"
            onClick={() => file.type === 'image' ? setLightbox(file.url) : window.open(file.url, '_blank')}
          >
            <img
              src={file.type === 'image' ? file.url : file.thumbnail_url}
              alt=""
              className="w-full h-full object-cover"
              loading="lazy"
            />
            {file.type === 'video' && (
              <span className="absolute top-2 end-2 bg-black/60 text-white text-xs px-1.5 py-0.5 rounded">\u25B6</span>
            )}
            {file.description && (
              <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/70 to-transparent p-2 text-white text-xs opacity-0 group-hover:opacity-100 transition-opacity">
                {file.description.slice(0, 80)}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Load more */}
      {files.length < total && (
        <div className="text-center py-6">
          <button
            onClick={() => { setPage(p => p + 1); load(false); }}
            className="px-6 py-2 border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50"
          >
            {t('loadMore')}
          </button>
        </div>
      )}

      {/* Lightbox */}
      {lightbox && (
        <div
          className="fixed inset-0 bg-black/80 z-[9999] flex items-center justify-center cursor-pointer"
          onClick={() => setLightbox(null)}
        >
          <img src={lightbox} alt="" className="max-w-[90vw] max-h-[90vh] object-contain" />
        </div>
      )}
    </div>
  );
}

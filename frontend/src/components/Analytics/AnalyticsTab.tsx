import { useEffect, useState } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { api } from '../../api/client';
import { t } from '../../utils/i18n';
import { formatNumber, senderColor } from '../../utils/formatters';
import type { AnalyticsData } from '../../types';

export function AnalyticsTab() {
  const currentChat = useChatStore(s => s.currentChat);
  const [data, setData] = useState<AnalyticsData | null>(null);

  useEffect(() => {
    if (!currentChat || currentChat === '__all__') { setData(null); return; }
    api.getAnalytics(currentChat).then(setData).catch(() => setData(null));
  }, [currentChat]);

  if (!currentChat || currentChat === '__all__') {
    return <div className="py-12 text-center text-gray-400">{t('selectChat')}</div>;
  }
  if (!data) return <div className="py-8 text-center text-gray-400">{t('loading')}</div>;

  const maxSender = data.senders[0]?.count || 1;
  const maxHour = Math.max(...data.hourly.map(h => h.count), 1);

  return (
    <div className="py-4 space-y-6">
      {/* Top Senders */}
      {data.senders.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="font-bold text-sm mb-3">{t('topSenders')}</h3>
          <div className="space-y-2">
            {data.senders.slice(0, 10).map(s => (
              <div key={s.sender} className="flex items-center gap-3 text-sm">
                <span className="w-24 truncate font-medium" style={{ color: senderColor(s.sender) }}>{s.sender}</span>
                <div className="flex-1 h-6 bg-gray-50 rounded overflow-hidden">
                  <div
                    className="h-full rounded bg-gradient-to-l from-[#0D9488] to-[#6366F1] flex items-center px-2"
                    style={{ width: `${(s.count / maxSender) * 100}%` }}
                  >
                    <span className="text-xs text-white font-bold">{formatNumber(s.count)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Hourly Activity */}
      {data.hourly.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="font-bold text-sm mb-3">{t('hourlyActivity')}</h3>
          <div className="flex items-end gap-0.5 h-24">
            {Array.from({ length: 24 }, (_, h) => {
              const entry = data.hourly.find(x => x.hour === h);
              const count = entry?.count || 0;
              const pct = (count / maxHour) * 100;
              return (
                <div
                  key={h}
                  className="flex-1 bg-[#0D9488] rounded-t hover:bg-[#0F766E] transition-colors relative group"
                  style={{ height: `${Math.max(2, pct)}%` }}
                  title={`${h}:00 \u2014 ${formatNumber(count)} ${t('messages')}`}
                >
                  <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 bg-gray-800 text-white text-xs px-2 py-1 rounded whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                    {h}:00 \u2014 {formatNumber(count)}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="flex gap-0.5 mt-1 text-xs text-gray-400">
            {Array.from({ length: 24 }, (_, h) => h % 3 === 0 ? (
              <span key={h} className="flex-1 text-center">{h}</span>
            ) : <span key={h} className="flex-1" />)}
          </div>
        </div>
      )}

      {/* Busiest Days */}
      {data.busiest_days.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="font-bold text-sm mb-3">{t('busiestDays')}</h3>
          <div className="space-y-1">
            {data.busiest_days.map((d, i) => (
              <div key={d.date} className="flex items-center gap-3 py-2 border-b border-gray-50 last:border-0 text-sm">
                <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                  i < 3 ? 'bg-[#0D9488] text-white' : 'bg-gray-100'
                }`}>{i + 1}</span>
                <span>{d.date}</span>
                <span className="ms-auto font-semibold">{formatNumber(d.count)} {t('messages')}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Media Breakdown */}
      {data.media.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="font-bold text-sm mb-3">{t('mediaBreakdown')}</h3>
          <div className="space-y-1">
            {data.media.map(m => (
              <div key={m.type} className="flex items-center gap-3 py-2 text-sm">
                <span>{m.type === 'audio' ? '\uD83C\uDF99\uFE0F' : m.type === 'image' ? '\uD83D\uDCF7' : m.type === 'video' ? '\uD83C\uDFA5' : '\uD83D\uDCC4'}</span>
                <span>{m.type}</span>
                <span className="ms-auto font-semibold">{formatNumber(m.count)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

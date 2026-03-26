import { t } from '../../utils/i18n';
import { formatNumber } from '../../utils/formatters';
import type { ChatStats } from '../../types';

interface StatsBannerProps {
  stats: ChatStats;
}

export function StatsBanner({ stats }: StatsBannerProps) {
  return (
    <div className="flex flex-wrap gap-3 text-sm">
      <div className="px-3 py-1.5 bg-[#0D9488]/10 text-[#0D9488] rounded-lg font-semibold">
        {formatNumber(stats.total_messages)} {t('messages')}
      </div>
      {stats.date_range && (
        <div className="px-3 py-1.5 bg-gray-100 text-gray-600 rounded-lg">
          {stats.date_range.first} — {stats.date_range.last}
        </div>
      )}
      {stats.senders && (
        <div className="px-3 py-1.5 bg-[#6366F1]/10 text-[#6366F1] rounded-lg">
          {Object.keys(stats.senders).length} {t('senders')}
        </div>
      )}
      {stats.chat_type && (
        <div className="px-3 py-1.5 bg-amber-50 text-amber-600 rounded-lg">
          {stats.chat_type === 'group' ? '👥 Group' : '👤 1-on-1'}
        </div>
      )}
    </div>
  );
}

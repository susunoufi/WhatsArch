import { ResultCard } from './ResultCard';
import { t } from '../../utils/i18n';
import { formatNumber } from '../../utils/formatters';
import type { SearchResult } from '../../types';

interface SearchResultsProps {
  results: SearchResult[];
  total: number;
  loading: boolean;
  onLoadMore: () => void;
  query: string;
  currentChat: string;
}

export function SearchResults({ results, total, loading, onLoadMore, query, currentChat }: SearchResultsProps) {
  if (!query) return null;

  const exportUrl = (fmt: string) => {
    const params = new URLSearchParams({ q: query, chat: currentChat || '__all__', format: fmt });
    return `/api/export?${params}`;
  };

  return (
    <div className="space-y-3">
      {/* Results header */}
      {total > 0 && (
        <div className="flex items-center gap-3 text-sm text-gray-500">
          <span>{t('found')} {formatNumber(total)} {t('results')}</span>
          <div className="ms-auto flex gap-2">
            <a href={exportUrl('csv')} target="_blank" rel="noopener"
               className="px-3 py-1 border border-gray-200 rounded-lg hover:bg-gray-50 text-xs font-medium">
              📄 CSV
            </a>
            <a href={exportUrl('json')} target="_blank" rel="noopener"
               className="px-3 py-1 border border-gray-200 rounded-lg hover:bg-gray-50 text-xs font-medium">
              📋 JSON
            </a>
          </div>
        </div>
      )}

      {/* Results list */}
      {results.map(r => (
        <ResultCard key={`${r.chat_name || currentChat}-${r.id}`} result={r} currentChat={currentChat} />
      ))}

      {/* No results */}
      {!loading && results.length === 0 && query && (
        <div className="text-center py-12 text-gray-400">
          {t('noResults')}
        </div>
      )}

      {/* Load more */}
      {results.length > 0 && results.length < total && (
        <div className="text-center py-4">
          <button
            onClick={onLoadMore}
            disabled={loading}
            className="px-6 py-2 border border-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
          >
            {loading ? '...' : t('loadMore')}
          </button>
        </div>
      )}
    </div>
  );
}

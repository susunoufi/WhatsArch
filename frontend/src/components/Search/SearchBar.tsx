import type { FormEvent } from 'react';
import { t } from '../../utils/i18n';

interface SearchBarProps {
  query: string;
  onQueryChange: (q: string) => void;
  onSearch: () => void;
  loading: boolean;
}

export function SearchBar({ query, onQueryChange, onSearch, loading }: SearchBarProps) {
  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onSearch();
  };

  return (
    <form onSubmit={handleSubmit} className="flex gap-2">
      <div className="flex-1 relative">
        <input
          type="text"
          value={query}
          onChange={e => onQueryChange(e.target.value)}
          placeholder={t('searchPlaceholder')}
          className="w-full px-4 py-3 bg-white border border-gray-200 rounded-xl text-sm focus:outline-none focus:border-[#0D9488] focus:ring-2 focus:ring-[#0D9488]/10 shadow-sm"
        />
        {loading && (
          <div className="absolute top-1/2 -translate-y-1/2 end-3">
            <div className="w-5 h-5 border-2 border-[#0D9488] border-t-transparent rounded-full animate-spin" />
          </div>
        )}
      </div>
      <button
        type="submit"
        disabled={loading || !query.trim()}
        className="px-6 py-3 bg-[#0D9488] text-white rounded-xl font-semibold text-sm hover:bg-[#0F766E] disabled:opacity-50 transition-colors shadow-sm"
      >
        🔍 {t('search')}
      </button>
    </form>
  );
}

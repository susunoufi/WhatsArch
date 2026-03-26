import { useCallback } from 'react';
import { SearchBar } from './SearchBar';
import { SearchFilters } from './SearchFilters';
import { SearchResults } from './SearchResults';
import { StatsBanner } from './StatsBanner';
import { useChatStore } from '../../stores/chatStore';
import { useSearchStore } from '../../stores/searchStore';

export function SearchTab() {
  const currentChat = useChatStore(s => s.currentChat);
  const stats = useChatStore(s => s.stats);
  const { query, results, total, loading, setQuery, search, loadMore, filters, setFilter } = useSearchStore();

  const handleSearch = useCallback(() => {
    if (currentChat) search(currentChat, true);
  }, [currentChat, search]);

  const handleLoadMore = useCallback(() => {
    if (currentChat) loadMore(currentChat);
  }, [currentChat, loadMore]);

  return (
    <div className="py-4 space-y-4">
      <SearchBar
        query={query}
        onQueryChange={setQuery}
        onSearch={handleSearch}
        loading={loading}
      />
      <SearchFilters
        filters={filters}
        onFilterChange={setFilter as (key: string, value: string) => void}
        senders={stats?.senders ? Object.keys(stats.senders) : []}
      />
      {stats && currentChat && currentChat !== '__all__' && (
        <StatsBanner stats={stats} />
      )}
      <SearchResults
        results={results}
        total={total}
        loading={loading}
        onLoadMore={handleLoadMore}
        query={query}
        currentChat={currentChat}
      />
    </div>
  );
}

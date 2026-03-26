import { t } from '../../utils/i18n';

interface SearchFiltersProps {
  filters: {
    sender: string;
    dateFrom: string;
    dateTo: string;
    searchType: string;
  };
  onFilterChange: (key: string, value: string) => void;
  senders: string[];
}

const SEARCH_TYPES = [
  { value: 'all', labelKey: 'all' },
  { value: 'text', labelKey: 'textOnly' },
  { value: 'transcription', labelKey: 'transcriptions' },
  { value: 'visual', labelKey: 'visualDesc' },
  { value: 'pdf', labelKey: 'pdfContent' },
];

export function SearchFilters({ filters, onFilterChange, senders }: SearchFiltersProps) {
  return (
    <div className="flex flex-wrap gap-3 items-center">
      {/* Search Type */}
      <select
        value={filters.searchType}
        onChange={e => onFilterChange('searchType', e.target.value)}
        className="px-3 py-2 bg-white border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#0D9488]"
      >
        {SEARCH_TYPES.map(st => (
          <option key={st.value} value={st.value}>{t(st.labelKey)}</option>
        ))}
      </select>

      {/* Sender */}
      {senders.length > 0 && (
        <select
          value={filters.sender}
          onChange={e => onFilterChange('sender', e.target.value)}
          className="px-3 py-2 bg-white border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#0D9488]"
        >
          <option value="">{t('sender')}: {t('all')}</option>
          {senders.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      )}

      {/* Date Range */}
      <div className="flex items-center gap-2">
        <input
          type="date"
          value={filters.dateFrom}
          onChange={e => onFilterChange('dateFrom', e.target.value)}
          className="px-3 py-2 bg-white border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#0D9488]"
          title={t('fromDate')}
        />
        <span className="text-gray-400">—</span>
        <input
          type="date"
          value={filters.dateTo}
          onChange={e => onFilterChange('dateTo', e.target.value)}
          className="px-3 py-2 bg-white border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-[#0D9488]"
          title={t('toDate')}
        />
      </div>
    </div>
  );
}

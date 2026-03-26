import { t } from '../../utils/i18n';

type Tab = 'search' | 'management' | 'settings' | 'gallery' | 'analytics';

interface TabNavProps {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
}

const TABS: { id: Tab; icon: string; labelKey: string }[] = [
  { id: 'search', icon: '🔍', labelKey: 'search' },
  { id: 'management', icon: '⚙️', labelKey: 'manage' },
  { id: 'settings', icon: '🔧', labelKey: 'settings' },
  { id: 'gallery', icon: '🖼️', labelKey: 'gallery' },
  { id: 'analytics', icon: '📊', labelKey: 'analytics' },
];

export function TabNav({ activeTab, onTabChange }: TabNavProps) {
  return (
    <nav className="sticky top-[72px] z-40 bg-white border-b border-gray-200 shadow-sm">
      <div className="max-w-6xl mx-auto flex">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`flex-1 py-3 px-2 text-sm font-semibold transition-colors border-b-2 ${
              activeTab === tab.id
                ? 'text-[#0D9488] border-[#0D9488]'
                : 'text-gray-500 border-transparent hover:text-gray-700 hover:bg-gray-50'
            }`}
          >
            <span className="me-1">{tab.icon}</span>
            {t(tab.labelKey)}
          </button>
        ))}
      </div>
    </nav>
  );
}

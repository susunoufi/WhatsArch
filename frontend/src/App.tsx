import { useEffect, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Header } from './components/Layout/Header';
import { TabNav } from './components/Layout/TabNav';
import { SearchTab } from './components/Search/SearchTab';
import { ManagementTab } from './components/Management/ManagementTab';
import { SettingsTab } from './components/Settings/SettingsTab';
import { GalleryTab } from './components/Gallery/GalleryTab';
import { AnalyticsTab } from './components/Analytics/AnalyticsTab';
import { AIChatPanel } from './components/AIChat/AIChatPanel';
import { useChatStore } from './stores/chatStore';
import { getLang, isRTL } from './utils/i18n';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30000 } },
});

type Tab = 'search' | 'management' | 'settings' | 'gallery' | 'analytics';

function AppContent() {
  const [activeTab, setActiveTab] = useState<Tab>('search');
  const [aiChatOpen, setAiChatOpen] = useState(false);
  const [lang, setLangState] = useState(getLang());
  const loadChats = useChatStore(s => s.loadChats);
  const currentChat = useChatStore(s => s.currentChat);

  useEffect(() => {
    loadChats();
  }, [loadChats]);

  // Update document direction when language changes
  useEffect(() => {
    document.documentElement.dir = isRTL() ? 'rtl' : 'ltr';
    document.documentElement.lang = lang;
  }, [lang]);

  const onLangChange = (newLang: string) => {
    setLangState(newLang);
  };

  return (
    <div className="min-h-screen bg-[#FAFAF8] text-[#1A1A2E]">
      <Header onLangChange={onLangChange} />
      <TabNav activeTab={activeTab} onTabChange={setActiveTab} />

      <main className="max-w-6xl mx-auto px-4 pb-20">
        {activeTab === 'search' && <SearchTab />}
        {activeTab === 'management' && <ManagementTab />}
        {activeTab === 'settings' && <SettingsTab />}
        {activeTab === 'gallery' && <GalleryTab />}
        {activeTab === 'analytics' && <AnalyticsTab />}
      </main>

      {/* AI Chat FAB + Panel */}
      {currentChat && currentChat !== '__all__' && (
        <>
          <button
            onClick={() => setAiChatOpen(!aiChatOpen)}
            className="fixed bottom-6 end-6 w-14 h-14 rounded-full bg-gradient-to-br from-[#0D9488] to-[#6366F1] text-white shadow-lg flex items-center justify-center text-xl hover:scale-105 transition-transform z-50"
            title="AI Chat"
          >
            💬
          </button>
          {aiChatOpen && (
            <AIChatPanel onClose={() => setAiChatOpen(false)} />
          )}
        </>
      )}
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  );
}

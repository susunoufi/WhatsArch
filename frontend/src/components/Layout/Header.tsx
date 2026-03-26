import { useChatStore } from '../../stores/chatStore';
import { t, getLang, setLang } from '../../utils/i18n';

interface HeaderProps {
  onLangChange: (lang: string) => void;
}

export function Header({ onLangChange }: HeaderProps) {
  const { chats, currentChat, setCurrentChat } = useChatStore();
  const lang = getLang();

  const toggleLang = () => {
    const newLang = lang === 'he' ? 'en' : 'he';
    setLang(newLang);
    onLangChange(newLang);
  };

  const readyChats = chats.filter(c => c.ready);

  return (
    <header className="sticky top-0 z-50 bg-gradient-to-r from-[#0D9488] to-[#6366F1] px-6 py-4 shadow-lg">
      <div className="flex items-center gap-3 max-w-6xl mx-auto">
        {/* Logo */}
        <div className="flex items-center gap-2">
          <span className="text-2xl">💬</span>
          <h1 className="text-lg font-bold">
            <span className="text-white">Whats</span>
            <span className="text-white/70">Arch</span>
          </h1>
        </div>

        {/* Chat Selector */}
        <select
          value={currentChat}
          onChange={(e) => setCurrentChat(e.target.value)}
          className="ms-auto px-3 py-2 bg-white/20 border border-white/30 rounded-lg text-white font-semibold text-sm cursor-pointer backdrop-blur-sm min-w-[160px] [&>option]:bg-white [&>option]:text-gray-900"
        >
          <option value="">{t('selectChat')}</option>
          {readyChats.length > 1 && (
            <option value="__all__">🔍 {t('allChats')}</option>
          )}
          {readyChats.map(c => (
            <option key={c.name} value={c.name}>
              {c.name} {c.platform ? `(${c.platform})` : ''}
            </option>
          ))}
        </select>

        {/* Language Toggle */}
        <button
          onClick={toggleLang}
          className="px-3 py-2 bg-white/20 border border-white/30 rounded-lg text-white font-bold text-sm cursor-pointer backdrop-blur-sm hover:bg-white/30 transition-colors"
        >
          {lang === 'he' ? 'EN' : 'עב'}
        </button>
      </div>
    </header>
  );
}

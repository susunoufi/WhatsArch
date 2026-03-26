import { useEffect, useState } from 'react';
import { useSettingsStore } from '../../stores/settingsStore';
import { t } from '../../utils/i18n';
import type { ModelOption } from '../../types';

export function SettingsTab() {
  const { settings, apiKeys, hardware, models, loading, load, updateSettings } = useSettingsStore();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [keys, setKeys] = useState({ anthropic: '', openai: '', gemini: '' });
  const [localSettings, setLocalSettings] = useState(settings);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (settings) setLocalSettings(settings); }, [settings]);

  const selectModel = (task: 'vision' | 'video' | 'rag', provider: string, model: string) => {
    setLocalSettings(prev => prev ? {
      ...prev,
      [`${task}_provider`]: provider,
      [`${task}_model`]: model,
    } : prev);
  };

  const save = async () => {
    setSaving(true);
    try {
      const body: Record<string, unknown> = { ...localSettings };
      if (keys.anthropic) body.anthropic_key = keys.anthropic;
      if (keys.openai) body.openai_key = keys.openai;
      if (keys.gemini) body.gemini_key = keys.gemini;
      await updateSettings(body);
      setKeys({ anthropic: '', openai: '', gemini: '' });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch { /* ignore */ }
    setSaving(false);
  };

  if (loading || !settings) return <div className="py-8 text-center text-gray-400">{t('loading')}</div>;

  const renderModelGrid = (task: 'vision' | 'video' | 'rag', modelList: ModelOption[]) => {
    const currentProvider = localSettings?.[`${task}_provider` as keyof typeof localSettings];
    const currentModel = localSettings?.[`${task}_model` as keyof typeof localSettings];
    const costKey = task === 'rag' ? 'cost_per_query' : task === 'video' ? 'cost_per_minute' : 'cost_per_image';
    const costLabel = task === 'rag' ? t('perQuestion') : task === 'video' ? t('perMinute') : t('perImage');

    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {modelList.map(m => {
          const isSelected = m.provider === currentProvider && m.model === currentModel;
          const cost = m[costKey as keyof ModelOption] as number;
          const stars = '\u2605'.repeat(m.hebrew_quality) + '\u2606'.repeat(5 - m.hebrew_quality);

          return (
            <div
              key={`${m.provider}-${m.model}`}
              onClick={() => selectModel(task, m.provider, m.model)}
              className={`relative p-3 rounded-lg border-2 cursor-pointer transition-all ${
                isSelected
                  ? 'border-[#0D9488] bg-[#0D9488]/5 shadow-sm'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              {/* Check mark */}
              <div className={`absolute top-2 start-2 w-5 h-5 rounded-full border-2 flex items-center justify-center text-xs ${
                isSelected ? 'bg-[#0D9488] border-[#0D9488] text-white' : 'border-gray-300'
              }`}>
                {isSelected && '\u2713'}
              </div>

              {/* Provider + name */}
              <div className="flex items-center gap-2 mb-2">
                <div className={`w-7 h-7 rounded-md flex items-center justify-center text-white text-xs font-bold ${
                  m.provider === 'gemini' ? 'bg-blue-500' :
                  m.provider === 'openai' ? 'bg-emerald-500' :
                  m.provider === 'anthropic' ? 'bg-amber-600' : 'bg-purple-500'
                }`}>
                  {m.provider[0].toUpperCase()}
                </div>
                <div>
                  <div className="text-sm font-semibold">{m.display}</div>
                  <div className="text-xs text-gray-400">{m.provider}</div>
                </div>
                <div className="ms-auto flex gap-1">
                  {m.badge === 'recommended' && <span className="text-xs px-1.5 py-0.5 bg-[#6366F1]/10 text-[#6366F1] rounded font-bold">{t('recommended')}</span>}
                  {m.badge === 'free' && <span className="text-xs px-1.5 py-0.5 bg-[#0D9488]/10 text-[#0D9488] rounded font-bold">{t('free')}</span>}
                  {m.badge === 'best' && <span className="text-xs px-1.5 py-0.5 bg-amber-100 text-amber-600 rounded font-bold">{t('best')}</span>}
                </div>
              </div>

              {/* Details */}
              <div className="grid grid-cols-2 gap-1 text-xs pt-2 border-t border-gray-100">
                <div><span className="text-gray-400">{t('costSummary')}:</span> <span className={cost === 0 ? 'text-[#0D9488] font-bold' : ''}>{cost === 0 ? t('free') : `$${cost}${costLabel}`}</span></div>
                <div><span className="text-gray-400">{t('fast')}:</span> <span className={m.speed === 'fast' ? 'text-[#0D9488]' : 'text-amber-500'}>{m.speed === 'fast' ? t('fast') : t('slow')}</span></div>
                <div><span className="text-amber-400 text-xs">{stars}</span></div>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="py-4 space-y-4">
      {/* API Keys */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <h3 className="font-bold text-sm mb-1">{t('apiKeys')}</h3>
        <p className="text-xs text-gray-500 mb-4">{t('apiKeysNote')}</p>
        {[
          { label: 'Anthropic', key: 'anthropic' as const, placeholder: 'sk-ant-...', configured: apiKeys?.anthropic_configured, preview: apiKeys?.anthropic_preview },
          { label: 'OpenAI', key: 'openai' as const, placeholder: 'sk-...', configured: apiKeys?.openai_configured, preview: apiKeys?.openai_preview },
          { label: 'Google Gemini', key: 'gemini' as const, placeholder: 'AIza...', configured: apiKeys?.gemini_configured, preview: apiKeys?.gemini_preview },
        ].map(field => (
          <div key={field.key} className="mb-3">
            <label className="text-sm font-medium">{field.label}</label>
            <input
              type="password"
              value={keys[field.key]}
              onChange={e => setKeys(prev => ({ ...prev, [field.key]: e.target.value }))}
              placeholder={field.placeholder}
              className="w-full mt-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm font-mono focus:outline-none focus:border-[#0D9488]"
              dir="ltr"
            />
            <div className={`text-xs mt-1 ${field.configured ? 'text-[#0D9488]' : 'text-gray-400'}`}>
              {field.configured ? `\u2713 ${t('configured')} (${field.preview})` : t('notConfigured')}
            </div>
          </div>
        ))}
      </div>

      {/* Hardware */}
      {hardware && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h3 className="font-bold text-sm mb-3">{t('hardware')} <span className="text-xs text-gray-400 font-normal">— {t('autoDetected')}</span></h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            {[
              { label: t('cpu'), value: hardware.hardware.cpu, sub: `${hardware.hardware.cpu_cores} ${t('cores')}` },
              { label: t('ram'), value: `${hardware.hardware.ram_gb} GB`, sub: `${hardware.hardware.ram_available_gb} GB ${t('available')}` },
              { label: t('gpu'), value: hardware.hardware.gpu, sub: hardware.hardware.gpu_dedicated ? t('dedicated') : t('integrated'), color: hardware.hardware.gpu_dedicated ? 'text-[#0D9488]' : 'text-amber-500' },
              { label: t('device'), value: hardware.hardware.device_name, sub: hardware.hardware.os },
            ].map((item, i) => (
              <div key={i} className="p-3 bg-gray-50 rounded-lg">
                <div className="text-xs text-gray-400 uppercase tracking-wide">{item.label}</div>
                <div className={`text-sm font-bold mt-1 ${item.color || ''}`}>{item.value}</div>
                <div className="text-xs text-gray-500 mt-0.5">{item.sub}</div>
              </div>
            ))}
          </div>
          {hardware.performance && (
            <>
              <div className="text-xs font-semibold mb-1">{t('ollamaPerf')}</div>
              <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden mb-2">
                <div className={`h-full rounded-full ${
                  hardware.performance.overall_rating === 'excellent' ? 'bg-[#0D9488] w-full' :
                  hardware.performance.overall_rating === 'good' ? 'bg-[#0D9488] w-4/5' :
                  hardware.performance.overall_rating === 'medium' ? 'bg-amber-400 w-3/5' :
                  'bg-red-400 w-1/4'
                }`} />
              </div>
              <div className="text-xs text-gray-600 p-3 bg-[#0D9488]/5 rounded-lg border border-[#0D9488]/20">
                {hardware.performance.recommendation_text}
              </div>
            </>
          )}
        </div>
      )}

      {/* Model Selection */}
      {models && (
        <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-6">
          <h3 className="font-bold text-sm">{t('modelSelection')}</h3>

          <div>
            <h4 className="font-semibold text-sm mb-2">{t('imageDesc')}</h4>
            {renderModelGrid('vision', models.vision)}
          </div>

          <hr className="border-gray-100" />

          <div>
            <h4 className="font-semibold text-sm mb-2">{t('videoDesc')}</h4>
            {renderModelGrid('video', models.video)}
          </div>

          <hr className="border-gray-100" />

          <div>
            <h4 className="font-semibold text-sm mb-2">{t('aiChat')}</h4>
            {renderModelGrid('rag', models.rag)}
          </div>
        </div>
      )}

      {/* Save */}
      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="px-6 py-3 bg-[#0D9488] text-white rounded-xl font-semibold text-sm hover:bg-[#0F766E] disabled:opacity-50"
        >
          {saving ? '...' : t('save')}
        </button>
        {saved && <span className="text-sm text-[#0D9488]">{'\u2713'} {t('saved')}</span>}
      </div>
    </div>
  );
}

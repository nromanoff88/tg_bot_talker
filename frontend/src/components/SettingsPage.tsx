import React, { useEffect, useState } from 'react';
import { CheckCircle2, CloudSun, Newspaper, RefreshCw, Save, Wifi, XCircle } from 'lucide-react';
import { api } from '../api';
import type { ExternalContextStatus, SystemConfig } from '../types';

export function SettingsPage({ onSaved }: { onSaved: () => void }) {
  const [cfg, setCfg] = useState<SystemConfig | null>(null);
  const [llmBaseUrl, setLlmBaseUrl] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [fastModel, setFastModel] = useState('');
  const [smartModel, setSmartModel] = useState('');
  const [embBaseUrl, setEmbBaseUrl] = useState('');
  const [embApiKey, setEmbApiKey] = useState('');
  const [embModel, setEmbModel] = useState('');

  const [weatherCity, setWeatherCity] = useState('');
  const [newsChannelsText, setNewsChannelsText] = useState('');
  const [newsTopics, setNewsTopics] = useState('');
  const [extStatus, setExtStatus] = useState<ExternalContextStatus | null>(null);
  const [refreshingExt, setRefreshingExt] = useState(false);

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState('');
  const [testResult, setTestResult] = useState<any>(null);

  const load = async () => {
    const [data, ext] = await Promise.all([api.getLLMConfig(), api.getExternalContext()]);
    setCfg(data);
    setLlmBaseUrl(data.llm_base_url);
    setFastModel(data.llm_fast_model);
    setSmartModel(data.llm_smart_model);
    setEmbBaseUrl(data.embedding_base_url);
    setEmbModel(data.embedding_model);
    setWeatherCity(data.weather_city || '');
    setNewsChannelsText((data.news_tg_channels || []).join(', '));
    setNewsTopics(data.news_topics || '');
    setExtStatus(ext);
    setLlmApiKey('');
    setEmbApiKey('');
  };

  useEffect(() => {
    load();
  }, []);

  const parseChannels = (raw: string): string[] =>
    raw
      .split(/[\s,;]+/)
      .map((s) => s.trim())
      .filter(Boolean);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setMessage('');
    try {
      await api.updateLLMConfig({
        llm_base_url: llmBaseUrl,
        llm_api_key: llmApiKey || undefined,
        llm_fast_model: fastModel,
        llm_smart_model: smartModel,
        embedding_base_url: embBaseUrl,
        embedding_api_key: embApiKey || undefined,
        embedding_model: embModel,
        weather_city: weatherCity.trim(),
        news_tg_channels: parseChannels(newsChannelsText),
        news_topics: newsTopics.trim(),
      });
      await load();
      onSaved();
      setMessage('Настройки моделей и внешнего контекста успешно применены на лету.');
    } catch (err: any) {
      setMessage(`Ошибка: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleRefreshExternal = async () => {
    setRefreshingExt(true);
    setMessage('');
    try {
      await api.updateLLMConfig({
        llm_base_url: llmBaseUrl,
        llm_api_key: llmApiKey || undefined,
        llm_fast_model: fastModel,
        llm_smart_model: smartModel,
        embedding_base_url: embBaseUrl,
        embedding_api_key: embApiKey || undefined,
        embedding_model: embModel,
        weather_city: weatherCity.trim(),
        news_tg_channels: parseChannels(newsChannelsText),
        news_topics: newsTopics.trim(),
      });
      const res = await api.refreshExternalContext();
      setExtStatus(res);
      setMessage('Погода на 10 дней и новости из Telegram-каналов обновлены.');
    } catch (err: any) {
      setMessage(`Ошибка обновления погоды/новостей: ${err.message}`);
    } finally {
      setRefreshingExt(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.testLLMConfig();
      setTestResult(res);
    } catch (err: any) {
      setTestResult({ errors: [err.message] });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">Настройки LLM-провайдера и моделей</h1>
        <p className="text-sm text-slate-400 mt-0.5">
          Поддерживается OpenRouter, OpenAI, Google Gemini (OpenAI API), Anthropic через прокси или любой совместимый API
        </p>
      </div>

      <form onSubmit={handleSave} className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
        <div className="space-y-4">
          <h3 className="text-sm font-semibold text-indigo-300 uppercase tracking-wider">
            1. Двухконтурный мозг бота (Fast Router + Smart Persona)
          </h3>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1">OpenAI-compatible Base URL</label>
              <input
                type="text"
                value={llmBaseUrl}
                onChange={(e) => setLlmBaseUrl(e.target.value)}
                placeholder="https://openrouter.ai/api/v1"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                required
              />
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1">
                API Key {cfg?.has_llm_api_key && <span className="text-emerald-400">({cfg.llm_api_key_masked})</span>}
              </label>
              <input
                type="password"
                value={llmApiKey}
                onChange={(e) => setLlmApiKey(e.target.value)}
                placeholder={cfg?.has_llm_api_key ? 'Оставьте пустым, чтобы не менять' : 'sk-or-v1-...'}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1">
                Fast Model (классификатор ответов и извлечение фактов)
              </label>
              <input
                type="text"
                value={fastModel}
                onChange={(e) => setFastModel(e.target.value)}
                placeholder="google/gemini-2.5-flash"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                required
              />
              <span className="text-[11px] text-slate-500">Вызывается часто — лучше быстрая и недорогая модель</span>
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1">
                Smart Model (живая речь Персоны и инициатива)
              </label>
              <input
                type="text"
                value={smartModel}
                onChange={(e) => setSmartModel(e.target.value)}
                placeholder="anthropic/claude-3.7-sonnet"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                required
              />
              <span className="text-[11px] text-slate-500">Отвечает за юмор, естественность и отыгрыш роли</span>
            </div>
          </div>
        </div>

        <div className="pt-5 border-t border-slate-800 space-y-4">
          <h3 className="text-sm font-semibold text-amber-300 uppercase tracking-wider">
            2. Векторные эмбеддинги (pgvector)
          </h3>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Embedding Base URL</label>
              <input
                type="text"
                value={embBaseUrl}
                onChange={(e) => setEmbBaseUrl(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
              />
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1">
                Embedding API Key{' '}
                {cfg?.has_embedding_api_key && (
                  <span className="text-emerald-400">({cfg.embedding_api_key_masked})</span>
                )}
              </label>
              <input
                type="password"
                value={embApiKey}
                onChange={(e) => setEmbApiKey(e.target.value)}
                placeholder="По умолчанию используется основной ключ"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
              />
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1">Embedding Model</label>
              <input
                type="text"
                value={embModel}
                onChange={(e) => setEmbModel(e.target.value)}
                placeholder="openai/text-embedding-3-small"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
              />
            </div>
          </div>
        </div>

        <div className="pt-5 border-t border-slate-800 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-sky-300 uppercase tracking-wider flex items-center gap-2">
              <CloudSun className="w-4 h-4" />
              3. Погода (прогноз на 10 дней) и Новости из Telegram-каналов
            </h3>
            <button
              type="button"
              onClick={handleRefreshExternal}
              disabled={refreshingExt || saving}
              className="px-3.5 py-1.5 rounded-xl bg-sky-500/15 hover:bg-sky-500/25 disabled:opacity-40 text-sky-300 border border-sky-500/30 text-xs font-medium flex items-center gap-1.5 cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshingExt ? 'animate-spin' : ''}`} />
              {refreshingExt ? 'Обновляем...' : 'Обновить погоду и новости сейчас'}
            </button>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1">
                Город для погоды (через запятую до 3 городов)
              </label>
              <input
                type="text"
                value={weatherCity}
                onChange={(e) => setWeatherCity(e.target.value)}
                placeholder="Уфа"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
              />
              <span className="text-[11px] text-slate-500">
                Текущая погода + прогноз на 10 дней с датами (Open-Meteo, обновляется каждые ~25 мин)
              </span>
            </div>

            <div>
              <label className="block text-xs text-slate-400 mb-1">
                Интересующие темы для фильтрации новостей
              </label>
              <input
                type="text"
                value={newsTopics}
                onChange={(e) => setNewsTopics(e.target.value)}
                placeholder="Уфа, автомобили, IT, игры, экономика, спорт"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
              />
              <span className="text-[11px] text-slate-500">
                Fast LLM отфильтрует посты по этим темам и уберёт рекламу и дубли
              </span>
            </div>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">
              Публичные Telegram-каналы для новостей (через запятую или с новой строки)
            </label>
            <textarea
              rows={2}
              value={newsChannelsText}
              onChange={(e) => setNewsChannelsText(e.target.value)}
              placeholder="@rbc_news, @ufa1ru, https://t.me/rian_ru"
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
            <span className="text-[11px] text-slate-500">
              Можно указывать @username или ссылку t.me/... — каналы должны быть публичными (с веб-предпросмотром)
            </span>
          </div>

          {(extStatus?.weather_block || extStatus?.news_block || (extStatus?.errors && extStatus.errors.length > 0)) && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 pt-2">
              <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium text-sky-300 flex items-center gap-1.5">
                    <CloudSun className="w-3.5 h-3.5" />
                    Кэш погоды (10 дней)
                  </span>
                  {extStatus.weather_updated_at && (
                    <span className="text-[10px] text-slate-500">
                      {new Date(extStatus.weather_updated_at).toLocaleTimeString('ru-RU', {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                  )}
                </div>
                <pre className="whitespace-pre-wrap text-[11px] text-slate-300 font-sans leading-relaxed max-h-48 overflow-y-auto">
                  {extStatus.weather_block || 'Пока не загружена — укажите город и нажмите «Обновить».'}
                </pre>
              </div>

              <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium text-amber-300 flex items-center gap-1.5">
                    <Newspaper className="w-3.5 h-3.5" />
                    Кэш новостей из ТГ ({extStatus.raw_posts_fetched} постов)
                  </span>
                  {extStatus.news_updated_at && (
                    <span className="text-[10px] text-slate-500">
                      {new Date(extStatus.news_updated_at).toLocaleTimeString('ru-RU', {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                  )}
                </div>
                <pre className="whitespace-pre-wrap text-[11px] text-slate-300 font-sans leading-relaxed max-h-48 overflow-y-auto">
                  {extStatus.news_block || 'Пока нет дайджеста — укажите публичные ТГ-каналы и нажмите «Обновить».'}
                </pre>
              </div>
            </div>
          )}

          {extStatus?.errors && extStatus.errors.length > 0 && (
            <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300 space-y-1">
              {extStatus.errors.map((err, i) => (
                <div key={i}>• {err}</div>
              ))}
            </div>
          )}
        </div>

        {message && (
          <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-xs text-indigo-200">
            {message}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3 pt-4 border-t border-slate-800">
          <button
            type="button"
            onClick={handleTest}
            disabled={testing}
            className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-200 flex items-center gap-2 cursor-pointer"
          >
            <Wifi className="w-4 h-4 text-emerald-400" />
            {testing ? 'Проверяем соединение...' : 'Проверить подключение к LLM'}
          </button>

          <button
            type="submit"
            disabled={saving}
            className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white flex items-center gap-2 cursor-pointer"
          >
            <Save className="w-4 h-4" />
            {saving ? 'Сохранение...' : 'Сохранить конфигурацию'}
          </button>
        </div>
      </form>

      {testResult && (
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 space-y-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-semibold text-white text-sm">Результат диагностики LLM:</span>
            {testResult.provider && (
              <span className="px-2.5 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                Режим: {testResult.provider}
              </span>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex items-center gap-2">
              {testResult.fast_ok ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              ) : (
                <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
              )}
              <span>Fast Router: {testResult.fast_ok ? 'Успешно' : 'Ошибка'}</span>
            </div>
            <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex items-center gap-2">
              {testResult.smart_ok ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              ) : (
                <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
              )}
              <span>Smart Persona: {testResult.smart_ok ? `ОК («${testResult.smart_sample}»)` : 'Ошибка'}</span>
            </div>
            <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex items-center gap-2">
              {testResult.embedding_ok ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              ) : (
                <XCircle className="w-4 h-4 text-amber-400 shrink-0" />
              )}
              <span>Embeddings (1536d): {testResult.embedding_ok ? 'Успешно' : 'Текстовый fallback'}</span>
            </div>
          </div>
          {testResult.errors?.length > 0 && (
            <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 space-y-1">
              {testResult.errors.map((err: string, idx: number) => (
                <div key={idx}>• {err}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


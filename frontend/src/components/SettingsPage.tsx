import React, { useEffect, useState } from 'react';
import { CheckCircle2, Save, Wifi, XCircle } from 'lucide-react';
import { api } from '../api';
import type { SystemConfig } from '../types';

export function SettingsPage({ onSaved }: { onSaved: () => void }) {
  const [cfg, setCfg] = useState<SystemConfig | null>(null);
  const [llmBaseUrl, setLlmBaseUrl] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [fastModel, setFastModel] = useState('');
  const [smartModel, setSmartModel] = useState('');
  const [embBaseUrl, setEmbBaseUrl] = useState('');
  const [embApiKey, setEmbApiKey] = useState('');
  const [embModel, setEmbModel] = useState('');

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState('');
  const [testResult, setTestResult] = useState<any>(null);

  const load = async () => {
    const data = await api.getLLMConfig();
    setCfg(data);
    setLlmBaseUrl(data.llm_base_url);
    setFastModel(data.llm_fast_model);
    setSmartModel(data.llm_smart_model);
    setEmbBaseUrl(data.embedding_base_url);
    setEmbModel(data.embedding_model);
    setLlmApiKey('');
    setEmbApiKey('');
  };

  useEffect(() => {
    load();
  }, []);

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
      });
      await load();
      onSaved();
      setMessage('Настройки моделей успешно применены на лету (перезапуск контейнеров не требуется).');
    } catch (err: any) {
      setMessage(`Ошибка: ${err.message}`);
    } finally {
      setSaving(false);
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


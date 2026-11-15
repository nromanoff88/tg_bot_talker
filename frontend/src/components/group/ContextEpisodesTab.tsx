import React, { useEffect, useState } from 'react';
import { Lock, Plus, Save, Search, Trash2, Unlock } from 'lucide-react';
import { api } from '../../api';
import type { GroupContext, GroupRuleOrJokeItem, MemoryEpisode } from '../../types';

interface Props {
  groupId: number;
  initialContext: GroupContext;
  onSaved: () => void;
}

export function ContextEpisodesTab({ groupId, initialContext, onSaved }: Props) {
  const [ctx, setCtx] = useState<GroupContext>(initialContext);
  const [topicsInput, setTopicsInput] = useState(initialContext.active_topics.join(', '));
  const [newRuleKind, setNewRuleKind] = useState<'meme' | 'rule' | 'norm'>('meme');
  const [newRuleText, setNewRuleText] = useState('');
  const [savingCtx, setSavingCtx] = useState(false);
  const [ctxNotice, setCtxNotice] = useState('');

  // Эпизоды pgvector
  const [episodes, setEpisodes] = useState<MemoryEpisode[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [epTitle, setEpTitle] = useState('');
  const [epSummary, setEpSummary] = useState('');
  const [showAddEpisode, setShowAddEpisode] = useState(false);

  const loadEpisodes = async (q?: string) => {
    const list = await api.listEpisodes(groupId, q);
    setEpisodes(list);
  };

  useEffect(() => {
    setCtx(initialContext);
    setTopicsInput(initialContext.active_topics.join(', '));
    loadEpisodes();
  }, [groupId, initialContext]);

  const handleAddJokeOrRule = () => {
    if (!newRuleText.trim()) return;
    const item: GroupRuleOrJokeItem = {
      id: 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      kind: newRuleKind,
      text: newRuleText.trim(),
      is_locked: true,
    };
    setCtx({ ...ctx, inside_jokes_and_rules: [item, ...ctx.inside_jokes_and_rules] });
    setNewRuleText('');
  };

  const handleToggleItemLock = (id: string) => {
    setCtx({
      ...ctx,
      inside_jokes_and_rules: ctx.inside_jokes_and_rules.map((it) =>
        it.id === id ? { ...it, is_locked: !it.is_locked } : it
      ),
    });
  };

  const handleUpdateItemText = (id: string, text: string) => {
    setCtx({
      ...ctx,
      inside_jokes_and_rules: ctx.inside_jokes_and_rules.map((it) =>
        it.id === id ? { ...it, text, is_locked: true } : it
      ),
    });
  };

  const handleRemoveItem = (id: string) => {
    setCtx({
      ...ctx,
      inside_jokes_and_rules: ctx.inside_jokes_and_rules.filter((it) => it.id !== id),
    });
  };

  const handleSaveContext = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingCtx(true);
    setCtxNotice('');
    try {
      const updated = await api.updateGroupContext(groupId, {
        ...ctx,
        active_topics: topicsInput
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
      });
      setCtx(updated);
      onSaved();
      setCtxNotice('Контекст группы сохранён! Закреплённые поля защищены от перезаписи ИИ.');
    } catch (err: any) {
      setCtxNotice(`Ошибка: ${err.message}`);
    } finally {
      setSavingCtx(false);
    }
  };

  const handleSearchEpisodes = (e: React.FormEvent) => {
    e.preventDefault();
    loadEpisodes(searchQuery.trim() || undefined);
  };

  const handleCreateEpisode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!epSummary.trim()) return;
    await api.createEpisode(groupId, {
      title: epTitle.trim() || 'Важное событие чата',
      episode_summary: epSummary.trim(),
      participant_names: [],
      is_locked: true,
    });
    setEpTitle('');
    setEpSummary('');
    setShowAddEpisode(false);
    loadEpisodes(searchQuery.trim() || undefined);
  };

  const handleDeleteEpisode = async (id: string) => {
    await api.deleteEpisode(groupId, id);
    loadEpisodes(searchQuery.trim() || undefined);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* Левая колонка: Общий контекст группы, сленг и правила */}
      <form onSubmit={handleSaveContext} className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-white">Контекст и атмосфера группы</h3>
            <p className="text-xs text-slate-400">Всегда передаётся боту при генерации ответов в этом чате</p>
          </div>
          <button
            type="button"
            onClick={() => setCtx({ ...ctx, summary_locked: !ctx.summary_locked })}
            className={`px-3 py-1.5 rounded-xl text-xs flex items-center gap-1.5 border cursor-pointer ${
              ctx.summary_locked
                ? 'bg-amber-500/15 text-amber-300 border-amber-500/40'
                : 'bg-slate-950 text-slate-400 border-slate-800'
            }`}
            title="Защитить описание от автоматического обновления ИИ"
          >
            {ctx.summary_locked ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
            {ctx.summary_locked ? 'Саммари закреплено' : 'Авто-обновление ИИ'}
          </button>
        </div>

        <div>
          <label className="block text-xs text-slate-400 mb-1">Общее саммари группы (о чём чат, кто здесь сидит)</label>
          <textarea
            rows={4}
            value={ctx.summary}
            onChange={(e) => setCtx({ ...ctx, summary: e.target.value, summary_locked: true })}
            placeholder="Например: Дружеский чат бывших коллег-разработчиков. Обсуждают работу, игры, переезды и быт..."
            className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
          />
        </div>

        <div>
          <label className="block text-xs text-slate-400 mb-1">Активные темы (через запятую)</label>
          <input
            type="text"
            value={topicsInput}
            onChange={(e) => setTopicsInput(e.target.value)}
            placeholder="релиз проекта, выбор авто, шашлыки в субботу"
            className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
          />
        </div>

        <div className="space-y-3 pt-2 border-t border-slate-800">
          <label className="block text-xs font-medium text-slate-300">
            Локальные мемы, внутренние шутки и правила группы
          </label>

          <div className="flex gap-2">
            <select
              value={newRuleKind}
              onChange={(e) => setNewRuleKind(e.target.value as any)}
              className="px-2.5 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-slate-200"
            >
              <option value="meme">Мем / Сленг</option>
              <option value="rule">Правило</option>
              <option value="norm">Традиция</option>
            </select>
            <input
              type="text"
              value={newRuleText}
              onChange={(e) => setNewRuleText(e.target.value)}
              placeholder="Добавить внутренний мем или правило..."
              className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
            />
            <button
              type="button"
              onClick={handleAddJokeOrRule}
              className="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-white cursor-pointer"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>

          <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
            {ctx.inside_jokes_and_rules.length === 0 ? (
              <div className="text-xs text-slate-500 py-3">Пока пусто — бот сам выделит их из общения или добавь вручную.</div>
            ) : (
              ctx.inside_jokes_and_rules.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center gap-2 p-2.5 rounded-xl bg-slate-950 border border-slate-800"
                >
                  <span className="text-[10px] uppercase px-2 py-0.5 rounded bg-slate-800 text-slate-300 shrink-0">
                    {item.kind === 'rule' ? 'Правило' : item.kind === 'norm' ? 'Норма' : 'Мем'}
                  </span>
                  <input
                    type="text"
                    value={item.text}
                    onChange={(e) => handleUpdateItemText(item.id, e.target.value)}
                    className="flex-1 bg-transparent text-xs text-slate-200 focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => handleToggleItemLock(item.id)}
                    className={`p-1 rounded cursor-pointer ${
                      item.is_locked ? 'text-amber-400' : 'text-slate-600 hover:text-slate-400'
                    }`}
                    title={item.is_locked ? 'Закреплено админом' : 'Не закреплено'}
                  >
                    {item.is_locked ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleRemoveItem(item.id)}
                    className="p-1 text-slate-600 hover:text-rose-400 cursor-pointer"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {ctxNotice && <div className="text-xs text-emerald-400">{ctxNotice}</div>}

        <button
          type="submit"
          disabled={savingCtx}
          className="w-full py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white flex items-center justify-center gap-2 cursor-pointer"
        >
          <Save className="w-4 h-4" />
          {savingCtx ? 'Сохранение...' : 'Сохранить контекст группы'}
        </button>
      </form>

      {/* Правая колонка: Векторная эпизодическая память (pgvector) */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4 flex flex-col">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-base font-semibold text-white">Эпизодическая память (pgvector)</h3>
            <p className="text-xs text-slate-400">
              Смысловые выжимки прошлых сцен и споров, которые бот ищет по векторам близости
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowAddEpisode(!showAddEpisode)}
            className="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-white flex items-center gap-1 cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" /> Добавить эпизод
          </button>
        </div>

        {/* Тест векторного поиска */}
        <form onSubmit={handleSearchEpisodes} className="flex gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Проверить семантический поиск: что вспомнит бот по запросу..."
            className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
          />
          <button
            type="submit"
            className="px-3.5 py-2 rounded-xl bg-indigo-600/20 border border-indigo-500/40 text-indigo-300 text-xs flex items-center gap-1.5 cursor-pointer"
          >
            <Search className="w-3.5 h-3.5" /> Найти
          </button>
          {searchQuery && (
            <button
              type="button"
              onClick={() => {
                setSearchQuery('');
                loadEpisodes();
              }}
              className="px-2.5 py-2 rounded-xl bg-slate-800 text-xs text-slate-400 cursor-pointer"
            >
              Сброс
            </button>
          )}
        </form>

        {showAddEpisode && (
          <form onSubmit={handleCreateEpisode} className="p-4 rounded-xl bg-slate-950 border border-indigo-500/30 space-y-2.5">
            <input
              type="text"
              value={epTitle}
              onChange={(e) => setEpTitle(e.target.value)}
              placeholder="Короткий заголовок воспоминания"
              className="w-full px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-xs text-white"
              required
            />
            <textarea
              rows={3}
              value={epSummary}
              onChange={(e) => setEpSummary(e.target.value)}
              placeholder="Описание события или спора, которое бот должен помнить..."
              className="w-full px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-xs text-white"
              required
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowAddEpisode(false)}
                className="px-3 py-1.5 rounded-lg bg-slate-800 text-xs text-slate-400 cursor-pointer"
              >
                Отмена
              </button>
              <button
                type="submit"
                className="px-3 py-1.5 rounded-lg bg-indigo-600 text-xs text-white cursor-pointer"
              >
                Сохранить и векторизовать
              </button>
            </div>
          </form>
        )}

        <div className="space-y-2.5 max-h-[520px] overflow-y-auto pr-1">
          {episodes.length === 0 ? (
            <div className="text-xs text-slate-500 py-8 text-center">
              Эпизодов пока нет — они появятся автоматически при накоплении обсуждений или после импорта истории чата.
            </div>
          ) : (
            episodes.map((ep) => (
              <div key={ep.id} className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 space-y-1.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="font-medium text-xs text-white flex items-center gap-2 flex-wrap">
                    <span>{ep.title}</span>
                    {ep.similarity != null && (
                      <span className="px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-[10px]">
                        Сходство: {Math.round(ep.similarity * 100)}%
                      </span>
                    )}
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded ${
                        ep.has_embedding ? 'bg-emerald-500/10 text-emerald-400' : 'bg-slate-800 text-slate-500'
                      }`}
                    >
                      {ep.has_embedding ? 'vector' : 'text'}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDeleteEpisode(ep.id)}
                    className="text-slate-600 hover:text-rose-400 cursor-pointer"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">{ep.episode_summary}</p>
                <div className="text-[11px] text-slate-500 flex justify-between pt-1">
                  <span>
                    {ep.participant_names.length > 0 ? `Участники: ${ep.participant_names.join(', ')}` : ''}
                  </span>
                  <span>{new Date(ep.happened_at).toLocaleDateString('ru-RU')}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

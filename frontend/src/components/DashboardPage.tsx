import React, { useEffect, useState } from 'react';
import {
  Brain,
  Coins,
  Database,
  MessageSquare,
  Plus,
  RefreshCw,
  Users,
  ChevronRight,
  PauseCircle,
  PlayCircle,
} from 'lucide-react';
import { api } from '../api';
import type { DashboardStats, GroupSummary } from '../types';

interface Props {
  stats: DashboardStats | null;
  onOpenGroup: (groupId: number) => void;
  onRefreshStats: () => void;
}

export function DashboardPage({ stats, onOpenGroup, onRefreshStats }: Props) {
  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [manualId, setManualId] = useState('');
  const [manualTitle, setManualTitle] = useState('');
  const [addError, setAddError] = useState('');

  const loadGroups = async () => {
    setLoading(true);
    try {
      const list = await api.listGroups();
      setGroups(list);
      onRefreshStats();
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadGroups();
  }, []);

  const handleManualAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setAddError('');
    const parsedId = Number(manualId.trim());
    if (!Number.isInteger(parsedId) || parsedId === 0) {
      setAddError('Укажите корректный числовой Telegram Chat ID (например, -1001234567890)');
      return;
    }
    try {
      const created = await api.registerGroup(parsedId, manualTitle.trim() || `Group ${parsedId}`);
      setShowAddModal(false);
      setManualId('');
      setManualTitle('');
      onOpenGroup(created.id);
    } catch (err: any) {
      setAddError(err.message || 'Ошибка регистрации группы');
    }
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-white">Группы и сводка памяти</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Изолированные профили участников, векторные эпизоды и поведение бота по каждому чату
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <button
            onClick={loadGroups}
            className="px-3.5 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs font-medium text-slate-300 flex items-center gap-2 transition cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Обновить
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            className="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white flex items-center gap-1.5 transition cursor-pointer"
          >
            <Plus className="w-4 h-4" />
            Добавить группу по ID
          </button>
        </div>
      </div>

      {/* Метрики */}
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-4 rounded-2xl bg-slate-900 border border-slate-800">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-2">
              <span>Активные группы</span>
              <MessageSquare className="w-4 h-4 text-indigo-400" />
            </div>
            <div className="text-2xl font-semibold text-white">
              {stats.active_groups}{' '}
              <span className="text-sm font-normal text-slate-500">/ {stats.total_groups}</span>
            </div>
            <div className="text-[11px] text-slate-500 mt-1">
              Сообщений в базе: {stats.total_messages.toLocaleString('ru-RU')}
            </div>
          </div>

          <div className="p-4 rounded-2xl bg-slate-900 border border-slate-800">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-2">
              <span>Изученные участники</span>
              <Users className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="text-2xl font-semibold text-white">{stats.total_members}</div>
            <div className="text-[11px] text-slate-500 mt-1">
              Атомарных фактов: {stats.total_facts.toLocaleString('ru-RU')}
            </div>
          </div>

          <div className="p-4 rounded-2xl bg-slate-900 border border-slate-800">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-2">
              <span>Эпизоды pgvector</span>
              <Brain className="w-4 h-4 text-amber-400" />
            </div>
            <div className="text-2xl font-semibold text-white">{stats.total_episodes}</div>
            <div className="text-[11px] text-slate-500 mt-1">Семантическая память обсуждений</div>
          </div>

          <div className="p-4 rounded-2xl bg-slate-900 border border-slate-800">
            <div className="flex items-center justify-between text-slate-400 text-xs mb-2">
              <span>Расход LLM (всего)</span>
              <Coins className="w-4 h-4 text-sky-400" />
            </div>
            <div className="text-2xl font-semibold text-white">${stats.cost_total_usd.toFixed(3)}</div>
            <div className="text-[11px] text-slate-500 mt-1">
              За 24ч: ${stats.cost_24h_usd.toFixed(4)} ({stats.tokens_24h.toLocaleString('ru-RU')} ток.)
            </div>
          </div>
        </div>
      )}

      {/* Список групп */}
      {groups.length === 0 && !loading ? (
        <div className="p-10 rounded-2xl bg-slate-900/60 border border-slate-800 text-center space-y-3">
          <Database className="w-10 h-10 text-slate-600 mx-auto" />
          <h3 className="text-base font-medium text-white">Пока нет подключённых групп</h3>
          <p className="text-sm text-slate-400 max-w-lg mx-auto">
            Просто добавь своего Telegram-бота в любой групповой чат (предварительно отключив{' '}
            <code className="text-indigo-300 bg-slate-800 px-1.5 py-0.5 rounded">Privacy Mode</code> у{' '}
            <code className="text-indigo-300 bg-slate-800 px-1.5 py-0.5 rounded">@BotFather</code>), и группа
            автоматически появится здесь! Либо добавь группу по её ID вручную для импорта старой истории.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {groups.map((g) => (
            <div
              key={g.id}
              onClick={() => onOpenGroup(g.id)}
              className="group p-5 rounded-2xl bg-slate-900 hover:bg-slate-900/90 border border-slate-800 hover:border-indigo-500/50 transition cursor-pointer flex flex-col justify-between gap-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-base text-white group-hover:text-indigo-300 transition">
                      {g.title}
                    </h3>
                    {g.is_active ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                        <PlayCircle className="w-3 h-3" /> Активен
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] bg-slate-800 text-slate-400 border border-slate-700">
                        <PauseCircle className="w-3 h-3" /> Пауза
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-slate-500 mt-1">
                    Chat ID: <code className="text-slate-400">{g.id}</code> • Персона:{' '}
                    <span className="text-slate-300">{g.persona_name}</span>
                  </div>
                </div>
                <ChevronRight className="w-5 h-5 text-slate-600 group-hover:text-indigo-400 transition shrink-0" />
              </div>

              <div className="grid grid-cols-3 gap-2 pt-3 border-t border-slate-800/80 text-xs">
                <div>
                  <span className="text-slate-500 block">Участников</span>
                  <span className="text-slate-200 font-medium">{g.member_count}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Сообщений</span>
                  <span className="text-slate-200 font-medium">{g.message_count.toLocaleString('ru-RU')}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Буфер анализа</span>
                  <span className="text-amber-300 font-medium">{g.unanalyzed_msg_count} новых</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Модальное окно ручной регистрации группы */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
          <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
            <h3 className="text-lg font-semibold text-white">Зарегистрировать группу вручную</h3>
            <p className="text-xs text-slate-400">
              Полезно, если ты хочешь заранее загрузить JSON-экспорт истории чата из Telegram Desktop до первых
              новых сообщений.
            </p>
            <form onSubmit={handleManualAdd} className="space-y-3">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Telegram Chat ID</label>
                <input
                  type="text"
                  placeholder="-1001234567890"
                  value={manualId}
                  onChange={(e) => setManualId(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                  required
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Название группы</label>
                <input
                  type="text"
                  placeholder="Наш уютный чатик"
                  value={manualTitle}
                  onChange={(e) => setManualTitle(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                  required
                />
              </div>
              {addError && <div className="text-xs text-rose-400">{addError}</div>}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-4 py-2 rounded-xl bg-slate-800 text-xs text-slate-300 cursor-pointer"
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white cursor-pointer"
                >
                  Создать карточку
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}


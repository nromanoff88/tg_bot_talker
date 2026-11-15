import React, { useEffect, useRef, useState } from 'react';
import { BrainCircuit, CornerDownRight, RefreshCw, Send, Sparkles, X } from 'lucide-react';
import { api } from '../../api';
import type { ChatMessage, DecisionLog, GroupDetail } from '../../types';

interface Props {
  group: GroupDetail;
  onRefreshGroup: () => void;
}

const DECISION_BADGES: Record<string, { label: string; cls: string }> = {
  ignore: { label: 'Промолчал', cls: 'bg-slate-800 text-slate-300 border-slate-700' },
  react: { label: 'Реакция эмодзи', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/30' },
  reply: { label: 'Ответил реплаем', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' },
  message: { label: 'Написал в чат', cls: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30' },
  proactive: { label: 'Сам начал диалог', cls: 'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30' },
  memory_extract: { label: 'Изучение памяти', cls: 'bg-sky-500/15 text-sky-300 border-sky-500/30' },
  manual: { label: 'Отправлено админом', cls: 'bg-teal-500/15 text-teal-300 border-teal-500/30' },
  error: { label: 'Ошибка LLM API', cls: 'bg-rose-500/15 text-rose-300 border-rose-500/40' },
};

export function LiveChatThoughtsTab({ group, onRefreshGroup }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [decisions, setDecisions] = useState<DecisionLog[]>([]);
  const [manualText, setManualText] = useState('');
  const [replyToMsg, setReplyToMsg] = useState<ChatMessage | null>(null);
  const [sending, setSending] = useState(false);
  const [actionStatus, setActionStatus] = useState('');
  const chatBottomRef = useRef<HTMLDivElement | null>(null);

  const loadData = async () => {
    const [m, d] = await Promise.all([api.listMessages(group.id, 80), api.listDecisions(group.id, 50)]);
    setMessages(m);
    setDecisions(d);
  };

  useEffect(() => {
    loadData();

    // Подключаемся к live SSE потоку событий группы
    const es = new EventSource(`/api/groups/${group.id}/stream`, { withCredentials: true });
    es.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data);
        if (parsed.type === 'new_message') {
          setMessages((prev) => {
            if (prev.some((x) => x.telegram_message_id === parsed.data.telegram_message_id)) return prev;
            return [...prev.slice(-120), parsed.data];
          });
        } else if (parsed.type === 'decision_logged' || parsed.type === 'memory_updated') {
          api.listDecisions(group.id, 50).then(setDecisions).catch(() => {});
          onRefreshGroup();
        }
      } catch {
        // ignore
      }
    };

    return () => es.close();
  }, [group.id]);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length]);

  const handleSendManual = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualText.trim()) return;
    setSending(true);
    try {
      await api.sendManualMessage(group.id, manualText.trim(), replyToMsg?.telegram_message_id ?? null);
      setManualText('');
      setReplyToMsg(null);
      setActionStatus('Команда на отправку сообщения передана боту!');
      setTimeout(() => setActionStatus(''), 4000);
    } catch (err: any) {
      setActionStatus(`Ошибка: ${err.message}`);
    } finally {
      setSending(false);
    }
  };

  const handleForceMemory = async () => {
    setActionStatus('Запускаем фоновый анализ накопившихся сообщений...');
    try {
      const res = await api.forceAnalyzeMemory(group.id);
      setActionStatus(res.message);
    } catch (err: any) {
      setActionStatus(`Ошибка: ${err.message}`);
    }
  };

  const handleForceProactive = async () => {
    setActionStatus('Генерируем живой повод начать диалог...');
    try {
      const res = await api.forceProactive(group.id);
      if (res.status === 'sent') {
        setActionStatus(`Инициатива отправлена: «${res.text}»`);
      } else {
        setActionStatus(`Статус: ${res.status}`);
      }
      loadData();
    } catch (err: any) {
      setActionStatus(`Ошибка: ${err.message}`);
    }
  };

  return (
    <div className="space-y-4">
      {/* Панель быстрых действий */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-900 border border-slate-800 rounded-2xl p-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <button
            onClick={handleForceMemory}
            className="px-3.5 py-2 rounded-xl bg-sky-500/15 hover:bg-sky-500/25 border border-sky-500/30 text-sky-300 text-xs font-medium flex items-center gap-1.5 cursor-pointer"
          >
            <BrainCircuit className="w-4 h-4" />
            Изучить буфер памяти сейчас ({group.unanalyzed_msg_count})
          </button>

          <button
            onClick={handleForceProactive}
            className="px-3.5 py-2 rounded-xl bg-fuchsia-500/15 hover:bg-fuchsia-500/25 border border-fuchsia-500/30 text-fuchsia-300 text-xs font-medium flex items-center gap-1.5 cursor-pointer"
          >
            <Sparkles className="w-4 h-4" />
            Начать диалог сейчас (Инициатива)
          </button>
        </div>

        <button
          onClick={loadData}
          className="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 flex items-center gap-1.5 cursor-pointer"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Обновить ленту
        </button>
      </div>

      {actionStatus && (
        <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-xs text-indigo-200">
          {actionStatus}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Левая часть: Живой поток чата + ручная отправка от лица бота */}
        <div className="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-2xl flex flex-col h-[640px]">
          <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
            <span className="text-sm font-semibold text-white">Лента сообщений группы</span>
            <span className="text-[11px] text-emerald-400">● Live SSE</span>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {messages.length === 0 ? (
              <div className="text-xs text-slate-500 text-center py-16">
                Сообщений пока нет. Напишите что-нибудь в группе Telegram или импортируйте историю во вкладке «Импорт».
              </div>
            ) : (
              messages.map((m) => (
                <div
                  key={m.id || m.telegram_message_id}
                  className={`p-3 rounded-2xl max-w-[88%] text-xs space-y-1 ${
                    m.is_from_bot
                      ? 'ml-auto bg-indigo-600/20 border border-indigo-500/40 text-slate-100'
                      : 'bg-slate-950 border border-slate-800 text-slate-200'
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className={`font-semibold ${m.is_from_bot ? 'text-indigo-300' : 'text-emerald-300'}`}>
                      {m.sender_name}{' '}
                      <span className="font-normal text-slate-500">#{m.telegram_message_id}</span>
                    </span>
                    <div className="flex items-center gap-2">
                      {!m.is_from_bot && (
                        <button
                          type="button"
                          onClick={() => setReplyToMsg(m)}
                          className="text-[10px] text-slate-500 hover:text-indigo-300 cursor-pointer"
                        >
                          Ответить
                        </button>
                      )}
                      <span className="text-[10px] text-slate-500">
                        {new Date(m.created_at).toLocaleTimeString('ru-RU', {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>
                  </div>

                  {m.reply_to_tg_msg_id && (
                    <div className="text-[11px] text-slate-400 flex items-center gap-1">
                      <CornerDownRight className="w-3 h-3" /> Ответ на #{m.reply_to_tg_msg_id}
                    </div>
                  )}

                  <div className="whitespace-pre-wrap leading-relaxed">{m.text_content}</div>

                  {m.reactions && Object.keys(m.reactions).length > 0 && (
                    <div className="flex flex-wrap gap-1 pt-1">
                      {Object.entries(m.reactions).map(([emoji, count]) => (
                        <span
                          key={emoji}
                          className="px-1.5 py-0.5 rounded-full bg-slate-800 text-[11px] text-slate-200"
                        >
                          {emoji} {count}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
            <div ref={chatBottomRef} />
          </div>

          {/* Ручная отправка сообщения от лица бота */}
          <form onSubmit={handleSendManual} className="p-3 border-t border-slate-800 space-y-2">
            {replyToMsg && (
              <div className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-[11px] text-slate-300">
                <span className="truncate">
                  Ответ от лица бота на #{replyToMsg.telegram_message_id} ({replyToMsg.sender_name}):{' '}
                  {replyToMsg.text_content}
                </span>
                <button type="button" onClick={() => setReplyToMsg(null)} className="text-slate-500 hover:text-white">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            <div className="flex gap-2">
              <input
                type="text"
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder="Написать в группу от лица бота вручную..."
                className="flex-1 px-3.5 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
              />
              <button
                type="submit"
                disabled={sending || !manualText.trim()}
                className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-xs font-medium text-white flex items-center gap-1.5 cursor-pointer"
              >
                <Send className="w-3.5 h-3.5" />
                Отправить
              </button>
            </div>
          </form>
        </div>

        {/* Правая часть: Лог мыслей и решений бота (Decision Logs) */}
        <div className="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-2xl flex flex-col h-[640px]">
          <div className="px-5 py-3.5 border-b border-slate-800">
            <span className="text-sm font-semibold text-white">Мысли и решения бота (Decision Log)</span>
            <p className="text-[11px] text-slate-400">
              Почему промолчал или ответил, какие факты вспомнил и сколько потратил токенов
            </p>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {decisions.length === 0 ? (
              <div className="text-xs text-slate-500 text-center py-16">
                Лог решений пока пуст — он заполнится при первых сообщениях в группе.
              </div>
            ) : (
              decisions.map((d) => {
                const badge = DECISION_BADGES[d.decision_type] || {
                  label: d.decision_type,
                  cls: 'bg-slate-800 text-slate-300 border-slate-700',
                };
                const factsUsed = d.retrieved_context_meta?.facts_used || [];
                const episodesUsed = d.retrieved_context_meta?.episodes_used || [];

                return (
                  <div key={d.id} className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 space-y-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className={`px-2 py-0.5 rounded-full text-[11px] border ${badge.cls}`}>
                        {badge.label}
                        {d.action_payload?.emoji ? ` ${d.action_payload.emoji}` : ''}
                      </span>
                      <span className="text-[10px] text-slate-500">
                        {new Date(d.created_at).toLocaleString('ru-RU', {
                          day: '2-digit',
                          month: '2-digit',
                          hour: '2-digit',
                          minute: '2-digit',
                          second: '2-digit',
                        })}
                      </span>
                    </div>

                    <p className="text-slate-300 leading-relaxed">{d.router_reasoning}</p>

                    {d.action_payload?.text && (
                      <div className="p-2 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-200 text-[11px]">
                        «{d.action_payload.text}»
                      </div>
                    )}

                    {(factsUsed.length > 0 || episodesUsed.length > 0) && (
                      <details className="text-[11px] text-slate-400">
                        <summary className="cursor-pointer hover:text-slate-200">
                          Использовано из памяти: {factsUsed.length} факт., {episodesUsed.length} эпиз.
                        </summary>
                        <div className="mt-1.5 pl-2 border-l border-slate-800 space-y-1">
                          {episodesUsed.map((ep: any, i: number) => (
                            <div key={i} className="text-amber-300/90">
                              • [pgvector {Math.round((ep.similarity || 0) * 100)}%] {ep.title}
                            </div>
                          ))}
                          {factsUsed.slice(0, 8).map((f: any, i: number) => (
                            <div key={i}>
                              • {f.member}: {f.fact}
                            </div>
                          ))}
                        </div>
                      </details>
                    )}

                    {(d.prompt_tokens > 0 || d.cost_usd > 0) && (
                      <div className="flex items-center justify-between text-[10px] text-slate-500 pt-1 border-t border-slate-900">
                        <span className="truncate max-w-[180px]">{d.model_used}</span>
                        <span>
                          {d.prompt_tokens + d.completion_tokens} ток. • ${d.cost_usd.toFixed(5)}
                        </span>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


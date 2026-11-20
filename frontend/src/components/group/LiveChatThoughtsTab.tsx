import React, { useEffect, useRef, useState } from 'react';
import {
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  CornerDownRight,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from 'lucide-react';
import { api } from '../../api';
import type { ChatMessage, DecisionLog, GroupDetail, PollPayload } from '../../types';

interface Props {
  group: GroupDetail;
  onRefreshGroup: () => void;
}

const DECISION_BADGES: Record<string, { label: string; cls: string }> = {
  ignore: { label: 'Промолчал', cls: 'bg-slate-800 text-slate-300 border-slate-700' },
  react: { label: 'Реакция эмодзи', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/30' },
  reply: { label: 'Ответил реплаем', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' },
  message: { label: 'Написал в чат', cls: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30' },
  poll: { label: 'Создал опрос', cls: 'bg-violet-500/15 text-violet-300 border-violet-500/30' },
  proactive: { label: 'Сам начал диалог', cls: 'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30' },
  memory_extract: { label: 'Изучение памяти', cls: 'bg-sky-500/15 text-sky-300 border-sky-500/30' },
  manual: { label: 'Отправлено админом', cls: 'bg-teal-500/15 text-teal-300 border-teal-500/30' },
  error: { label: 'Ошибка LLM API', cls: 'bg-rose-500/15 text-rose-300 border-rose-500/40' },
};

const createDefaultPollDraft = (): PollPayload => ({
  question: '',
  options: ['', ''],
  is_anonymous: false,
  type: 'regular',
  allows_multiple_answers: false,
  correct_option_id: 0,
  explanation: '',
  open_period: null,
  is_closed: false,
  protect_content: false,
  reply_to_tg_msg_id: null,
});

export function LiveChatThoughtsTab({ group, onRefreshGroup }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [decisions, setDecisions] = useState<DecisionLog[]>([]);
  const [manualText, setManualText] = useState('');
  const [replyToMsg, setReplyToMsg] = useState<ChatMessage | null>(null);
  const [sending, setSending] = useState(false);
  const [actionStatus, setActionStatus] = useState('');
  const chatBottomRef = useRef<HTMLDivElement | null>(null);

  // Конструктор опросов
  const [showPollModal, setShowPollModal] = useState(false);
  const [pollDraft, setPollDraft] = useState<PollPayload>(createDefaultPollDraft);
  const [pollTopicHint, setPollTopicHint] = useState('');
  const [openPeriodInput, setOpenPeriodInput] = useState('');
  const [pollGenerating, setPollGenerating] = useState(false);
  const [pollSending, setPollSending] = useState(false);
  const [pollError, setPollError] = useState('');

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

  const openPollConstructor = () => {
    setPollError('');
    setPollDraft((prev) => ({
      ...createDefaultPollDraft(),
      type: prev.type,
      reply_to_tg_msg_id: replyToMsg?.telegram_message_id ?? null,
    }));
    setOpenPeriodInput('');
    setShowPollModal(true);
  };

  const handleGeneratePollWithAI = async () => {
    setPollGenerating(true);
    setPollError('');
    try {
      const generated = await api.generatePollDraft(group.id, {
        topic_hint: pollTopicHint.trim(),
        type: pollDraft.type,
        is_anonymous: pollDraft.is_anonymous,
        allows_multiple_answers: pollDraft.allows_multiple_answers,
      });
      setPollDraft({
        ...generated,
        explanation: generated.explanation || '',
        reply_to_tg_msg_id: replyToMsg?.telegram_message_id ?? null,
      });
      setOpenPeriodInput(generated.open_period ? String(generated.open_period) : '');
    } catch (err: any) {
      setPollError(`Не удалось сгенерировать опрос: ${err.message}`);
    } finally {
      setPollGenerating(false);
    }
  };

  const handleOptionChange = (idx: number, val: string) => {
    const next = [...pollDraft.options];
    next[idx] = val;
    setPollDraft({ ...pollDraft, options: next });
  };

  const handleAddOption = () => {
    if (pollDraft.options.length >= 10) return;
    setPollDraft({ ...pollDraft, options: [...pollDraft.options, ''] });
  };

  const handleRemoveOption = (idx: number) => {
    if (pollDraft.options.length <= 2) return;
    const next = pollDraft.options.filter((_, i) => i !== idx);
    let nextCorrect = pollDraft.correct_option_id ?? 0;
    if (nextCorrect >= next.length) nextCorrect = 0;
    setPollDraft({ ...pollDraft, options: next, correct_option_id: nextCorrect });
  };

  const handleSendPollSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPollError('');

    const question = pollDraft.question.trim();
    const cleanedOptions = pollDraft.options.map((o) => o.trim()).filter(Boolean);
    if (!question) {
      setPollError('Введите вопрос опроса.');
      return;
    }
    if (cleanedOptions.length < 2) {
      setPollError('Нужно минимум 2 непустых варианта ответа.');
      return;
    }

    let parsedOpenPeriod: number | null = null;
    if (openPeriodInput.trim()) {
      const n = Number(openPeriodInput.trim());
      if (!Number.isInteger(n) || n < 5 || n > 600) {
        setPollError('Таймер автозакрытия должен быть от 5 до 600 секунд (или оставьте пустым).');
        return;
      }
      parsedOpenPeriod = n;
    }

    const payload: PollPayload = {
      ...pollDraft,
      question: question.slice(0, 300),
      options: cleanedOptions.slice(0, 10).map((o) => o.slice(0, 100)),
      allows_multiple_answers: pollDraft.type === 'quiz' ? false : pollDraft.allows_multiple_answers,
      correct_option_id:
        pollDraft.type === 'quiz'
          ? Math.min(Math.max(0, pollDraft.correct_option_id ?? 0), cleanedOptions.length - 1)
          : null,
      explanation: pollDraft.type === 'quiz' ? (pollDraft.explanation || '').trim().slice(0, 200) || null : null,
      open_period: parsedOpenPeriod,
      reply_to_tg_msg_id: replyToMsg?.telegram_message_id ?? pollDraft.reply_to_tg_msg_id ?? null,
    };

    setPollSending(true);
    try {
      await api.sendPoll(group.id, payload);
      setShowPollModal(false);
      setReplyToMsg(null);
      setActionStatus(`Опрос «${payload.question}» отправлен в группу!`);
      setTimeout(() => setActionStatus(''), 5000);
    } catch (err: any) {
      setPollError(`Ошибка отправки: ${err.message}`);
    } finally {
      setPollSending(false);
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

          <button
            onClick={openPollConstructor}
            className="px-3.5 py-2 rounded-xl bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-300 text-xs font-medium flex items-center gap-1.5 cursor-pointer"
          >
            <BarChart3 className="w-4 h-4" />
            Создать опрос
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

          {/* Ручная отправка сообщения или опроса от лица бота */}
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
                type="button"
                onClick={openPollConstructor}
                title="Создать опрос в группе"
                className="px-3 py-2 rounded-xl bg-violet-600/20 hover:bg-violet-600/35 border border-violet-500/30 text-xs font-medium text-violet-300 flex items-center gap-1.5 cursor-pointer"
              >
                <BarChart3 className="w-3.5 h-3.5" />
                Опрос
              </button>
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
                const pollPayload = d.action_payload?.poll;

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

                    {pollPayload?.question && (
                      <div className="p-2.5 rounded-lg bg-violet-500/10 border border-violet-500/25 text-violet-200 text-[11px] space-y-1">
                        <div className="font-semibold">
                          📊 {pollPayload.type === 'quiz' ? 'Викторина: ' : 'Опрос: '}
                          {pollPayload.question}
                        </div>
                        {Array.isArray(pollPayload.options) && (
                          <div className="text-violet-300/90 space-y-0.5">
                            {pollPayload.options.map((opt: string, idx: number) => (
                              <div key={idx}>
                                • {opt}
                                {pollPayload.type === 'quiz' && pollPayload.correct_option_id === idx ? ' ✅' : ''}
                              </div>
                            ))}
                          </div>
                        )}
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

      {/* Модальное окно конструктора опросов со всеми параметрами Telegram Bot API */}
      {showPollModal && (
        <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4 overflow-y-auto">
          <div className="w-full max-w-2xl bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-5 my-8">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2.5">
                <BarChart3 className="w-5 h-5 text-violet-400" />
                <div>
                  <h3 className="text-base font-semibold text-white">Создать опрос в Telegram-группе</h3>
                  <p className="text-xs text-slate-400">
                    Поддерживаются все параметры Telegram Polls (обычный опрос, викторина, анонимность, таймер и др.)
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowPollModal(false)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Блок ИИ-генерации черновика */}
            <div className="p-3.5 rounded-xl bg-slate-950 border border-violet-500/30 space-y-2">
              <label className="block text-xs text-violet-300 font-medium">
                Помощник ИИ (придумать вопрос и варианты в характере бота)
              </label>
              <div className="flex flex-col sm:flex-row gap-2">
                <input
                  type="text"
                  value={pollTopicHint}
                  onChange={(e) => setPollTopicHint(e.target.value)}
                  placeholder="Тема опроса (или оставьте пустым — бот придумает по последнему контексту чата)..."
                  className="flex-1 px-3 py-2 bg-slate-900 border border-slate-800 rounded-xl text-xs text-white"
                />
                <button
                  type="button"
                  disabled={pollGenerating}
                  onClick={handleGeneratePollWithAI}
                  className="px-4 py-2 rounded-xl bg-violet-600/25 hover:bg-violet-600/40 border border-violet-500/40 text-violet-200 text-xs font-medium flex items-center justify-center gap-1.5 cursor-pointer shrink-0"
                >
                  <Wand2 className={`w-3.5 h-3.5 ${pollGenerating ? 'animate-spin' : ''}`} />
                  {pollGenerating ? 'Придумываю...' : 'Придумать через ИИ'}
                </button>
              </div>
            </div>

            <form onSubmit={handleSendPollSubmit} className="space-y-4">
              {/* Тип опроса */}
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setPollDraft({ ...pollDraft, type: 'regular' })}
                  className={`p-3 rounded-xl border text-left text-xs transition cursor-pointer ${
                    pollDraft.type === 'regular'
                      ? 'bg-violet-600/20 border-violet-500 text-white'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
                  }`}
                >
                  <div className="font-semibold">Обычный опрос (regular)</div>
                  <div className="text-[11px] opacity-75 mt-0.5">Свободное голосование, можно включить мультивыбор</div>
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setPollDraft({
                      ...pollDraft,
                      type: 'quiz',
                      allows_multiple_answers: false,
                      correct_option_id: pollDraft.correct_option_id ?? 0,
                    })
                  }
                  className={`p-3 rounded-xl border text-left text-xs transition cursor-pointer ${
                    pollDraft.type === 'quiz'
                      ? 'bg-violet-600/20 border-violet-500 text-white'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
                  }`}
                >
                  <div className="font-semibold">Викторина (quiz)</div>
                  <div className="text-[11px] opacity-75 mt-0.5">Один правильный ответ и пояснение после выбора</div>
                </button>
              </div>

              {/* Вопрос */}
              <div>
                <div className="flex justify-between text-xs text-slate-400 mb-1">
                  <span>Вопрос опроса</span>
                  <span>{pollDraft.question.length}/300</span>
                </div>
                <input
                  type="text"
                  maxLength={300}
                  value={pollDraft.question}
                  onChange={(e) => setPollDraft({ ...pollDraft, question: e.target.value })}
                  placeholder="Например: Куда идём в субботу вечером?"
                  className="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                  required
                />
              </div>

              {/* Варианты ответа */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-400">
                    Варианты ответа (от 2 до 10, до 100 символов каждый)
                    {pollDraft.type === 'quiz' ? ' — отметьте правильный' : ''}
                  </span>
                  {pollDraft.options.length < 10 && (
                    <button
                      type="button"
                      onClick={handleAddOption}
                      className="text-violet-300 hover:text-violet-200 flex items-center gap-1 cursor-pointer"
                    >
                      <Plus className="w-3.5 h-3.5" /> Добавить вариант
                    </button>
                  )}
                </div>

                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {pollDraft.options.map((opt, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      {pollDraft.type === 'quiz' && (
                        <button
                          type="button"
                          onClick={() => setPollDraft({ ...pollDraft, correct_option_id: idx })}
                          className={`p-1.5 rounded-full border cursor-pointer shrink-0 ${
                            (pollDraft.correct_option_id ?? 0) === idx
                              ? 'bg-emerald-500/20 border-emerald-400 text-emerald-300'
                              : 'bg-slate-950 border-slate-700 text-slate-600 hover:text-slate-400'
                          }`}
                          title="Отметить как правильный ответ"
                        >
                          <CheckCircle2 className="w-4 h-4" />
                        </button>
                      )}
                      <input
                        type="text"
                        maxLength={100}
                        value={opt}
                        onChange={(e) => handleOptionChange(idx, e.target.value)}
                        placeholder={`Вариант ${idx + 1}`}
                        className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                        required={idx < 2}
                      />
                      {pollDraft.options.length > 2 && (
                        <button
                          type="button"
                          onClick={() => handleRemoveOption(idx)}
                          className="p-2 text-slate-500 hover:text-rose-400 cursor-pointer"
                          title="Удалить вариант"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Объяснение (для quiz) */}
              {pollDraft.type === 'quiz' && (
                <div>
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>Пояснение к правильному ответу (explanation, необязательно)</span>
                    <span>{(pollDraft.explanation || '').length}/200</span>
                  </div>
                  <input
                    type="text"
                    maxLength={200}
                    value={pollDraft.explanation || ''}
                    onChange={(e) => setPollDraft({ ...pollDraft, explanation: e.target.value })}
                    placeholder="Появится у участника в Telegram после выбора ответа"
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                  />
                </div>
              )}

              {/* Дополнительные параметры Telegram sendPoll */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2 border-t border-slate-800">
                <label className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={pollDraft.is_anonymous}
                    onChange={(e) => setPollDraft({ ...pollDraft, is_anonymous: e.target.checked })}
                  />
                  Анонимное голосование (is_anonymous)
                </label>

                <label
                  className={`flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-950 border border-slate-800 text-xs cursor-pointer ${
                    pollDraft.type === 'quiz' ? 'opacity-40 cursor-not-allowed text-slate-500' : 'text-slate-200'
                  }`}
                >
                  <input
                    type="checkbox"
                    disabled={pollDraft.type === 'quiz'}
                    checked={pollDraft.type === 'quiz' ? false : pollDraft.allows_multiple_answers}
                    onChange={(e) => setPollDraft({ ...pollDraft, allows_multiple_answers: e.target.checked })}
                  />
                  Несколько вариантов (allows_multiple_answers)
                </label>

                <label className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={pollDraft.protect_content}
                    onChange={(e) => setPollDraft({ ...pollDraft, protect_content: e.target.checked })}
                  />
                  Запретить пересылку (protect_content)
                </label>

                <label className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={pollDraft.is_closed}
                    onChange={(e) => setPollDraft({ ...pollDraft, is_closed: e.target.checked })}
                  />
                  Отправить сразу закрытым (is_closed)
                </label>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-center">
                <div>
                  <label className="block text-xs text-slate-400 mb-1">
                    Таймер автозакрытия в секундах (open_period, 5–600)
                  </label>
                  <input
                    type="number"
                    min="5"
                    max="600"
                    value={openPeriodInput}
                    onChange={(e) => setOpenPeriodInput(e.target.value)}
                    placeholder="Пусто — без таймера"
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                  />
                </div>

                <div className="text-xs text-slate-400">
                  {replyToMsg ? (
                    <div className="p-2.5 rounded-xl bg-slate-950 border border-slate-800 flex items-center justify-between">
                      <span className="truncate">Реплай на #{replyToMsg.telegram_message_id}</span>
                      <button
                        type="button"
                        onClick={() => setReplyToMsg(null)}
                        className="text-slate-500 hover:text-white ml-2 cursor-pointer"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : (
                    <span>Будет отправлен как самостоятельный опрос в чат.</span>
                  )}
                </div>
              </div>

              {pollError && (
                <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
                  {pollError}
                </div>
              )}

              <div className="flex justify-end gap-2.5 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setShowPollModal(false)}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 cursor-pointer"
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  disabled={pollSending}
                  className="px-5 py-2 rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-xs font-medium text-white flex items-center gap-1.5 cursor-pointer"
                >
                  <Send className="w-3.5 h-3.5" />
                  {pollSending ? 'Отправка...' : 'Запустить опрос в группе'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

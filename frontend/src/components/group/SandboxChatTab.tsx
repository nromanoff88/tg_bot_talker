import React, { useEffect, useRef, useState } from 'react';
import {
  Bot,
  Eraser,
  FlaskConical,
  Info,
  Send,
  Sparkles,
  User,
} from 'lucide-react';
import { api } from '../../api';
import type {
  GroupDetail,
  GroupMember,
  SandboxChatMessage,
} from '../../types';

interface Props {
  group: GroupDetail;
}

interface SandboxEntry {
  id: string;
  role: 'user' | 'bot';
  senderName: string;
  memberId?: string | null;
  createdAt: string;
  text: string;
  tagBot?: boolean;
  isDirectPing?: boolean;
  action?: 'ignore' | 'react' | 'reply' | 'message' | 'poll' | 'proactive';
  emoji?: string | null;
  pollData?: Record<string, any> | null;
  reasoning?: string;
  newTopicMode?: boolean;
  silenceHours?: number;
  gateNotes?: string[];
  modelUsed?: string;
  promptTokens?: number;
  completionTokens?: number;
  costUsd?: number;
  retrievedContextMeta?: Record<string, any>;
}

const ACTION_BADGES: Record<string, { label: string; cls: string }> = {
  reply: { label: 'Ответил реплаем', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' },
  message: { label: 'Написал в чат', cls: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30' },
  react: { label: 'Реакция эмодзи', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/30' },
  poll: { label: 'Создал опрос', cls: 'bg-violet-500/15 text-violet-300 border-violet-500/30' },
  ignore: { label: 'Промолчал', cls: 'bg-slate-800 text-slate-300 border-slate-700' },
};

export function SandboxChatTab({ group }: Props) {
  const [members, setMembers] = useState<GroupMember[]>([]);
  const [selectedMemberId, setSelectedMemberId] = useState<string>('');
  const [customSenderName, setCustomSenderName] = useState<string>('Админ');
  const [includeRealHistory, setIncludeRealHistory] = useState<boolean>(true);
  const [tagBot, setTagBot] = useState<boolean>(true);
  const [proactiveMode, setProactiveMode] = useState<'auto' | 'revive' | 'new_topic'>('auto');
  const [silenceOverride, setSilenceOverride] = useState<string>('');

  const [entries, setEntries] = useState<SandboxEntry[]>([]);
  const [inputText, setInputText] = useState<string>('');
  const [loadingChat, setLoadingChat] = useState<boolean>(false);
  const [loadingProactive, setLoadingProactive] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string>('');

  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setEntries([]);
    setErrorMsg('');
    api
      .listMembers(group.id)
      .then((list) => setMembers(list))
      .catch(() => setMembers([]));
  }, [group.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [entries.length, loadingChat, loadingProactive]);

  const toSandboxHistory = (list: SandboxEntry[]): SandboxChatMessage[] => {
    const out: SandboxChatMessage[] = [];
    for (const item of list) {
      if (item.role === 'user') {
        out.push({
          role: 'user',
          text: item.text,
          sender_name: item.senderName,
          member_id: item.memberId || null,
          created_at: item.createdAt,
        });
      } else if (item.action === 'poll' && item.pollData?.question) {
        const opts = Array.isArray(item.pollData.options) ? item.pollData.options.join(', ') : '';
        out.push({
          role: 'bot',
          text: `[Опрос] ${item.pollData.question}${opts ? ` (${opts})` : ''}`,
          created_at: item.createdAt,
        });
      } else if (item.action !== 'ignore' && item.action !== 'react' && item.text.trim()) {
        out.push({
          role: 'bot',
          text: item.text,
          created_at: item.createdAt,
        });
      }
    }
    return out;
  };

  const resolveCurrentSender = () => {
    if (selectedMemberId) {
      const m = members.find((x) => x.id === selectedMemberId);
      if (m) {
        return { memberId: m.id, senderName: m.display_name };
      }
    }
    return { memberId: null, senderName: customSenderName.trim() || 'Админ' };
  };

  const handleSend = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const cleanText = inputText.trim();
    if (!cleanText || loadingChat || loadingProactive) return;

    setErrorMsg('');
    const { memberId, senderName } = resolveCurrentSender();
    const nowIso = new Date().toISOString();
    const usedTagBot = tagBot;
    const userEntry: SandboxEntry = {
      id: `u-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: 'user',
      senderName,
      memberId,
      createdAt: nowIso,
      text: cleanText,
      tagBot: usedTagBot,
    };

    const nextEntries = [...entries, userEntry];
    setEntries(nextEntries);
    setInputText('');
    setLoadingChat(true);

    try {
      const res = await api.sandboxChat(group.id, {
        messages: toSandboxHistory(nextEntries),
        include_real_history: includeRealHistory,
        tag_bot: usedTagBot,
      });
      const botEntry: SandboxEntry = {
        id: `b-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: 'bot',
        senderName: res.persona_name || 'Бот',
        createdAt: new Date().toISOString(),
        text: res.text || '',
        tagBot: usedTagBot,
        isDirectPing: Boolean(res.retrieved_context_meta?.is_direct_ping),
        action: res.action,
        emoji: res.emoji,
        pollData: res.poll_data,
        reasoning: res.reasoning,
        modelUsed: res.model_used,
        promptTokens: res.prompt_tokens,
        completionTokens: res.completion_tokens,
        costUsd: res.cost_usd,
        retrievedContextMeta: res.retrieved_context_meta,
      };
      setEntries((prev) => [...prev, botEntry]);
    } catch (err: any) {
      setErrorMsg(err?.message || 'Ошибка генерации ответа в песочнице');
    } finally {
      setLoadingChat(false);
    }
  };

  const handleTestProactive = async () => {
    if (loadingChat || loadingProactive) return;
    setErrorMsg('');
    setLoadingProactive(true);

    const parsedSilence = silenceOverride.trim() !== '' ? Number(silenceOverride.trim()) : null;
    const silenceHoursOverride =
      parsedSilence !== null && !Number.isNaN(parsedSilence) && parsedSilence >= 0 ? parsedSilence : null;

    try {
      const res = await api.sandboxProactive(group.id, {
        mode: proactiveMode,
        silence_hours_override: silenceHoursOverride,
        sandbox_messages: toSandboxHistory(entries),
        include_real_history: includeRealHistory,
      });
      const botEntry: SandboxEntry = {
        id: `p-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: 'bot',
        senderName: res.persona_name || 'Бот',
        createdAt: new Date().toISOString(),
        text: res.text,
        action: 'proactive',
        newTopicMode: res.new_topic_mode,
        silenceHours: res.silence_hours,
        gateNotes: res.gate_notes,
        modelUsed: res.model_used,
        promptTokens: res.prompt_tokens,
        completionTokens: res.completion_tokens,
        costUsd: res.cost_usd,
        retrievedContextMeta: res.retrieved_context_meta,
      };
      setEntries((prev) => [...prev, botEntry]);
    } catch (err: any) {
      setErrorMsg(err?.message || 'Ошибка тестирования инициативы');
    } finally {
      setLoadingProactive(false);
    }
  };

  const busy = loadingChat || loadingProactive;

  return (
    <div className="space-y-4">
      {/* Banner */}
      <div className="bg-indigo-950/30 border border-indigo-500/30 rounded-2xl p-4 flex items-start gap-3">
        <FlaskConical className="w-5 h-5 text-indigo-400 shrink-0 mt-0.5" />
        <div className="text-xs text-slate-300 leading-relaxed">
          <span className="font-semibold text-indigo-300">Режим песочницы:</span> бот оценивает сообщение с учётом
          текущей персоны, промптов, общительности, досье участников и pgvector-памяти (в режиме с тегом или без него), но{' '}
          <span className="text-white font-medium">ничего не сохраняет в базу данных</span>, не запоминает эти реплики
          и не отправляет сообщения в Telegram.
        </div>
      </div>

      {/* Controls Bar */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-3">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 items-end">
          <div className="lg:col-span-4">
            <label className="block text-[11px] text-slate-400 mb-1">Писать от имени</label>
            <select
              value={selectedMemberId}
              onChange={(e) => setSelectedMemberId(e.target.value)}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
            >
              <option value="">Гость / Админ (без досье)</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                  {m.chat_nickname ? ` (${m.chat_nickname})` : ''}
                  {m.username ? ` @${m.username}` : ''} · {m.facts_count} факт.
                </option>
              ))}
            </select>
          </div>

          {!selectedMemberId && (
            <div className="lg:col-span-3">
              <label className="block text-[11px] text-slate-400 mb-1">Имя отправителя</label>
              <input
                type="text"
                value={customSenderName}
                onChange={(e) => setCustomSenderName(e.target.value)}
                placeholder="Админ"
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
              />
            </div>
          )}

          <div className={selectedMemberId ? 'lg:col-span-5' : 'lg:col-span-3'}>
            <label className="flex items-center gap-2 px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={includeRealHistory}
                onChange={(e) => setIncludeRealHistory(e.target.checked)}
              />
              Учитывать реальную историю чата
            </label>
          </div>

          <div className={selectedMemberId ? 'lg:col-span-3 flex justify-end' : 'lg:col-span-2 flex justify-end'}>
            <button
              type="button"
              onClick={() => {
                setEntries([]);
                setErrorMsg('');
              }}
              disabled={entries.length === 0 || busy}
              className="w-full lg:w-auto px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-xs text-slate-200 flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <Eraser className="w-3.5 h-3.5" />
              Очистить чат
            </button>
          </div>
        </div>

        {/* Proactive Test Bar */}
        <div className="pt-3 border-t border-slate-800 flex flex-wrap items-end gap-3 justify-between">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-[11px] text-slate-400 mb-1">Режим инициативы</label>
              <select
                value={proactiveMode}
                onChange={(e) => setProactiveMode(e.target.value as 'auto' | 'revive' | 'new_topic')}
                className="px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
              >
                <option value="auto">Авто (по времени тишины)</option>
                <option value="revive">Оживить текущий диалог</option>
                <option value="new_topic">Поднять новую тему</option>
              </select>
            </div>

            <div>
              <label className="block text-[11px] text-slate-400 mb-1">Тишина (часов, опц.)</label>
              <input
                type="number"
                min="0"
                max="720"
                step="0.5"
                value={silenceOverride}
                onChange={(e) => setSilenceOverride(e.target.value)}
                placeholder="авто"
                className="w-28 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
              />
            </div>
          </div>

          <button
            type="button"
            onClick={handleTestProactive}
            disabled={busy}
            className="px-4 py-2 rounded-xl bg-fuchsia-600/20 hover:bg-fuchsia-600/30 disabled:opacity-40 text-fuchsia-300 border border-fuchsia-500/30 text-xs font-medium flex items-center gap-1.5 cursor-pointer"
          >
            <Sparkles className="w-3.5 h-3.5" />
            {loadingProactive ? 'Генерация инициативы...' : 'Протестировать инициативу'}
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
          {errorMsg}
        </div>
      )}

      {/* Chat Stream */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl flex flex-col h-[580px]">
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {entries.length === 0 ? (
            <div className="text-xs text-slate-500 text-center py-20 space-y-2">
              <p>Напишите сообщение ниже (с галочкой «Тегнуть бота» или без неё) либо протестируйте инициативу.</p>
              <p className="text-[11px] text-slate-600">
                Под каждым ответом или молчанием бота сразу видно объяснение модели из роутера — почему она решила ответить, поставить реакцию или промолчать.
              </p>
            </div>
          ) : (
            entries.map((entry) => {
              const isUser = entry.role === 'user';
              const llmPrompts = entry.retrievedContextMeta?.llm_prompts;
              const factsUsed = entry.retrievedContextMeta?.facts_used || [];
              const episodesUsed = entry.retrievedContextMeta?.episodes_used || [];
              const promptSections = [
                { label: 'Smart LLM — System Prompt', text: llmPrompts?.smart_system_prompt },
                { label: 'Smart LLM — User Prompt', text: llmPrompts?.smart_user_prompt },
                { label: 'Fast LLM (Роутер) — System Prompt', text: llmPrompts?.router_system_prompt },
                { label: 'Fast LLM (Роутер) — User Prompt', text: llmPrompts?.router_user_prompt },
              ].filter((s): s is { label: string; text: string } => typeof s.text === 'string' && s.text.length > 0);

              let badgeLabel = '';
              let badgeCls = 'bg-slate-800 text-slate-300 border-slate-700';
              if (entry.action === 'proactive') {
                badgeLabel = entry.newTopicMode ? 'Инициатива · Новая тема' : 'Инициатива · Оживление';
                badgeCls = 'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30';
              } else if (entry.action && ACTION_BADGES[entry.action]) {
                badgeLabel = ACTION_BADGES[entry.action].label;
                badgeCls = ACTION_BADGES[entry.action].cls;
              }

              return (
                <div
                  key={entry.id}
                  className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[85%] rounded-2xl p-3.5 text-xs space-y-2 border ${
                      isUser
                        ? 'bg-indigo-600/20 border-indigo-500/35 text-slate-100'
                        : 'bg-slate-950 border-slate-800 text-slate-200'
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {isUser ? (
                          <User className="w-3.5 h-3.5 text-indigo-300" />
                        ) : (
                          <Bot className="w-3.5 h-3.5 text-emerald-400" />
                        )}
                        <span className={`font-semibold ${isUser ? 'text-indigo-200' : 'text-emerald-300'}`}>
                          {entry.senderName}
                        </span>
                        {isUser && entry.tagBot !== undefined && (
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] border ${
                              entry.tagBot
                                ? 'bg-indigo-500/20 text-indigo-200 border-indigo-400/30'
                                : 'bg-slate-800/80 text-slate-400 border-slate-700'
                            }`}
                          >
                            {entry.tagBot ? 'С тегом бота' : 'Без тега'}
                          </span>
                        )}
                        {badgeLabel && (
                          <span className={`px-2 py-0.5 rounded-full text-[10px] border ${badgeCls}`}>
                            {badgeLabel}
                            {entry.emoji ? ` ${entry.emoji}` : ''}
                          </span>
                        )}
                        {!isUser && entry.action !== 'proactive' && entry.tagBot === false && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] border bg-slate-900 text-slate-400 border-slate-700">
                            {entry.isDirectPing ? 'Упомянут в тексте' : 'Без тега'}
                          </span>
                        )}
                      </div>
                      <span className="text-[10px] text-slate-500">
                        {new Date(entry.createdAt).toLocaleTimeString('ru-RU', {
                          hour: '2-digit',
                          minute: '2-digit',
                          second: '2-digit',
                        })}
                      </span>
                    </div>

                    {entry.text && (
                      <div className="whitespace-pre-wrap leading-relaxed text-[13px]">{entry.text}</div>
                    )}

                    {entry.action === 'react' && entry.emoji && (
                      <div className="text-lg">Реакция на сообщение: {entry.emoji}</div>
                    )}

                    {entry.action === 'poll' && entry.pollData?.question && (
                      <div className="p-2.5 rounded-xl bg-violet-500/10 border border-violet-500/25 text-violet-200 space-y-1">
                        <div className="font-semibold">
                          📊 {entry.pollData.type === 'quiz' ? 'Викторина: ' : 'Опрос: '}
                          {entry.pollData.question}
                        </div>
                        {Array.isArray(entry.pollData.options) && (
                          <div className="text-violet-300/90 space-y-0.5">
                            {entry.pollData.options.map((opt: string, idx: number) => (
                              <div key={idx}>
                                • {opt}
                                {entry.pollData?.type === 'quiz' && entry.pollData?.correct_option_id === idx ? ' ✅' : ''}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {!isUser && entry.reasoning && (
                      <div className="p-2.5 rounded-xl bg-slate-900/90 border border-slate-800 space-y-1">
                        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                          Мысль модели (почему {entry.action === 'ignore' ? 'промолчала' : 'вмешалась'})
                        </div>
                        <p className="text-slate-300 leading-relaxed">{entry.reasoning}</p>
                      </div>
                    )}

                    {entry.gateNotes && entry.gateNotes.length > 0 && (
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1 text-[11px] text-slate-300">
                        <div className="flex items-center gap-1.5 text-slate-400 font-medium">
                          <Info className="w-3.5 h-3.5 text-sky-400" />
                          <span>
                            Диагностика планировщика (тишина: {entry.silenceHours ?? 0} ч):
                          </span>
                        </div>
                        {entry.gateNotes.map((note, idx) => (
                          <div key={idx} className="text-slate-300">
                            • {note}
                          </div>
                        ))}
                      </div>
                    )}

                    {!isUser && (promptSections.length > 0 || factsUsed.length > 0 || episodesUsed.length > 0 || entry.modelUsed) && (
                      <details className="pt-1 border-t border-slate-800/80 text-[11px] text-slate-400">
                        <summary className="cursor-pointer hover:text-indigo-300 text-indigo-400/90 font-medium">
                          Память и промпты
                          {entry.modelUsed ? ` · ${entry.modelUsed}` : ''}
                          {typeof entry.costUsd === 'number' && entry.costUsd > 0 ? ` ($${entry.costUsd.toFixed(5)})` : ''}
                        </summary>
                        <div className="mt-2 space-y-2 pl-2 border-l border-indigo-500/30">
                          {(factsUsed.length > 0 || episodesUsed.length > 0) && (
                            <div className="space-y-1">
                              <div className="text-slate-500">
                                Использовано из памяти: {factsUsed.length} факт., {episodesUsed.length} эпиз.
                              </div>
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
                          )}
                          {(entry.promptTokens || entry.completionTokens) ? (
                            <div className="text-[10px] text-slate-500">
                              Токены: in {entry.promptTokens ?? 0} / out {entry.completionTokens ?? 0}
                            </div>
                          ) : null}
                          {promptSections.map((sec) => (
                            <details key={sec.label}>
                              <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-wide text-slate-400 hover:text-slate-200">
                                {sec.label} ({sec.text.length} симв.)
                              </summary>
                              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed bg-slate-900 border border-slate-800 rounded-lg p-2.5 max-h-64 overflow-y-auto text-slate-300">
                                {sec.text}
                              </pre>
                            </details>
                          ))}
                        </div>
                      </details>
                    )}
                  </div>
                </div>
              );
            })
          )}
          {busy && (
            <div className="flex justify-start">
              <div className="px-4 py-2.5 rounded-2xl bg-slate-950 border border-slate-800 text-xs text-slate-400">
                {loadingProactive ? 'Придумывает повод написать...' : 'Оценивает сообщение и готовит ответ...'}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Message Composer */}
        <form onSubmit={handleSend} className="p-3 border-t border-slate-800 flex flex-col sm:flex-row gap-2.5 sm:items-end">
          <textarea
            rows={2}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Напишите сообщение боту (Enter — отправить, Shift+Enter — перенос строки)..."
            disabled={busy}
            className="flex-1 px-3.5 py-2.5 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white focus:outline-none focus:border-indigo-500 resize-none disabled:opacity-50"
          />
          <div className="flex items-center justify-between sm:justify-end gap-3 shrink-0">
            <label
              className="flex items-center gap-2 px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer select-none h-10"
              title="Если включено — сообщение уйдёт как прямой тег/реплай боту. Если снято — как обычное сообщение в чат (бот сам решает, вмешиваться ли)."
            >
              <input
                type="checkbox"
                checked={tagBot}
                onChange={(e) => setTagBot(e.target.checked)}
                disabled={busy}
              />
              <span>Тегнуть бота</span>
            </label>
            <button
              type="submit"
              disabled={!inputText.trim() || busy}
              className="px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-xs font-medium text-white flex items-center gap-1.5 cursor-pointer h-10"
            >
              <Send className="w-3.5 h-3.5" />
              Отправить
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

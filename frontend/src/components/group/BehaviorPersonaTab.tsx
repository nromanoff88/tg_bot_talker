import React, { useState } from 'react';
import { Save } from 'lucide-react';
import { api } from '../../api';
import type { GroupDetail, GroupSettings, PersonaOverride, PersonaTemplate } from '../../types';

interface Props {
  group: GroupDetail;
  personas: PersonaTemplate[];
  onSaved: () => void;
}

const COMMON_TIMEZONES = [
  'Europe/Kaliningrad',
  'Europe/Moscow',
  'Europe/Samara',
  'Asia/Yekaterinburg',
  'Asia/Omsk',
  'Asia/Novosibirsk',
  'Asia/Irkutsk',
  'Asia/Vladivostok',
  'Europe/Kyiv',
  'Asia/Almaty',
  'Asia/Tashkent',
  'UTC',
];

export function BehaviorPersonaTab({ group, personas, onSaved }: Props) {
  const [templateId, setTemplateId] = useState<string>(group.persona_template_id || '');
  const [override, setOverride] = useState<PersonaOverride>({
    use_custom: Boolean(group.persona_override?.use_custom),
    display_name: group.persona_override?.display_name || 'Макс',
    backstory: group.persona_override?.backstory || '',
    communication_style: group.persona_override?.communication_style || '',
    taboos: group.persona_override?.taboos || '',
    example_phrases: group.persona_override?.example_phrases || [],
  });
  const [examplesText, setExamplesText] = useState(
    (group.persona_override?.example_phrases || []).join('\n')
  );

  const [settings, setSettings] = useState<GroupSettings>(group.settings);
  const [emojisText, setEmojisText] = useState(group.settings.allowed_emojis.join(' '));
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const handleCopyFromTemplate = () => {
    const tpl = personas.find((p) => p.id === templateId) || personas[0];
    if (!tpl) return;
    setOverride({
      use_custom: true,
      display_name: tpl.display_name,
      backstory: tpl.backstory,
      communication_style: tpl.communication_style,
      taboos: tpl.taboos,
      example_phrases: tpl.example_phrases,
    });
    setExamplesText((tpl.example_phrases || []).join('\n'));
  };

  const handleSaveAll = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setNotice('');
    try {
      const cleanedEmojis = emojisText
        .split(/[\s,]+/)
        .map((x) => x.trim())
        .filter(Boolean);

      await Promise.all([
        api.updateGroupPersona(group.id, {
          is_active: group.is_active,
          persona_template_id: templateId || null,
          persona_override: {
            ...override,
            example_phrases: examplesText
              .split('\n')
              .map((x) => x.trim())
              .filter(Boolean),
          },
        }),
        api.updateGroupSettings(group.id, {
          ...settings,
          allowed_emojis: cleanedEmojis.length > 0 ? cleanedEmojis : ['👍', '🔥', '😂'],
        }),
      ]);

      onSaved();
      setNotice('Персона и настройки поведения в группе сохранены!');
    } catch (err: any) {
      setNotice(`Ошибка сохранения: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSaveAll} className="space-y-6">
      {/* Секция 1: Личность (Персона) в этой группе */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-base font-semibold text-white">1. Личность бота в этой группе</h3>
            <p className="text-xs text-slate-400">
              Можно использовать общий шаблон или задать полностью уникальную легенду специально под этот чат
            </p>
          </div>
          {override.use_custom && (
            <button
              type="button"
              onClick={handleCopyFromTemplate}
              className="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 cursor-pointer"
            >
              Скопировать текст из выбранного шаблона
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-end">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Базовый шаблон Персоны</label>
            <select
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            >
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.display_name})
                </option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={Boolean(override.use_custom)}
              onChange={(e) => setOverride({ ...override, use_custom: e.target.checked })}
            />
            Использовать индивидуальную роль/легенду только для этой группы
          </label>
        </div>

        {override.use_custom && (
          <div className="space-y-3 pt-3 border-t border-slate-800">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Имя / Ник в этом чате</label>
              <input
                type="text"
                value={override.display_name || ''}
                onChange={(e) => setOverride({ ...override, display_name: e.target.value })}
                className="w-full sm:w-72 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Легенда и характер</label>
                <textarea
                  rows={3}
                  value={override.backstory || ''}
                  onChange={(e) => setOverride({ ...override, backstory: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Стиль речи и манера письма</label>
                <textarea
                  rows={3}
                  value={override.communication_style || ''}
                  onChange={(e) => setOverride({ ...override, communication_style: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-slate-400 mb-1">Табу (запреты)</label>
                <textarea
                  rows={2}
                  value={override.taboos || ''}
                  onChange={(e) => setOverride({ ...override, taboos: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">Примеры фраз (по одной на строку)</label>
                <textarea
                  rows={2}
                  value={examplesText}
                  onChange={(e) => setExamplesText(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Секция 2: Реактивность, Debounce и Человечность */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-5">
        <div>
          <h3 className="text-base font-semibold text-white">2. Частота ответов, Debounce и Эмодзи-реакции</h3>
          <p className="text-xs text-slate-400">
            Настройка того, когда бот вступает в чужие обсуждения и сколько ждёт конца мысли собеседника
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800">
            <div className="flex justify-between text-xs mb-1.5">
              <span className="text-slate-400">Общительность (Chattiness)</span>
              <span className="text-indigo-300 font-semibold">{Math.round(settings.chattiness_level * 100)}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={settings.chattiness_level}
              onChange={(e) => setSettings({ ...settings, chattiness_level: Number(e.target.value) })}
              className="w-full accent-indigo-500"
            />
            <span className="text-[11px] text-slate-500">Желание встревать без прямого тега</span>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Ожидание конца мысли (Debounce, сек)</label>
            <input
              type="number"
              step="0.5"
              min="1"
              max="30"
              value={settings.debounce_seconds}
              onChange={(e) => setSettings({ ...settings, debounce_seconds: Number(e.target.value) })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
            <span className="text-[11px] text-slate-500">Ждёт серию коротких сообщений подряд</span>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Кулдаун между репликами (сек)</label>
            <input
              type="number"
              min="0"
              max="86400"
              value={settings.cooldown_seconds}
              onChange={(e) => setSettings({ ...settings, cooldown_seconds: Number(e.target.value) })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
            <span className="text-[11px] text-slate-500">Если бота не тегнули напрямую</span>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Лимит сообщений бота в час</label>
            <input
              type="number"
              min="1"
              max="300"
              value={settings.max_msgs_per_hour}
              onChange={(e) => setSettings({ ...settings, max_msgs_per_hour: Number(e.target.value) })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
            <span className="text-[11px] text-slate-500">Защита от перерасхода токенов</span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <label className="flex items-center gap-2.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.reply_on_mention}
              onChange={(e) => setSettings({ ...settings, reply_on_mention: e.target.checked })}
            />
            Всегда отвечать при прямом реплае / @упоминании
          </label>

          <label className="flex items-center gap-2.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.simulate_typing}
              onChange={(e) => setSettings({ ...settings, simulate_typing: e.target.checked })}
            />
            Имитировать паузу и набор текста («печатает...»)
          </label>

          <label className="flex items-center gap-2.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.reactions_enabled}
              onChange={(e) => setSettings({ ...settings, reactions_enabled: e.target.checked })}
            />
            Ставить эмодзи-реакции на сообщения
          </label>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="sm:col-span-2">
            <label className="block text-xs text-slate-400 mb-1">Разрешённые эмодзи для реакций (через пробел)</label>
            <input
              type="text"
              value={emojisText}
              onChange={(e) => setEmojisText(e.target.value)}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Размер батча анализа памяти (сообщ.)</label>
            <input
              type="number"
              min="5"
              max="200"
              value={settings.memory_batch_size}
              onChange={(e) => setSettings({ ...settings, memory_batch_size: Number(e.target.value) })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
          </div>
        </div>
      </div>

      {/* Секция 3: Спонтанная инициатива и Тихие часы */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-white">3. Собственная инициатива (сам начинает диалог)</h3>
            <p className="text-xs text-slate-400">
              Бот может сам подбросить тему в тишине, учитывая часовой пояс и не спамя, если ему не ответили
            </p>
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-emerald-300 font-medium cursor-pointer">
            <input
              type="checkbox"
              checked={settings.proactive_enabled}
              onChange={(e) => setSettings({ ...settings, proactive_enabled: e.target.checked })}
            />
            Инициатива включена
          </label>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800">
            <div className="flex justify-between text-xs mb-1.5">
              <span className="text-slate-400">Вероятность начала диалога в час</span>
              <span className="text-emerald-300 font-semibold">
                {Math.round(settings.proactive_chance_per_hour * 100)}%
              </span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={settings.proactive_chance_per_hour}
              onChange={(e) =>
                setSettings({ ...settings, proactive_chance_per_hour: Number(e.target.value) })
              }
              className="w-full accent-emerald-500"
            />
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Мин. тишина в чате до вброса (минут)</label>
            <input
              type="number"
              min="5"
              max="10080"
              value={settings.min_silence_minutes}
              onChange={(e) => setSettings({ ...settings, min_silence_minutes: Number(e.target.value) })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Мин. интервал между инициативами (часов)</label>
            <input
              type="number"
              step="0.5"
              min="0.5"
              max="168"
              value={settings.proactive_cooldown_hours}
              onChange={(e) => setSettings({ ...settings, proactive_cooldown_hours: Number(e.target.value) })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Новая тема, если тишина дольше (часов)</label>
            <input
              type="number"
              step="0.5"
              min="0"
              max="720"
              value={settings.new_topic_after_hours ?? 4}
              onChange={(e) => setSettings({ ...settings, new_topic_after_hours: Number(e.target.value) })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
            <span className="text-[11px] text-slate-500">Не продолжает старый разговор, а меняет тему</span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 pt-2 items-end">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Часовой пояс группы</label>
            <select
              value={settings.timezone}
              onChange={(e) => setSettings({ ...settings, timezone: e.target.value })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            >
              {COMMON_TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 p-2.5 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.quiet_hours_enabled}
              onChange={(e) => setSettings({ ...settings, quiet_hours_enabled: e.target.checked })}
            />
            Тихие часы (ночной режим)
          </label>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Начало тихих часов</label>
            <input
              type="time"
              value={settings.quiet_hours_start}
              onChange={(e) => setSettings({ ...settings, quiet_hours_start: e.target.value })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Конец тихих часов</label>
            <input
              type="time"
              value={settings.quiet_hours_end}
              onChange={(e) => setSettings({ ...settings, quiet_hours_end: e.target.value })}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
            />
          </div>
        </div>
      </div>

      {notice && (
        <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-300">
          {notice}
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={saving}
          className="px-6 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium flex items-center gap-2 cursor-pointer"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Сохранение...' : 'Сохранить настройки группы'}
        </button>
      </div>
    </form>
  );
}


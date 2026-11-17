import React, { useEffect, useState } from 'react';
import { GitMerge, Lock, Pin, Plus, Save, Search, Trash2, Unlock } from 'lucide-react';
import { api } from '../../api';
import type { GroupMember, GroupMemberDetail, MemberFact } from '../../types';

const FACT_CATEGORIES: { value: string; label: string }[] = [
  { value: 'work', label: 'Работа и навыки' },
  { value: 'hobby', label: 'Хобби и увлечения' },
  { value: 'bio', label: 'Биография / быт' },
  { value: 'opinion', label: 'Взгляды и триггеры' },
  { value: 'style', label: 'Манера речи' },
  { value: 'relationship', label: 'Связи в чате' },
  { value: 'other', label: 'Другое' },
];

export function MembersDossierTab({ groupId }: { groupId: number }) {
  const [members, setMembers] = useState<GroupMember[]>([]);
  const [filter, setFilter] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<GroupMemberDetail | null>(null);

  const [displayName, setDisplayName] = useState('');
  const [nickname, setNickname] = useState('');
  const [nicknameLocked, setNicknameLocked] = useState(false);
  const [character, setCharacter] = useState('');
  const [characterLocked, setCharacterLocked] = useState(false);
  const [attitude, setAttitude] = useState('');
  const [attitudeLocked, setAttitudeLocked] = useState(false);
  const [adminNotes, setAdminNotes] = useState('');

  const [showMergeBox, setShowMergeBox] = useState(false);
  const [mergeSourceId, setMergeSourceId] = useState('');
  const [merging, setMerging] = useState(false);

  const [newFactCat, setNewFactCat] = useState('bio');
  const [newFactText, setNewFactText] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const loadList = async () => {
    const list = await api.listMembers(groupId);
    setMembers(list);
    if (!selectedId && list.length > 0) {
      loadMemberDetail(list[0].id);
    }
  };

  const loadMemberDetail = async (memberId: string) => {
    setSelectedId(memberId);
    setNotice('');
    setShowMergeBox(false);
    setMergeSourceId('');
    const d = await api.getMember(groupId, memberId);
    setDetail(d);
    setDisplayName(d.display_name);
    setNickname(d.chat_nickname);
    setNicknameLocked(d.nickname_locked);
    setCharacter(d.character_summary);
    setCharacterLocked(d.character_locked);
    setAttitude(d.bot_attitude);
    setAttitudeLocked(d.attitude_locked);
    setAdminNotes(d.admin_notes);
  };

  useEffect(() => {
    setSelectedId(null);
    setDetail(null);
    loadList();
  }, [groupId]);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!detail) return;
    setSaving(true);
    setNotice('');
    try {
      const updated = await api.updateMember(groupId, detail.id, {
        display_name: displayName,
        chat_nickname: nickname,
        nickname_locked: nicknameLocked,
        character_summary: character,
        character_locked: characterLocked,
        bot_attitude: attitude,
        attitude_locked: attitudeLocked,
        admin_notes: adminNotes,
      });
      setDetail(updated);
      await loadList();
      setNotice('Карточка участника сохранена!');
    } catch (err: any) {
      setNotice(`Ошибка: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleMergeDuplicate = async () => {
    if (!detail || !mergeSourceId) return;
    const sourceMember = members.find((m) => m.id === mergeSourceId);
    if (!sourceMember) return;
    if (
      !confirm(
        `Объединить карточку «${sourceMember.display_name}» (${sourceMember.message_count} сообщ., ${sourceMember.facts_count} факт.) в текущий профиль «${detail.display_name}»?\n\nВсе сообщения, факты и реальный Telegram ID будут перенесены сюда, а дубль удалён.`
      )
    ) {
      return;
    }
    setMerging(true);
    setNotice('');
    try {
      const merged = await api.mergeMember(groupId, detail.id, mergeSourceId);
      setDetail(merged);
      setDisplayName(merged.display_name);
      setNickname(merged.chat_nickname);
      setNicknameLocked(merged.nickname_locked);
      setCharacter(merged.character_summary);
      setCharacterLocked(merged.character_locked);
      setAttitude(merged.bot_attitude);
      setAttitudeLocked(merged.attitude_locked);
      setAdminNotes(merged.admin_notes);
      setShowMergeBox(false);
      setMergeSourceId('');
      await loadList();
      setNotice(`Профиль «${sourceMember.display_name}» успешно объединён с этой карточкой!`);
    } catch (err: any) {
      setNotice(`Ошибка объединения: ${err.message}`);
    } finally {
      setMerging(false);
    }
  };

  const handleDeleteMember = async () => {
    if (!detail) return;
    if (!confirm(`Удалить карточку участника «${detail.display_name}» и все его факты?`)) return;
    await api.deleteMember(groupId, detail.id);
    setSelectedId(null);
    setDetail(null);
    await loadList();
  };

  const handleAddFact = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!detail || !newFactText.trim()) return;
    await api.createFact(groupId, detail.id, {
      category: newFactCat,
      fact_text: newFactText.trim(),
      is_pinned: true,
    });
    setNewFactText('');
    await loadMemberDetail(detail.id);
    await loadList();
  };

  const handleToggleFactPin = async (fact: MemberFact) => {
    if (!detail) return;
    await api.updateFact(groupId, detail.id, fact.id, { is_pinned: !fact.is_pinned });
    await loadMemberDetail(detail.id);
  };

  const handleEditFactBlur = async (fact: MemberFact, nextText: string) => {
    if (!detail || nextText.trim() === fact.fact_text || !nextText.trim()) return;
    await api.updateFact(groupId, detail.id, fact.id, { fact_text: nextText.trim(), is_pinned: true });
    await loadMemberDetail(detail.id);
  };

  const handleDeleteFact = async (factId: string) => {
    if (!detail) return;
    await api.deleteFact(groupId, detail.id, factId);
    await loadMemberDetail(detail.id);
    await loadList();
  };

  const filteredMembers = members.filter((m) => {
    const q = filter.toLowerCase().trim();
    if (!q) return true;
    return (
      m.display_name.toLowerCase().includes(q) ||
      (m.username && m.username.toLowerCase().includes(q)) ||
      m.chat_nickname.toLowerCase().includes(q)
    );
  });

  const otherMembers = members.filter((m) => m.id !== detail?.id);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
      {/* Список участников чата */}
      <div className="lg:col-span-4 bg-slate-900 border border-slate-800 rounded-2xl p-4 flex flex-col gap-3 max-h-[760px]">
        <div className="relative">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Поиск участника группы..."
            className="w-full pl-9 pr-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
          />
        </div>

        <div className="overflow-y-auto space-y-2 pr-1 flex-1">
          {filteredMembers.length === 0 ? (
            <div className="text-xs text-slate-500 text-center py-8">
              Участники появятся, как только напишут в группу или после импорта истории чата.
            </div>
          ) : (
            filteredMembers.map((m) => {
              const isSynthetic = m.telegram_user_id >= 9000000000000;
              return (
                <div
                  key={m.id}
                  onClick={() => loadMemberDetail(m.id)}
                  className={`p-3 rounded-xl border transition cursor-pointer ${
                    selectedId === m.id
                      ? 'bg-indigo-600/15 border-indigo-500/50'
                      : 'bg-slate-950 border-slate-800 hover:border-slate-700'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-medium text-xs text-white truncate">
                      {m.display_name}
                      {m.chat_nickname && <span className="text-indigo-300 ml-1">({m.chat_nickname})</span>}
                    </div>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 shrink-0">
                      {m.facts_count} факт.
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-500 mt-1">
                    <span>
                      {m.username ? `@${m.username}` : isSynthetic ? 'импорт HTML' : `tg:${m.telegram_user_id}`}
                    </span>
                    <span>{m.message_count} сообщ.</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Детальное досье участника */}
      <div className="lg:col-span-8 space-y-6">
        {detail ? (
          <>
            <form onSubmit={handleSaveProfile} className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800">
                <div>
                  <h3 className="text-base font-semibold text-white">{detail.display_name}</h3>
                  <div className="text-xs text-slate-400">
                    {detail.username ? `@${detail.username} • ` : ''}
                    {detail.telegram_user_id >= 9000000000000
                      ? 'Импортированный профиль (HTML)'
                      : `Telegram ID: ${detail.telegram_user_id}`}{' '}
                    • Сообщений в этой группе: {detail.message_count}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {otherMembers.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setShowMergeBox(!showMergeBox)}
                      className="px-3 py-1.5 rounded-xl bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-500/30 text-indigo-300 text-xs font-medium flex items-center gap-1.5 cursor-pointer"
                    >
                      <GitMerge className="w-3.5 h-3.5" />
                      Объединить с дублем
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={handleDeleteMember}
                    className="p-1.5 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/20 text-rose-400 cursor-pointer"
                    title="Удалить эту карточку участника"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* Панель слияния дубликатов */}
              {showMergeBox && (
                <div className="p-4 rounded-xl bg-slate-950 border border-indigo-500/40 space-y-3">
                  <div className="text-xs text-slate-300">
                    Выбери карточку-дубль, которую нужно **влить в текущий профиль «{detail.display_name}»** (все её
                    сообщения, факты и реальный Telegram ID перенесутся сюда, а дубль удалится):
                  </div>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <select
                      value={mergeSourceId}
                      onChange={(e) => setMergeSourceId(e.target.value)}
                      className="flex-1 px-3 py-2 bg-slate-900 border border-slate-800 rounded-xl text-xs text-white"
                    >
                      <option value="">-- Выберите дубль для слияния --</option>
                      {otherMembers.map((om) => (
                        <option key={om.id} value={om.id}>
                          {om.display_name} {om.username ? `(@${om.username})` : ''} — {om.message_count} сообщ.,{' '}
                          {om.facts_count} факт.
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      disabled={!mergeSourceId || merging}
                      onClick={handleMergeDuplicate}
                      className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-xs font-medium text-white flex items-center justify-center gap-1.5 cursor-pointer"
                    >
                      <GitMerge className="w-3.5 h-3.5" />
                      {merging ? 'Объединяем...' : 'Влить в эту карточку'}
                    </button>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Отображаемое имя</label>
                  <input
                    type="text"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                  />
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs text-slate-400">Как зовут в чате</label>
                    <button
                      type="button"
                      onClick={() => setNicknameLocked(!nicknameLocked)}
                      className={`text-[11px] flex items-center gap-1 cursor-pointer ${
                        nicknameLocked ? 'text-amber-300' : 'text-slate-500'
                      }`}
                    >
                      {nicknameLocked ? <Lock className="w-3 h-3" /> : <Unlock className="w-3 h-3" />}
                      {nicknameLocked ? 'Закреплено' : 'Авто'}
                    </button>
                  </div>
                  <input
                    type="text"
                    value={nickname}
                    onChange={(e) => {
                      setNickname(e.target.value);
                      setNicknameLocked(true);
                    }}
                    placeholder="Серёга, Шеф..."
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                  />
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs text-slate-400">Отношение бота</label>
                    <button
                      type="button"
                      onClick={() => setAttitudeLocked(!attitudeLocked)}
                      className={`text-[11px] flex items-center gap-1 cursor-pointer ${
                        attitudeLocked ? 'text-amber-300' : 'text-slate-500'
                      }`}
                    >
                      {attitudeLocked ? <Lock className="w-3 h-3" /> : <Unlock className="w-3 h-3" />}
                      {attitudeLocked ? 'Закреплено' : 'Авто'}
                    </button>
                  </div>
                  <input
                    type="text"
                    value={attitude}
                    onChange={(e) => {
                      setAttitude(e.target.value);
                      setAttitudeLocked(true);
                    }}
                    placeholder="свой парень, подкалываем..."
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                  />
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-xs text-slate-400">Характер, темперамент и манера общения</label>
                  <button
                    type="button"
                    onClick={() => setCharacterLocked(!characterLocked)}
                    className={`text-[11px] flex items-center gap-1 cursor-pointer ${
                      characterLocked ? 'text-amber-300' : 'text-slate-500'
                    }`}
                  >
                    {characterLocked ? <Lock className="w-3 h-3" /> : <Unlock className="w-3 h-3" />}
                    {characterLocked ? 'Закреплено от перезаписи ИИ' : 'Обновляется ИИ автоматически'}
                  </button>
                </div>
                <textarea
                  rows={3}
                  value={character}
                  onChange={(e) => {
                    setCharacter(e.target.value);
                    setCharacterLocked(true);
                  }}
                  placeholder="ИИ сам сформирует психологический портрет, либо опишите его вручную..."
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                />
              </div>

              <div>
                <label className="block text-xs text-amber-300/90 mb-1">
                  Скрытая заметка администратора (высший приоритет для бота, никогда не меняется ИИ)
                </label>
                <textarea
                  rows={2}
                  value={adminNotes}
                  onChange={(e) => setAdminNotes(e.target.value)}
                  placeholder="Например: это владелец компании, с ним не шутить про увольнения..."
                  className="w-full px-3 py-2 bg-slate-950 border border-amber-500/20 rounded-xl text-xs text-white"
                />
              </div>

              {notice && <div className="text-xs text-emerald-400">{notice}</div>}

              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={saving}
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white flex items-center gap-1.5 cursor-pointer"
                >
                  <Save className="w-3.5 h-3.5" />
                  {saving ? 'Сохранение...' : 'Сохранить портрет'}
                </button>
              </div>
            </form>

            {/* Атомарные факты об участнике */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
              <div>
                <h4 className="text-sm font-semibold text-white">
                  Атомарные факты о человеке ({detail.facts.length})
                </h4>
                <p className="text-xs text-slate-400">
                  Отредактируй любой факт или нажми булавку — закреплённые факты защищены от перезаписи фоновым ИИ
                </p>
              </div>

              <form onSubmit={handleAddFact} className="flex flex-col sm:flex-row gap-2">
                <select
                  value={newFactCat}
                  onChange={(e) => setNewFactCat(e.target.value)}
                  className="px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-slate-200"
                >
                  {FACT_CATEGORIES.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
                <input
                  type="text"
                  value={newFactText}
                  onChange={(e) => setNewFactText(e.target.value)}
                  placeholder="Добавить новый факт об этом участнике..."
                  className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs text-white"
                />
                <button
                  type="submit"
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-medium text-white flex items-center justify-center gap-1 cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" /> Добавить
                </button>
              </form>

              <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                {detail.facts.length === 0 ? (
                  <div className="text-xs text-slate-500 py-6 text-center">
                    Пока нет сохранённых фактов. Нажми «Обновить память сейчас» во вкладке Живого чата или добавь факт
                    вручную.
                  </div>
                ) : (
                  detail.facts.map((f) => (
                    <div
                      key={f.id}
                      className={`flex items-center gap-2.5 p-2.5 rounded-xl border ${
                        f.is_pinned
                          ? 'bg-amber-500/5 border-amber-500/30'
                          : 'bg-slate-950 border-slate-800'
                      }`}
                    >
                      <span className="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300 shrink-0">
                        {FACT_CATEGORIES.find((c) => c.value === f.category)?.label || f.category}
                      </span>
                      <input
                        type="text"
                        defaultValue={f.fact_text}
                        onBlur={(e) => handleEditFactBlur(f, e.target.value)}
                        className="flex-1 bg-transparent text-xs text-slate-100 focus:outline-none focus:bg-slate-900 px-1.5 py-0.5 rounded"
                      />
                      <button
                        type="button"
                        onClick={() => handleToggleFactPin(f)}
                        className={`p-1 rounded cursor-pointer ${
                          f.is_pinned ? 'text-amber-400' : 'text-slate-600 hover:text-slate-300'
                        }`}
                        title={f.is_pinned ? 'Закреплено админом (защищено от ИИ)' : 'Закрепить факт'}
                      >
                        <Pin className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteFact(f.id)}
                        className="p-1 text-slate-600 hover:text-rose-400 cursor-pointer"
                        title="Удалить факт"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-10 text-center text-sm text-slate-400">
            Выберите участника группы слева, чтобы посмотреть и отредактировать его досье.
          </div>
        )}
      </div>
    </div>
  );
}

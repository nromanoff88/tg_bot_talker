import React, { useEffect, useState } from 'react';
import {
  ArrowLeft,
  Brain,
  FileUp,
  FlaskConical,
  MessageSquareText,
  Sliders,
  Trash2,
  Users,
} from 'lucide-react';
import { api } from '../../api';
import type { GroupDetail, PersonaTemplate } from '../../types';
import { BehaviorPersonaTab } from './BehaviorPersonaTab';
import { ContextEpisodesTab } from './ContextEpisodesTab';
import { MembersDossierTab } from './MembersDossierTab';
import { LiveChatThoughtsTab } from './LiveChatThoughtsTab';
import { SandboxChatTab } from './SandboxChatTab';
import { ImportHistoryTab } from './ImportHistoryTab';

type TabKey = 'live' | 'sandbox' | 'members' | 'context' | 'behavior' | 'import';

interface Props {
  groupId: number;
  onBack: () => void;
}

export function GroupDetailPage({ groupId, onBack }: Props) {
  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [personas, setPersonas] = useState<PersonaTemplate[]>([]);
  const [activeTab, setActiveTab] = useState<TabKey>('live');
  const [loading, setLoading] = useState(true);

  const reloadGroup = async () => {
    const [g, p] = await Promise.all([api.getGroup(groupId), api.listPersonas()]);
    setGroup(g);
    setPersonas(p);
    setLoading(false);
  };

  useEffect(() => {
    setLoading(true);
    reloadGroup();
  }, [groupId]);

  const handleToggleActive = async () => {
    if (!group) return;
    const updated = await api.updateGroupPersona(group.id, {
      is_active: !group.is_active,
      persona_template_id: group.persona_template_id,
      persona_override: group.persona_override,
    });
    setGroup(updated);
  };

  const handleDeleteGroup = async () => {
    if (!group) return;
    if (!confirm(`Удалить группу «${group.title}» и всю её память из базы данных?`)) return;
    await api.deleteGroup(group.id);
    onBack();
  };

  if (loading || !group) {
    return <div className="text-sm text-slate-400 p-8">Загрузка данных группы...</div>;
  }

  const tabs: { key: TabKey; label: string; icon: React.ReactNode }[] = [
    { key: 'live', label: 'Живой чат и Мысли бота', icon: <MessageSquareText className="w-4 h-4" /> },
    { key: 'sandbox', label: 'Песочница (Тест-чат)', icon: <FlaskConical className="w-4 h-4" /> },
    { key: 'members', label: 'Участники (Досье)', icon: <Users className="w-4 h-4" /> },
    { key: 'context', label: 'Контекст и Векторная память', icon: <Brain className="w-4 h-4" /> },
    { key: 'behavior', label: 'Персона и Поведение', icon: <Sliders className="w-4 h-4" /> },
    { key: 'import', label: 'Импорт истории (HTML / JSON)', icon: <FileUp className="w-4 h-4" /> },
  ];

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-slate-900 border border-slate-800 rounded-2xl p-5">
        <div className="flex items-start gap-3">
          <button
            onClick={onBack}
            className="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 transition cursor-pointer mt-0.5"
            title="К списку групп"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="text-xl font-semibold text-white">{group.title}</h1>
              <span className="text-xs px-2 py-0.5 rounded-lg bg-slate-800 text-slate-400 font-mono">
                {group.id}
              </span>
            </div>
            <div className="text-xs text-slate-400 mt-1 flex flex-wrap gap-x-4 gap-y-1">
              <span>Непроанализировано в буфере: {group.unanalyzed_msg_count} сообщ.</span>
              <span>Безответных инициатив подряд: {group.unanswered_initiatives}</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={handleToggleActive}
            className={`px-3.5 py-2 rounded-xl text-xs font-medium transition cursor-pointer ${
              group.is_active
                ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25'
                : 'bg-slate-800 text-slate-400 border border-slate-700 hover:bg-slate-700'
            }`}
          >
            {group.is_active ? '● Бот активен в чате' : '○ Бот на паузе'}
          </button>

          <button
            onClick={handleDeleteGroup}
            className="p-2 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 transition cursor-pointer"
            title="Удалить группу и её память"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs font-medium whitespace-nowrap transition cursor-pointer ${
              activeTab === t.key
                ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-950/50'
                : 'bg-slate-900 text-slate-400 hover:text-slate-200 border border-slate-800'
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'live' && <LiveChatThoughtsTab group={group} onRefreshGroup={reloadGroup} />}
      {activeTab === 'sandbox' && <SandboxChatTab group={group} />}
      {activeTab === 'members' && <MembersDossierTab groupId={group.id} />}
      {activeTab === 'context' && (
        <ContextEpisodesTab
          groupId={group.id}
          initialContext={group.context}
          onSaved={reloadGroup}
        />
      )}
      {activeTab === 'behavior' && (
        <BehaviorPersonaTab
          group={group}
          personas={personas}
          onSaved={reloadGroup}
        />
      )}
      {activeTab === 'import' && (
        <ImportHistoryTab
          groupId={group.id}
          onImported={reloadGroup}
        />
      )}
    </div>
  );
}


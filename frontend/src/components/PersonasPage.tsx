import React, { useEffect, useState } from 'react';
import { Plus, Save, Star, Trash2 } from 'lucide-react';
import { api } from '../api';
import type { PersonaTemplate } from '../types';

const emptyDraft = {
  name: '',
  description: '',
  display_name: 'Макс',
  backstory: '',
  communication_style: '',
  taboos: '',
  example_phrases: [] as string[],
  is_default: false,
};

export function PersonasPage() {
  const [personas, setPersonas] = useState<PersonaTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<string | 'new' | null>(null);
  const [form, setForm] = useState(emptyDraft);
  const [examplesText, setExamplesText] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const load = async () => {
    const list = await api.listPersonas();
    setPersonas(list);
    if (!selectedId && list.length > 0) {
      selectPersona(list[0]);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const selectPersona = (p: PersonaTemplate) => {
    setSelectedId(p.id);
    setForm({
      name: p.name,
      description: p.description,
      display_name: p.display_name,
      backstory: p.backstory,
      communication_style: p.communication_style,
      taboos: p.taboos,
      example_phrases: p.example_phrases || [],
      is_default: p.is_default,
    });
    setExamplesText((p.example_phrases || []).join('\n'));
    setNotice('');
  };

  const handleStartNew = () => {
    setSelectedId('new');
    setForm(emptyDraft);
    setExamplesText('');
    setNotice('');
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setNotice('');
    try {
      const payload = {
        ...form,
        example_phrases: examplesText
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean),
      };
      if (selectedId === 'new') {
        const created = await api.createPersona(payload);
        await load();
        selectPersona(created);
        setNotice('Шаблон персоны создан!');
      } else if (selectedId) {
        const updated = await api.updatePersona(selectedId, payload);
        await load();
        selectPersona(updated);
        setNotice('Изменения сохранены!');
      }
    } catch (err: any) {
      setNotice(`Ошибка: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedId || selectedId === 'new') return;
    if (!confirm('Удалить этот шаблон персоны?')) return;
    await api.deletePersona(selectedId);
    setSelectedId(null);
    await load();
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">Библиотека шаблонов Персон</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Базовые характеры и легенды, которые можно назначать группам или переопределять индивидуально под чат
          </p>
        </div>
        <button
          onClick={handleStartNew}
          className="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white flex items-center gap-1.5 cursor-pointer"
        >
          <Plus className="w-4 h-4" />
          Новый шаблон
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="space-y-2.5">
          {personas.map((p) => (
            <div
              key={p.id}
              onClick={() => selectPersona(p)}
              className={`p-4 rounded-2xl border transition cursor-pointer ${
                selectedId === p.id
                  ? 'bg-indigo-600/15 border-indigo-500/50'
                  : 'bg-slate-900 border-slate-800 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-sm text-white">{p.name}</span>
                {p.is_default && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-amber-500/15 text-amber-300 border border-amber-500/30">
                    <Star className="w-3 h-3" /> По умолчанию
                  </span>
                )}
              </div>
              <div className="text-xs text-slate-400 mt-1 line-clamp-2">{p.description}</div>
              <div className="text-[11px] text-indigo-300 mt-2">Имя в чате: {p.display_name}</div>
            </div>
          ))}
        </div>

        <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-6">
          {selectedId ? (
            <form onSubmit={handleSave} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Название шаблона</label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Имя бота в общении</label>
                  <input
                    type="text"
                    value={form.display_name}
                    onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                    required
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1">Краткое описание (для админки)</label>
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1">Легенда, бэкграунд и интересы</label>
                <textarea
                  rows={3}
                  value={form.backstory}
                  onChange={(e) => setForm({ ...form, backstory: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1">Манера общения и стиль письма</label>
                <textarea
                  rows={3}
                  value={form.communication_style}
                  onChange={(e) => setForm({ ...form, communication_style: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1">Строгие табу (чего не говорить и не делать)</label>
                <textarea
                  rows={2}
                  value={form.taboos}
                  onChange={(e) => setForm({ ...form, taboos: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1">
                  Примеры живых фраз (по одной реплике на строку)
                </label>
                <textarea
                  rows={4}
                  value={examplesText}
                  onChange={(e) => setExamplesText(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white"
                />
              </div>

              <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                  className="rounded border-slate-700 bg-slate-950"
                />
                Назначать этот шаблон по умолчанию всем новым группам
              </label>

              {notice && <div className="text-xs text-emerald-400">{notice}</div>}

              <div className="flex items-center justify-between pt-3 border-t border-slate-800">
                {selectedId !== 'new' ? (
                  <button
                    type="button"
                    onClick={handleDelete}
                    className="px-3 py-2 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 text-xs flex items-center gap-1.5 cursor-pointer"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    Удалить
                  </button>
                ) : (
                  <div />
                )}

                <button
                  type="submit"
                  disabled={saving}
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium flex items-center gap-1.5 cursor-pointer"
                >
                  <Save className="w-3.5 h-3.5" />
                  {saving ? 'Сохранение...' : 'Сохранить шаблон'}
                </button>
              </div>
            </form>
          ) : (
            <div className="text-sm text-slate-400">Выберите шаблон слева или создайте новый.</div>
          )}
        </div>
      </div>
    </div>
  );
}


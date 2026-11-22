import React, { useEffect, useRef, useState } from 'react';
import { Code2, Eye, Plus, RotateCcw, Save, Sparkles, Star, Trash2 } from 'lucide-react';
import { api } from '../api';
import type { GroupSummary, PersonaTemplate, PromptTemplateSpec } from '../types';

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
  const [mode, setMode] = useState<'personas' | 'prompts'>('personas');

  // Редактор карточек Персон
  const [personas, setPersonas] = useState<PersonaTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<string | 'new' | null>(null);
  const [form, setForm] = useState(emptyDraft);
  const [examplesText, setExamplesText] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  // Редактор общих Jinja2-шаблонов
  const [specs, setSpecs] = useState<PromptTemplateSpec[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>('persona_system_prompt');
  const [promptDraft, setPromptDraft] = useState('');
  const [promptSaving, setPromptSaving] = useState(false);
  const [promptNotice, setPromptNotice] = useState('');
  const [promptError, setPromptError] = useState('');
  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [previewGroupId, setPreviewGroupId] = useState<string>('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewResult, setPreviewResult] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const loadPersonas = async () => {
    const list = await api.listPersonas();
    setPersonas(list);
    if (!selectedId && list.length > 0) {
      selectPersona(list[0]);
    }
  };

  const loadPromptsAndGroups = async () => {
    const [promptList, groupList] = await Promise.all([api.getPromptTemplates(), api.listGroups()]);
    setSpecs(promptList);
    setGroups(groupList);
    if (!previewGroupId && groupList.length > 0) {
      setPreviewGroupId(String(groupList[0].id));
    }
    const target = promptList.find((s) => s.key === selectedKey) || promptList[0];
    if (target) {
      setSelectedKey(target.key);
      setPromptDraft(target.template);
    }
  };

  useEffect(() => {
    loadPersonas();
    loadPromptsAndGroups();
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

  const handleSavePersona = async (e: React.FormEvent) => {
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
        await loadPersonas();
        selectPersona(created);
        setNotice('Шаблон персоны создан!');
      } else if (selectedId) {
        const updated = await api.updatePersona(selectedId, payload);
        await loadPersonas();
        selectPersona(updated);
        setNotice('Изменения сохранены!');
      }
    } catch (err: any) {
      setNotice(`Ошибка: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDeletePersona = async () => {
    if (!selectedId || selectedId === 'new') return;
    if (!confirm('Удалить этот шаблон персоны?')) return;
    await api.deletePersona(selectedId);
    setSelectedId(null);
    await loadPersonas();
  };

  const selectedSpec = specs.find((s) => s.key === selectedKey) || null;

  const handleSelectPrompt = (spec: PromptTemplateSpec) => {
    setSelectedKey(spec.key);
    setPromptDraft(spec.template);
    setPromptNotice('');
    setPromptError('');
    setPreviewResult('');
  };

  const insertVariableAtCursor = (varName: string) => {
    const snippet = `{{ ${varName} }}`;
    const el = textareaRef.current;
    if (!el) {
      setPromptDraft((prev) => prev + snippet);
      return;
    }
    const start = el.selectionStart ?? promptDraft.length;
    const end = el.selectionEnd ?? promptDraft.length;
    const next = promptDraft.slice(0, start) + snippet + promptDraft.slice(end);
    setPromptDraft(next);
    requestAnimationFrame(() => {
      el.focus();
      const pos = start + snippet.length;
      el.setSelectionRange(pos, pos);
    });
  };

  const handleSavePrompt = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSpec) return;
    setPromptSaving(true);
    setPromptNotice('');
    setPromptError('');
    try {
      const updatedList = await api.updatePromptTemplates({ [selectedSpec.key]: promptDraft });
      setSpecs(updatedList);
      const refreshed = updatedList.find((s) => s.key === selectedSpec.key);
      if (refreshed) {
        setPromptDraft(refreshed.template);
      }
      setPromptNotice('Jinja2-шаблон сохранён и сразу применяется ко всем группам и персонам.');
    } catch (err: any) {
      setPromptError(err.message);
    } finally {
      setPromptSaving(false);
    }
  };

  const handleResetPrompt = async () => {
    if (!selectedSpec) return;
    if (!confirm(`Сбросить «${selectedSpec.title}» к стандартному шаблону?`)) return;
    setPromptSaving(true);
    setPromptNotice('');
    setPromptError('');
    try {
      const updatedList = await api.updatePromptTemplates({
        [selectedSpec.key]: selectedSpec.default_template,
      });
      setSpecs(updatedList);
      setPromptDraft(selectedSpec.default_template);
      setPreviewResult('');
      setPromptNotice('Шаблон сброшен к стандартному.');
    } catch (err: any) {
      setPromptError(err.message);
    } finally {
      setPromptSaving(false);
    }
  };

  const handlePreviewPrompt = async () => {
    if (!selectedSpec) return;
    setPreviewLoading(true);
    setPromptError('');
    try {
      const gid = previewGroupId ? Number(previewGroupId) : null;
      const res = await api.previewPromptTemplate(selectedSpec.key, promptDraft, gid);
      setPreviewResult(res.rendered);
    } catch (err: any) {
      setPromptError(err.message);
    } finally {
      setPreviewLoading(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-white">Персоны и общие промпты</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Настройка характеров Персон и общих системных Jinja2-шаблонов без правок кода
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex p-1 rounded-xl bg-slate-900 border border-slate-800">
            <button
              type="button"
              onClick={() => setMode('personas')}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 cursor-pointer transition ${
                mode === 'personas'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              Роли и характеры Персон
            </button>
            <button
              type="button"
              onClick={() => setMode('prompts')}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 cursor-pointer transition ${
                mode === 'prompts'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Code2 className="w-3.5 h-3.5" />
              Общие промты (Jinja2)
            </button>
          </div>

          {mode === 'personas' && (
            <button
              onClick={handleStartNew}
              className="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-medium text-white flex items-center gap-1.5 cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              Новый шаблон
            </button>
          )}
        </div>
      </div>

      {mode === 'personas' ? (
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
              <form onSubmit={handleSavePersona} className="space-y-4">
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
                      onClick={handleDeletePersona}
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
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Список шаблонов */}
          <div className="lg:col-span-4 space-y-2.5">
            {specs.map((spec) => (
              <div
                key={spec.key}
                onClick={() => handleSelectPrompt(spec)}
                className={`p-4 rounded-2xl border transition cursor-pointer ${
                  selectedKey === spec.key
                    ? 'bg-indigo-600/15 border-indigo-500/50'
                    : 'bg-slate-900 border-slate-800 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-xs sm:text-sm text-white">{spec.title}</span>
                  <span
                    className={`px-2 py-0.5 rounded-full text-[10px] border shrink-0 ${
                      spec.is_custom
                        ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
                        : 'bg-slate-800 text-slate-400 border-slate-700'
                    }`}
                  >
                    {spec.is_custom ? 'Изменён' : 'По умолчанию'}
                  </span>
                </div>
                <div className="text-[11px] font-mono text-indigo-300/80 mt-1">{spec.key}</div>
                <p className="text-xs text-slate-400 mt-1 line-clamp-2">{spec.description}</p>
              </div>
            ))}
          </div>

          {/* Редактор Jinja2 */}
          <div className="lg:col-span-8 bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
            {selectedSpec ? (
              <form onSubmit={handleSavePrompt} className="space-y-4">
                <div>
                  <h2 className="text-base font-semibold text-white">{selectedSpec.title}</h2>
                  <p className="text-xs text-slate-400 mt-1">{selectedSpec.description}</p>
                </div>

                {/* Доступные переменные Jinja2 */}
                <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 space-y-2">
                  <div className="text-[11px] font-medium text-slate-300">
                    Доступные переменные Jinja2 (клик вставит в шаблон):
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {selectedSpec.variables.map((v) => (
                      <button
                        key={v.name}
                        type="button"
                        onClick={() => insertVariableAtCursor(v.name)}
                        title={v.description}
                        className="px-2 py-1 rounded-lg bg-slate-900 hover:bg-indigo-600/25 border border-slate-800 hover:border-indigo-500/40 text-[11px] font-mono text-indigo-300 cursor-pointer transition"
                      >
                        {`{{ ${v.name} }}`}
                      </button>
                    ))}
                  </div>
                  <details className="text-[11px] text-slate-400 pt-1">
                    <summary className="cursor-pointer hover:text-slate-200">
                      Расшифровка всех переменных ({selectedSpec.variables.length})
                    </summary>
                    <div className="mt-1.5 space-y-1 pl-2 border-l border-slate-800">
                      {selectedSpec.variables.map((v) => (
                        <div key={v.name}>
                          <code className="text-indigo-300">{`{{ ${v.name} }}`}</code> — {v.description}
                        </div>
                      ))}
                    </div>
                  </details>
                </div>

                {/* Поле редактирования шаблона */}
                <div>
                  <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
                    <span>Шаблон (синтаксис Jinja2: {'{{ ... }}'}, {'{% if ... %}'}, {'{% for ... %}'})</span>
                    <span>{promptDraft.length} симв.</span>
                  </div>
                  <textarea
                    ref={textareaRef}
                    rows={16}
                    value={promptDraft}
                    onChange={(e) => setPromptDraft(e.target.value)}
                    spellCheck={false}
                    className="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-800 rounded-xl text-xs font-mono leading-relaxed text-slate-100 focus:outline-none focus:border-indigo-500"
                    required
                  />
                </div>

                {promptError && (
                  <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
                    {promptError}
                  </div>
                )}

                {promptNotice && (
                  <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-300">
                    {promptNotice}
                  </div>
                )}

                {/* Панель действий */}
                <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-800">
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      value={previewGroupId}
                      onChange={(e) => setPreviewGroupId(e.target.value)}
                      className="px-3 py-2 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-200"
                    >
                      {groups.length === 0 ? (
                        <option value="">Демо-контекст</option>
                      ) : (
                        groups.map((g) => (
                          <option key={g.id} value={String(g.id)}>
                            Группа: {g.title}
                          </option>
                        ))
                      )}
                    </select>

                    <button
                      type="button"
                      onClick={handlePreviewPrompt}
                      disabled={previewLoading}
                      className="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-200 flex items-center gap-1.5 cursor-pointer"
                    >
                      <Eye className="w-3.5 h-3.5 text-sky-400" />
                      {previewLoading ? 'Рендерим...' : 'Предпросмотр рендера'}
                    </button>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={handleResetPrompt}
                      disabled={promptSaving}
                      className="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs text-slate-300 flex items-center gap-1.5 cursor-pointer"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                      Сбросить к стандартному
                    </button>

                    <button
                      type="submit"
                      disabled={promptSaving}
                      className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-xs font-medium text-white flex items-center gap-1.5 cursor-pointer"
                    >
                      <Save className="w-3.5 h-3.5" />
                      {promptSaving ? 'Сохранение...' : 'Сохранить шаблон'}
                    </button>
                  </div>
                </div>

                {/* Результат предпросмотра */}
                {previewResult && (
                  <div className="space-y-1.5 pt-2">
                    <div className="text-xs font-medium text-sky-300">
                      Итоговый отрендеренный промпт ({previewResult.length} симв.):
                    </div>
                    <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed bg-slate-950 border border-sky-500/30 rounded-xl p-3.5 max-h-96 overflow-y-auto text-slate-200">
                      {previewResult}
                    </pre>
                  </div>
                )}
              </form>
            ) : (
              <div className="text-sm text-slate-400">Загрузка шаблонов...</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

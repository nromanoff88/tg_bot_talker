import React, { useState } from 'react';
import { CheckCircle2, FileUp, Info } from 'lucide-react';
import { api } from '../../api';
import type { ImportHistoryResult } from '../../types';

interface Props {
  groupId: number;
  onImported: () => void;
}

export function ImportHistoryTab({ groupId, onImported }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [triggerAnalysis, setTriggerAnalysis] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<ImportHistoryResult | null>(null);
  const [error, setError] = useState('');

  const handleImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (files.length === 0) return;
    setUploading(true);
    setError('');
    setResult(null);
    try {
      const res = await api.importTelegramHistory(groupId, files, triggerAnalysis);
      setResult(res);
      onImported();
    } catch (err: any) {
      setError(err.message || 'Ошибка импорта файла');
    } finally {
      setUploading(false);
    }
  };

  const totalSizeMb = files.reduce((acc, f) => acc + f.size, 0) / 1024 / 1024;

  return (
    <div className="max-w-3xl space-y-6">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-4">
        <h3 className="text-base font-semibold text-white">
          Импорт старой истории группы из Telegram Desktop (`messages.html` / `result.json`)
        </h3>

        <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-300 space-y-1.5">
          <div className="flex items-center gap-2 font-medium text-indigo-300 mb-1">
            <Info className="w-4 h-4" /> Как выгрузить историю группы:
          </div>
          <div>1. Открой нужную группу в приложении Telegram Desktop на компьютере.</div>
          <div>2. Нажми «⋮» в правом верхнем углу → «Экспорт истории чата».</div>
          <div>3. Сними галочки с фото/видео/файлов и нажми «Экспортировать» (поддерживается стандартный HTML и JSON).</div>
          <div>
            4. Выбери файл `messages.html` (или сразу все `messages.html`, `messages2.html`, `messages3.html`...) —
            дубликаты сообщений пропускаются автоматически.
          </div>
        </div>

        <form onSubmit={handleImport} className="space-y-4">
          <label className="block border-2 border-dashed border-slate-700 hover:border-indigo-500/60 rounded-2xl p-8 text-center cursor-pointer transition bg-slate-950/60">
            <FileUp className="w-8 h-8 text-indigo-400 mx-auto mb-2" />
            <div className="text-sm font-medium text-white">
              {files.length > 0
                ? files.length === 1
                  ? files[0].name
                  : `Выбрано файлов: ${files.length} (${files.map((f) => f.name).join(', ')})`
                : 'Выбери messages.html (можно сразу несколько) или result.json'}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              {files.length > 0
                ? `Общий размер: ${totalSizeMb.toFixed(2)} МБ`
                : 'Поддерживаются HTML и JSON файлы экспорта Telegram Desktop до 150 МБ'}
            </div>
            <input
              type="file"
              multiple
              accept=".html,.htm,.json,text/html,application/json"
              onChange={(e) => setFiles(e.target.files ? Array.from(e.target.files) : [])}
              className="hidden"
            />
          </label>

          <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={triggerAnalysis}
              onChange={(e) => setTriggerAnalysis(e.target.checked)}
            />
            Сразу запустить фоновое изучение участников, фактов и эпизодов в воркере (`memory-worker`)
          </label>

          {error && (
            <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={files.length === 0 || uploading}
            className="px-6 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-xs font-medium text-white cursor-pointer"
          >
            {uploading ? 'Импортируем историю...' : 'Загрузить историю в память группы'}
          </button>
        </form>
      </div>

      {result && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-2xl p-5 space-y-2 text-xs text-emerald-200">
          <div className="flex items-center gap-2 font-semibold text-sm text-emerald-300">
            <CheckCircle2 className="w-4 h-4" /> История успешно загружена!
          </div>
          <div>• Добавлено текстовых сообщений: {result.imported_messages}</div>
          <div>• Пропущено дубликатов: {result.skipped_duplicates}</div>
          <div>• Создано новых карточек участников: {result.discovered_members}</div>
          <div>• Поставлено батчей анализа в очередь воркера: {result.queued_analysis_batches}</div>
        </div>
      )}
    </div>
  );
}

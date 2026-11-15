import React, { useEffect, useState } from 'react';
import {
  Bot,
  LayoutDashboard,
  LogOut,
  Settings,
  Sparkles,
  Lock,
  Radio,
} from 'lucide-react';
import { api } from './api';
import type { DashboardStats } from './types';
import { DashboardPage } from './components/DashboardPage';
import { PersonasPage } from './components/PersonasPage';
import { SettingsPage } from './components/SettingsPage';
import { GroupDetailPage } from './components/group/GroupDetailPage';

type ActiveRoute =
  | { page: 'dashboard' }
  | { page: 'personas' }
  | { page: 'settings' }
  | { page: 'group'; groupId: number };

export default function App() {
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [adminUser, setAdminUser] = useState('');
  const [loginUsername, setLoginUsername] = useState('admin');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  const [route, setRoute] = useState<ActiveRoute>({ page: 'dashboard' });
  const [stats, setStats] = useState<DashboardStats | null>(null);

  const refreshStats = async () => {
    try {
      const s = await api.getStats();
      setStats(s);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    api
      .checkAuth()
      .then((res) => {
        setAuthenticated(res.authenticated);
        setAdminUser(res.username);
      })
      .catch(() => setAuthenticated(false))
      .finally(() => setAuthChecked(true));

    const handleUnauth = () => setAuthenticated(false);
    window.addEventListener('auth:unauthorized', handleUnauth);
    return () => window.removeEventListener('auth:unauthorized', handleUnauth);
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    refreshStats();
    const timer = setInterval(refreshStats, 15000);
    return () => clearInterval(timer);
  }, [authenticated]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginError('');
    setLoginLoading(true);
    try {
      const res = await api.login(loginUsername, loginPassword);
      setAuthenticated(true);
      setAdminUser(res.username);
      setLoginPassword('');
    } catch (err: any) {
      setLoginError(err.message || 'Ошибка входа');
    } finally {
      setLoginLoading(false);
    }
  };

  const handleLogout = async () => {
    await api.logout();
    setAuthenticated(false);
  };

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-400">
        Загрузка панели...
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
        <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-11 h-11 rounded-xl bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
              <Bot className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-white">Bot Talker</h1>
              <p className="text-xs text-slate-400">Панель управления живым участником групп</p>
            </div>
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Логин администратора</label>
              <input
                type="text"
                value={loginUsername}
                onChange={(e) => setLoginUsername(e.target.value)}
                className="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white focus:outline-none focus:border-indigo-500"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Пароль</label>
              <input
                type="password"
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-800 rounded-xl text-sm text-white focus:outline-none focus:border-indigo-500"
                required
              />
            </div>

            {loginError && (
              <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
                {loginError}
              </div>
            )}

            <button
              type="submit"
              disabled={loginLoading}
              className="w-full py-2.5 px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition flex items-center justify-center gap-2 cursor-pointer"
            >
              <Lock className="w-4 h-4" />
              {loginLoading ? 'Вход...' : 'Войти в админку'}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col lg:flex-row bg-slate-950 text-slate-100">
      {/* Sidebar */}
      <aside className="w-full lg:w-64 bg-slate-900/80 border-b lg:border-b-0 lg:border-r border-slate-800 flex flex-col shrink-0">
        <div className="p-5 border-b border-slate-800 flex items-center justify-between lg:justify-start gap-3">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center text-indigo-400">
              <Bot className="w-5 h-5" />
            </div>
            <div>
              <div className="font-semibold text-sm text-white leading-tight">Bot Talker</div>
              <div className="text-[11px] text-slate-400 flex items-center gap-1.5 mt-0.5">
                <Radio
                  className={`w-3 h-3 ${stats?.bot_online ? 'text-emerald-400 animate-pulse' : 'text-slate-500'}`}
                />
                {stats?.bot_online
                  ? `@${stats.bot_username || 'online'}`
                  : 'Бот оффлайн / ждёт токен'}
              </div>
            </div>
          </div>
        </div>

        <nav className="p-3 flex lg:flex-col gap-1.5 overflow-x-auto">
          <button
            onClick={() => setRoute({ page: 'dashboard' })}
            className={`flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl text-sm font-medium transition cursor-pointer whitespace-nowrap ${
              route.page === 'dashboard' || route.page === 'group'
                ? 'bg-indigo-600/15 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <LayoutDashboard className="w-4 h-4" />
            Группы и Дашборд
          </button>

          <button
            onClick={() => setRoute({ page: 'personas' })}
            className={`flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl text-sm font-medium transition cursor-pointer whitespace-nowrap ${
              route.page === 'personas'
                ? 'bg-indigo-600/15 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Sparkles className="w-4 h-4" />
            Шаблоны Персон
          </button>

          <button
            onClick={() => setRoute({ page: 'settings' })}
            className={`flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl text-sm font-medium transition cursor-pointer whitespace-nowrap ${
              route.page === 'settings'
                ? 'bg-indigo-600/15 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Settings className="w-4 h-4" />
            Модели и API
          </button>
        </nav>

        <div className="mt-auto hidden lg:block p-4 border-t border-slate-800 space-y-3">
          {stats && (
            <div className="p-3 rounded-xl bg-slate-950/80 border border-slate-800/80 text-xs space-y-1">
              <div className="flex justify-between text-slate-400">
                <span>Расход за 24ч:</span>
                <span className="text-slate-200 font-medium">${stats.cost_24h_usd.toFixed(4)}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Токены (24ч):</span>
                <span className="text-slate-300">{stats.tokens_24h.toLocaleString('ru-RU')}</span>
              </div>
            </div>
          )}

          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Админ: {adminUser}</span>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1 text-slate-400 hover:text-rose-400 transition cursor-pointer"
            >
              <LogOut className="w-3.5 h-3.5" />
              Выйти
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 min-w-0 p-4 lg:p-8 overflow-y-auto">
        {route.page === 'dashboard' && (
          <DashboardPage
            stats={stats}
            onOpenGroup={(groupId) => setRoute({ page: 'group', groupId })}
            onRefreshStats={refreshStats}
          />
        )}

        {route.page === 'personas' && <PersonasPage />}

        {route.page === 'settings' && <SettingsPage onSaved={refreshStats} />}

        {route.page === 'group' && (
          <GroupDetailPage
            groupId={route.groupId}
            onBack={() => {
              refreshStats();
              setRoute({ page: 'dashboard' });
            }}
          />
        )}
      </main>
    </div>
  );
}


import type {
  ChatMessage,
  DashboardStats,
  DecisionLog,
  ExternalContextStatus,
  GeneratePollRequest,
  GroupContext,
  GroupDetail,
  GroupMember,
  GroupMemberDetail,
  GroupSettings,
  GroupSummary,
  ImportHistoryResult,
  MemberFact,
  MemoryEpisode,
  PersonaOverride,
  PersonaTemplate,
  PollPayload,
  PromptTemplateSpec,
  SandboxChatMessage,
  SandboxChatResponse,
  SandboxProactiveResponse,
  SystemConfig,
} from './types';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };
  if (!(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(path, {
    ...options,
    credentials: 'include',
    headers,
  });

  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('auth:unauthorized'));
    throw new Error('Требуется авторизация');
  }

  if (!res.ok) {
    let errText = `Ошибка ${res.status}`;
    try {
      const data = await res.json();
      if (data?.detail) errText = String(data.detail);
    } catch {
      // ignore
    }
    throw new Error(errText);
  }

  return res.json() as Promise<T>;
}

export const api = {
  // Auth
  checkAuth: () => request<{ authenticated: boolean; username: string }>('/api/auth/me'),
  login: (username: string, password: string) =>
    request<{ authenticated: boolean; username: string; token: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  // Dashboard & System Config
  getStats: () => request<DashboardStats>('/api/settings/stats'),
  getLLMConfig: () => request<SystemConfig>('/api/settings/llm'),
  updateLLMConfig: (payload: {
    llm_base_url: string;
    llm_api_key?: string;
    llm_fast_model: string;
    llm_smart_model: string;
    embedding_base_url: string;
    embedding_api_key?: string;
    embedding_model: string;
    weather_city?: string;
    news_tg_channels?: string[];
    news_topics?: string;
  }) =>
    request<SystemConfig>('/api/settings/llm', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  getExternalContext: () => request<ExternalContextStatus>('/api/settings/external-context'),
  refreshExternalContext: () =>
    request<ExternalContextStatus>('/api/settings/external-context/refresh', { method: 'POST' }),
  testLLMConfig: () =>
    request<{
      fast_ok: boolean;
      smart_ok: boolean;
      embedding_ok: boolean;
      fast_model?: string;
      smart_model?: string;
      smart_sample?: string;
      errors: string[];
    }>('/api/settings/llm/test', { method: 'POST' }),

  // Persona Templates
  listPersonas: () => request<PersonaTemplate[]>('/api/personas'),
  createPersona: (payload: Omit<PersonaTemplate, 'id' | 'created_at' | 'updated_at'>) =>
    request<PersonaTemplate>('/api/personas', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updatePersona: (id: string, payload: Omit<PersonaTemplate, 'id' | 'created_at' | 'updated_at'>) =>
    request<PersonaTemplate>(`/api/personas/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deletePersona: (id: string) => request(`/api/personas/${id}`, { method: 'DELETE' }),

  // Prompt Templates (Jinja2)
  getPromptTemplates: () => request<PromptTemplateSpec[]>('/api/settings/prompts'),
  updatePromptTemplates: (templates: Record<string, string>) =>
    request<PromptTemplateSpec[]>('/api/settings/prompts', {
      method: 'PUT',
      body: JSON.stringify({ templates }),
    }),
  previewPromptTemplate: (key: string, template: string, group_id?: number | null) =>
    request<{ rendered: string; variable_values: Record<string, string> }>('/api/settings/prompts/preview', {
      method: 'POST',
      body: JSON.stringify({ key, template, group_id: group_id ?? null }),
    }),

  // Groups
  listGroups: () => request<GroupSummary[]>('/api/groups'),
  registerGroup: (group_id: number, title: string) =>
    request<GroupDetail>('/api/groups/register', {
      method: 'POST',
      body: JSON.stringify({ group_id, title }),
    }),
  getGroup: (groupId: number) => request<GroupDetail>(`/api/groups/${groupId}`),
  deleteGroup: (groupId: number) => request(`/api/groups/${groupId}`, { method: 'DELETE' }),
  updateGroupPersona: (
    groupId: number,
    payload: { is_active: boolean; persona_template_id: string | null; persona_override: PersonaOverride }
  ) =>
    request<GroupDetail>(`/api/groups/${groupId}/persona`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  updateGroupSettings: (groupId: number, payload: GroupSettings) =>
    request<GroupSettings>(`/api/groups/${groupId}/settings`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  updateGroupContext: (groupId: number, payload: GroupContext) =>
    request<GroupContext>(`/api/groups/${groupId}/context`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  // Episodes (pgvector)
  listEpisodes: (groupId: number, query?: string) => {
    const qs = query ? `?q=${encodeURIComponent(query)}` : '';
    return request<MemoryEpisode[]>(`/api/groups/${groupId}/episodes${qs}`);
  },
  createEpisode: (
    groupId: number,
    payload: { title: string; episode_summary: string; participant_names: string[]; is_locked: boolean }
  ) =>
    request<MemoryEpisode>(`/api/groups/${groupId}/episodes`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateEpisode: (
    groupId: number,
    episodeId: string,
    payload: Partial<{ title: string; episode_summary: string; participant_names: string[]; is_locked: boolean }>
  ) =>
    request<MemoryEpisode>(`/api/groups/${groupId}/episodes/${episodeId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteEpisode: (groupId: number, episodeId: string) =>
    request(`/api/groups/${groupId}/episodes/${episodeId}`, { method: 'DELETE' }),

  // Members & Facts
  listMembers: (groupId: number) => request<GroupMember[]>(`/api/groups/${groupId}/members`),
  getMember: (groupId: number, memberId: string) =>
    request<GroupMemberDetail>(`/api/groups/${groupId}/members/${memberId}`),
  updateMember: (
    groupId: number,
    memberId: string,
    payload: {
      display_name?: string;
      chat_nickname: string;
      nickname_locked: boolean;
      character_summary: string;
      character_locked: boolean;
      bot_attitude: string;
      attitude_locked: boolean;
      admin_notes: string;
    }
  ) =>
    request<GroupMemberDetail>(`/api/groups/${groupId}/members/${memberId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  mergeMember: (groupId: number, targetMemberId: string, sourceMemberId: string) =>
    request<GroupMemberDetail>(`/api/groups/${groupId}/members/${targetMemberId}/merge`, {
      method: 'POST',
      body: JSON.stringify({ source_member_id: sourceMemberId }),
    }),
  deleteMember: (groupId: number, memberId: string) =>
    request(`/api/groups/${groupId}/members/${memberId}`, { method: 'DELETE' }),
  createFact: (
    groupId: number,
    memberId: string,
    payload: { category: string; fact_text: string; is_pinned: boolean }
  ) =>
    request<MemberFact>(`/api/groups/${groupId}/members/${memberId}/facts`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateFact: (
    groupId: number,
    memberId: string,
    factId: string,
    payload: Partial<{ category: string; fact_text: string; is_pinned: boolean }>
  ) =>
    request<MemberFact>(`/api/groups/${groupId}/members/${memberId}/facts/${factId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteFact: (groupId: number, memberId: string, factId: string) =>
    request(`/api/groups/${groupId}/members/${memberId}/facts/${factId}`, { method: 'DELETE' }),

  // Live Chat, Decisions & Actions
  listMessages: (groupId: number, limit = 80) =>
    request<ChatMessage[]>(`/api/groups/${groupId}/messages?limit=${limit}`),
  listDecisions: (groupId: number, limit = 60) =>
    request<DecisionLog[]>(`/api/groups/${groupId}/decisions?limit=${limit}`),
  sendManualMessage: (groupId: number, text: string, reply_to_tg_msg_id: number | null) =>
    request(`/api/groups/${groupId}/actions/send-message`, {
      method: 'POST',
      body: JSON.stringify({ text, reply_to_tg_msg_id }),
    }),
  sendPoll: (groupId: number, payload: PollPayload) =>
    request<{ queued: boolean; poll: PollPayload }>(`/api/groups/${groupId}/actions/send-poll`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  generatePollDraft: (groupId: number, payload: GeneratePollRequest) =>
    request<PollPayload>(`/api/groups/${groupId}/actions/generate-poll`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  forceAnalyzeMemory: (groupId: number) =>
    request<{ queued: boolean; message: string }>(`/api/groups/${groupId}/actions/analyze-memory`, {
      method: 'POST',
    }),
  forceProactive: (groupId: number) =>
    request<{ status: string; text?: string }>(`/api/groups/${groupId}/actions/trigger-proactive`, {
      method: 'POST',
    }),
  sandboxChat: (
    groupId: number,
    payload: { messages: SandboxChatMessage[]; include_real_history: boolean; tag_bot: boolean }
  ) =>
    request<SandboxChatResponse>(`/api/groups/${groupId}/sandbox/chat`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  sandboxProactive: (
    groupId: number,
    payload: {
      mode: 'auto' | 'revive' | 'new_topic';
      silence_hours_override?: number | null;
      sandbox_messages?: SandboxChatMessage[];
      include_real_history: boolean;
    }
  ) =>
    request<SandboxProactiveResponse>(`/api/groups/${groupId}/sandbox/proactive`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  importTelegramHistory: (groupId: number, files: File[], triggerAnalysis: boolean) => {
    const fd = new FormData();
    for (const f of files) {
      fd.append('files', f);
    }
    return request<ImportHistoryResult>(
      `/api/groups/${groupId}/import?trigger_analysis=${triggerAnalysis}`,
      {
        method: 'POST',
        body: fd,
      }
    );
  },
};


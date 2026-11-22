export interface DashboardStats {
  total_groups: number;
  active_groups: number;
  total_members: number;
  total_messages: number;
  total_facts: number;
  total_episodes: number;
  tokens_24h: number;
  cost_24h_usd: number;
  tokens_total: number;
  cost_total_usd: number;
  bot_online: boolean;
  bot_username: string | null;
}

export interface SystemConfig {
  llm_base_url: string;
  llm_api_key_masked: string;
  has_llm_api_key: boolean;
  llm_fast_model: string;
  llm_smart_model: string;
  embedding_base_url: string;
  embedding_api_key_masked: string;
  has_embedding_api_key: boolean;
  embedding_model: string;
  bot_telegram_id: number | null;
  bot_username: string | null;
  bot_display_name: string | null;
  bot_online: boolean;
  bot_last_heartbeat: string | null;
}

export interface PersonaTemplate {
  id: string;
  name: string;
  description: string;
  display_name: string;
  backstory: string;
  communication_style: string;
  taboos: string;
  example_phrases: string[];
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface GroupSummary {
  id: number;
  title: string;
  chat_type: string;
  is_active: boolean;
  persona_template_id: string | null;
  persona_name: string;
  member_count: number;
  message_count: number;
  unanalyzed_msg_count: number;
  last_message_at: string | null;
  last_bot_message_at: string | null;
}

export interface GroupSettings {
  chattiness_level: number;
  debounce_seconds: number;
  cooldown_seconds: number;
  max_msgs_per_hour: number;
  context_window_messages: number;
  simulate_typing: boolean;
  reply_on_mention: boolean;
  reactions_enabled: boolean;
  allowed_emojis: string[];
  proactive_enabled: boolean;
  proactive_chance_per_hour: number;
  min_silence_minutes: number;
  proactive_cooldown_hours: number;
  new_topic_after_hours: number;
  timezone: string;
  quiet_hours_enabled: boolean;
  quiet_hours_start: string;
  quiet_hours_end: string;
  memory_batch_size: number;
}

export interface GroupRuleOrJokeItem {
  id: string;
  kind: 'meme' | 'rule' | 'norm';
  text: string;
  is_locked: boolean;
}

export interface GroupContext {
  summary: string;
  summary_locked: boolean;
  inside_jokes_and_rules: GroupRuleOrJokeItem[];
  active_topics: string[];
  updated_at: string | null;
}

export interface PersonaOverride {
  use_custom?: boolean;
  display_name?: string;
  backstory?: string;
  communication_style?: string;
  taboos?: string;
  example_phrases?: string[];
}

export interface GroupDetail {
  id: number;
  title: string;
  chat_type: string;
  is_active: boolean;
  persona_template_id: string | null;
  persona_override: PersonaOverride;
  unanalyzed_msg_count: number;
  unanswered_initiatives: number;
  last_message_at: string | null;
  last_bot_message_at: string | null;
  last_proactive_at: string | null;
  last_analyzed_at: string | null;
  settings: GroupSettings;
  context: GroupContext;
}

export interface MemberFact {
  id: string;
  member_id: string;
  category: string;
  fact_text: string;
  confidence: number;
  source_message_id: number | null;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface GroupMember {
  id: string;
  group_id: number;
  telegram_user_id: number;
  username: string | null;
  display_name: string;
  chat_nickname: string;
  nickname_locked: boolean;
  character_summary: string;
  character_locked: boolean;
  bot_attitude: string;
  attitude_locked: boolean;
  admin_notes: string;
  message_count: number;
  last_seen_at: string | null;
  facts_count: number;
}

export interface GroupMemberDetail extends GroupMember {
  facts: MemberFact[];
}

export interface MemoryEpisode {
  id: string;
  group_id: number;
  title: string;
  episode_summary: string;
  participant_names: string[];
  is_locked: boolean;
  has_embedding: boolean;
  similarity?: number | null;
  happened_at: string;
  created_at: string;
}

export interface ChatMessage {
  id: number;
  group_id: number;
  telegram_message_id: number;
  member_id: string | null;
  sender_name: string;
  sender_username: string | null;
  is_from_bot: boolean;
  reply_to_tg_msg_id: number | null;
  text_content: string;
  reactions: Record<string, number>;
  is_imported: boolean;
  is_processed_for_memory: boolean;
  created_at: string;
}

export interface DecisionLog {
  id: string;
  group_id: number;
  trigger_tg_msg_ids: number[];
  decision_type: string;
  router_reasoning: string;
  action_payload: Record<string, any>;
  retrieved_context_meta: Record<string, any>;
  model_used: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  created_at: string;
}

export interface ImportHistoryResult {
  imported_messages: number;
  skipped_duplicates: number;
  discovered_members: number;
  queued_analysis_batches: number;
}

export interface PollPayload {
  question: string;
  options: string[];
  is_anonymous: boolean;
  type: 'regular' | 'quiz';
  allows_multiple_answers: boolean;
  correct_option_id: number | null;
  explanation: string | null;
  open_period: number | null;
  is_closed: boolean;
  protect_content: boolean;
  reply_to_tg_msg_id: number | null;
}

export interface GeneratePollRequest {
  topic_hint: string;
  type: 'regular' | 'quiz';
  is_anonymous: boolean;
  allows_multiple_answers: boolean;
}

export interface PromptVariableDoc {
  name: string;
  description: string;
}

export interface PromptTemplateSpec {
  key: string;
  title: string;
  description: string;
  template: string;
  default_template: string;
  is_custom: boolean;
  variables: PromptVariableDoc[];
}


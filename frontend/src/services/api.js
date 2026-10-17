/**
 * Talk2Data API Service Client.
 * Centralized, typed fetch wrapper for all Talk2Data REST endpoints.
 */

export const api = {
  // Sources
  async getSources() {
    const res = await fetch('/api/v1/sources');
    if (!res.ok) throw new Error(`Failed to fetch sources: ${res.statusText}`);
    return res.json();
  },

  async getSourceCatalog(sourceId) {
    const res = await fetch(`/api/v1/sources/${sourceId}/catalog`);
    if (!res.ok) throw new Error(`Failed to fetch catalog: ${res.statusText}`);
    return res.json();
  },

  async uploadDatabase(file, sourceName = '') {
    const formData = new FormData();
    formData.append('file', file);
    if (sourceName) formData.append('source_name', sourceName);

    const res = await fetch('/api/v1/sources/upload', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Upload failed: ${res.statusText}`);
    }
    return res.json();
  },

  // Conversations
  async getConversations() {
    const res = await fetch('/api/v1/conversations');
    if (!res.ok) throw new Error(`Failed to fetch conversations: ${res.statusText}`);
    return res.json();
  },

  async createConversation(title = 'New Analysis') {
    const res = await fetch('/api/v1/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) throw new Error(`Failed to create conversation: ${res.statusText}`);
    return res.json();
  },

  async getConversation(conversationId) {
    const res = await fetch(`/api/v1/conversations/${conversationId}`);
    if (!res.ok) throw new Error(`Failed to get conversation: ${res.statusText}`);
    return res.json();
  },

  async getFollowUps(conversationId) {
    const res = await fetch(`/api/v1/conversations/${conversationId}/follow-ups`);
    if (!res.ok) return { suggestions: [] };
    return res.json();
  },

  // Turns
  async submitQuestion(conversationId, sourceId, questionText, proposedLlmJson = null) {
    const payload = { source_id: sourceId, question: questionText };
    if (proposedLlmJson) payload.proposed_llm_json = proposedLlmJson;

    const res = await fetch(`/api/v1/conversations/${conversationId}/questions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Failed to submit question: ${res.statusText}`);
    return res.json();
  },

  async getTurn(turnId) {
    const res = await fetch(`/api/v1/turns/${turnId}`);
    if (!res.ok) throw new Error(`Failed to fetch turn: ${res.statusText}`);
    return res.json();
  },

  async getTurnAnswer(turnId) {
    const res = await fetch(`/api/v1/turns/${turnId}/answer`);
    if (!res.ok) throw new Error(`Failed to fetch answer: ${res.statusText}`);
    return res.json();
  },

  async submitClarification(turnId, choiceId) {
    const res = await fetch(`/api/v1/turns/${turnId}/clarification`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ choice_id: choiceId }),
    });
    if (!res.ok) throw new Error(`Failed to submit clarification: ${res.statusText}`);
    return res.json();
  },

  async cancelTurn(turnId) {
    const res = await fetch(`/api/v1/turns/${turnId}/cancel`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`Failed to cancel turn: ${res.statusText}`);
    return res.json();
  },

  // Health
  async checkHealth() {
    const res = await fetch('/healthz');
    return res.ok;
  },
};

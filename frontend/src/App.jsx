import React, { useState, useEffect, useRef } from 'react';
import { 
  Sparkles, 
  Send, 
  Plus, 
  Database, 
  LayoutDashboard, 
  MessageSquare, 
  BookOpen, 
  Shield,
  Upload
} from 'lucide-react';

import { api } from './services/api';
import StudioDashboard from './components/StudioDashboard';
import ConversationStream from './components/ConversationStream';
import CatalogDictionary from './components/CatalogDictionary';
import GovernanceAudit from './components/GovernanceAudit';
import DatabaseUploadModal from './components/DatabaseUploadModal';

export default function App() {
  const [sources, setSources] = useState([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [sourceCatalog, setSourceCatalog] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [turns, setTurns] = useState([]);
  const [answers, setAnswers] = useState({}); // turn_id -> answer_data
  const [activeTurnId, setActiveTurnId] = useState(null);
  const [currentView, setCurrentView] = useState('dashboard'); // 'dashboard', 'stream', 'catalog', 'governance'
  const [followUps, setFollowUps] = useState([]);
  const [questionText, setQuestionText] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isConnected, setIsConnected] = useState(true);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  const pollIntervals = useRef({});

  // Initial Load: sources & conversation list
  useEffect(() => {
    loadSources();
    loadConversations();

    return () => {
      Object.values(pollIntervals.current).forEach(clearInterval);
    };
  }, []);

  // Fetch catalog when selectedSource changes
  useEffect(() => {
    if (selectedSource) {
      loadCatalog(selectedSource);
    }
  }, [selectedSource]);

  const loadSources = async () => {
    try {
      const data = await api.getSources();
      setSources(data);
      if (data.length > 0 && !selectedSource) {
        setSelectedSource(data[0].source_id);
      }
      setIsConnected(true);
    } catch (err) {
      console.error('Failed to load sources:', err);
      setIsConnected(false);
    }
  };

  const loadCatalog = async (sourceId) => {
    try {
      const data = await api.getSourceCatalog(sourceId);
      setSourceCatalog(data);
    } catch (err) {
      console.error('Failed to load catalog:', err);
    }
  };

  const loadConversations = async () => {
    try {
      const data = await api.getConversations();
      setConversations(data);
      if (data.length > 0 && !currentConversationId) {
        selectConversation(data[0].conversation_id);
      } else if (data.length === 0) {
        createNewConversation();
      }
    } catch (err) {
      console.error('Failed to load conversations:', err);
    }
  };

  const createNewConversation = async () => {
    try {
      const title = `Analysis ${new Date().toLocaleDateString()}`;
      const newConv = await api.createConversation(title);
      setConversations((prev) => [newConv, ...prev]);
      setCurrentConversationId(newConv.conversation_id);
      setTurns([]);
      setAnswers({});
      setActiveTurnId(null);
      return newConv.conversation_id;
    } catch (err) {
      console.error('Failed to create conversation:', err);
      return null;
    }
  };

  const selectConversation = async (convId) => {
    setCurrentConversationId(convId);
    try {
      const data = await api.getConversation(convId);
      const convTurns = data.turns || [];
      setTurns(convTurns);

      // Load answers for answered turns
      for (const t of convTurns) {
        if (t.status === 'ANSWERED' && !answers[t.turn_id]) {
          loadAnswer(t.turn_id);
        }
        if (['SUBMITTED', 'INTERPRETING', 'ANALYZING', 'COMPOSING'].includes(t.status)) {
          startPolling(t.turn_id, convId);
        }
      }

      if (convTurns.length > 0) {
        setActiveTurnId(convTurns[convTurns.length - 1].turn_id);
      } else {
        setActiveTurnId(null);
      }

      loadFollowUps(convId);
    } catch (err) {
      console.error(`Failed to load conversation ${convId}:`, err);
    }
  };

  const loadAnswer = async (turnId) => {
    try {
      const answerData = await api.getTurnAnswer(turnId);
      setAnswers((prev) => ({ ...prev, [turnId]: answerData }));
    } catch (err) {
      console.error(`Failed to fetch answer for turn ${turnId}:`, err);
    }
  };

  const loadFollowUps = async (convId) => {
    try {
      const data = await api.getFollowUps(convId);
      setFollowUps(data.suggestions || data.suggested_questions || []);
    } catch {
      setFollowUps([]);
    }
  };

  const startPolling = (turnId, convId) => {
    if (pollIntervals.current[turnId]) {
      clearInterval(pollIntervals.current[turnId]);
    }

    pollIntervals.current[turnId] = setInterval(async () => {
      try {
        const updatedTurn = await api.getTurn(turnId);
        setTurns((prev) => prev.map((t) => (t.turn_id === turnId ? updatedTurn : t)));

        const isFinished =
          ['ANSWERED', 'DECLINED', 'FAILED', 'CANCELLED'].includes(updatedTurn.status) ||
          (updatedTurn.status === 'AWAITING_CLARIFICATION' &&
            !['QUEUED', 'RUNNING'].includes(updatedTurn.job_status));

        if (isFinished) {
          clearInterval(pollIntervals.current[turnId]);
          delete pollIntervals.current[turnId];

          if (updatedTurn.status === 'ANSWERED') {
            await loadAnswer(turnId);
            setActiveTurnId(turnId);
          }
          loadFollowUps(convId);
        }
      } catch (err) {
        console.error(`Polling error for turn ${turnId}:`, err);
      }
    }, 1000);
  };

  const handleSubmitQuestion = async (customText = null) => {
    const text = (customText || questionText).trim();
    if (!text || !selectedSource) return;

    setIsSubmitting(true);
    let targetConvId = currentConversationId;

    if (!targetConvId) {
      targetConvId = await createNewConversation();
      if (!targetConvId) {
        setIsSubmitting(false);
        return;
      }
    }

    setQuestionText('');

    try {
      const data = await api.submitQuestion(targetConvId, selectedSource, text);
      setActiveTurnId(data.turn_id);
      await selectConversation(targetConvId);
      startPolling(data.turn_id, targetConvId);
      setCurrentView('dashboard');
    } catch (err) {
      console.error('Failed to submit question:', err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSelectChoice = async (turnId, choiceId) => {
    try {
      await api.submitClarification(turnId, choiceId);
      await selectConversation(currentConversationId);
      startPolling(turnId, currentConversationId);
    } catch (err) {
      console.error('Failed to submit clarification choice:', err);
    }
  };

  const handleCancelTurn = async (turnId) => {
    try {
      await api.cancelTurn(turnId);
      if (pollIntervals.current[turnId]) {
        clearInterval(pollIntervals.current[turnId]);
        delete pollIntervals.current[turnId];
      }
      setTurns((prev) =>
        prev.map((t) => (t.turn_id === turnId ? { ...t, status: 'CANCELLED' } : t))
      );
    } catch (err) {
      console.error('Failed to cancel turn:', err);
    }
  };

  const activeTurn = turns.find((t) => t.turn_id === activeTurnId) || turns[turns.length - 1];
  const activeAnswer = activeTurn ? answers[activeTurn.turn_id] : null;

  const catalogStats = sourceCatalog
    ? {
        tables: sourceCatalog.tables?.length || 0,
        measures: Object.keys(sourceCatalog.measures || {}).length,
      }
    : null;

  return (
    <div className="analytics-app-container">
      {/* Top Studio Header */}
      <header className="app-header">
        <div className="header-left">
          <div className="brand-logo">
            <Sparkles size={22} color="var(--accent-blue)" />
            <span className="brand-title">Talk2Data</span>
          </div>
          <span className="brand-sub">Enterprise Conversational Analytics</span>
          <div className="connection-badge">
            <span className={`status-dot ${isConnected ? 'live' : 'offline'}`} />
            <span>{isConnected ? 'Governance Engine Online' : 'Connecting...'}</span>
          </div>
        </div>

        {/* View Switcher Tabs */}
        <nav className="header-nav-tabs">
          <button
            onClick={() => setCurrentView('dashboard')}
            className={`nav-tab ${currentView === 'dashboard' ? 'active' : ''}`}
          >
            <LayoutDashboard size={15} />
            <span>Studio Dashboard</span>
          </button>
          <button
            onClick={() => setCurrentView('stream')}
            className={`nav-tab ${currentView === 'stream' ? 'active' : ''}`}
          >
            <MessageSquare size={15} />
            <span>Conversation Stream</span>
            {turns.length > 0 && <span className="tab-counter">{turns.length}</span>}
          </button>
          <button
            onClick={() => setCurrentView('catalog')}
            className={`nav-tab ${currentView === 'catalog' ? 'active' : ''}`}
          >
            <BookOpen size={15} />
            <span>Catalog & Metrics</span>
          </button>
          <button
            onClick={() => setCurrentView('governance')}
            className={`nav-tab ${currentView === 'governance' ? 'active' : ''}`}
          >
            <Shield size={15} />
            <span>Governance Audit</span>
          </button>
        </nav>

        {/* Source Selector & New Conversation */}
        <div className="header-right">
          <div className="source-picker-group">
            <Database size={15} color="var(--accent-blue)" />
            <select
              value={selectedSource}
              onChange={(e) => setSelectedSource(e.target.value)}
              className="source-select"
            >
              {sources.map((s) => (
                <option key={s.source_id} value={s.source_id}>
                  {s.name || s.source_id}
                </option>
              ))}
            </select>
            {catalogStats && (
              <span className="source-stats-badge" title="Introspected Schema Stats">
                {catalogStats.tables} tbls • {catalogStats.measures} metrics
              </span>
            )}
          </div>
          <button onClick={() => setIsUploadModalOpen(true)} className="btn-connect-db" title="Upload new SQLite database and extract relationships">
            <Upload size={14} />
            <span>Connect DB</span>
          </button>
          <button onClick={createNewConversation} className="btn-new-conv">
            <Plus size={15} />
            <span>New Session</span>
          </button>
        </div>
      </header>

      {/* Main Workspace Layout */}
      <div className="workspace-body">
        {/* Sidebar: Conversation Sessions */}
        <aside className="workspace-sidebar">
          <div className="sidebar-header">
            <h3>Analysis Sessions</h3>
            <span className="conv-count">{conversations.length}</span>
          </div>
          <div id="conv-list" className="conversations-scroll">
            {conversations.map((c) => (
              <div
                key={c.conversation_id}
                onClick={() => selectConversation(c.conversation_id)}
                className={`conv-item ${c.conversation_id === currentConversationId ? 'active' : ''}`}
              >
                <div className="conv-title">{c.title || 'Analysis Session'}</div>
                <div className="conv-meta">
                  <span>{c.tenant_id}</span>
                  <span>{new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
              </div>
            ))}
          </div>
        </aside>

        {/* Central Analytical Viewport */}
        <main className="workspace-main">
          <div className="viewport-content">
            {currentView === 'dashboard' && (
              <StudioDashboard
                activeTurn={activeTurn}
                answer={activeAnswer}
                followUps={followUps}
                onSelectChoice={handleSelectChoice}
                onSubmitQuestion={handleSubmitQuestion}
                onNavigateToCatalog={() => setCurrentView('catalog')}
              />
            )}

            {currentView === 'stream' && (
              <ConversationStream
                turns={turns}
                activeTurnId={activeTurnId}
                onSelectTurn={setActiveTurnId}
                onSelectChoice={handleSelectChoice}
                onCancelTurn={handleCancelTurn}
                onOpenInStudio={(turnId) => {
                  setActiveTurnId(turnId);
                  setCurrentView('dashboard');
                }}
              />
            )}

            {currentView === 'catalog' && (
              <CatalogDictionary
                catalog={sourceCatalog}
                selectedSource={selectedSource}
                onQuickQuery={(q) => {
                  setQuestionText(q);
                  setCurrentView('dashboard');
                }}
                onOpenUpload={() => setIsUploadModalOpen(true)}
              />
            )}

            {currentView === 'governance' && (
              <GovernanceAudit turns={turns} answers={answers} />
            )}
          </div>

          {/* Unified Analytics Question Input Bar */}
          <footer className="workspace-footer">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSubmitQuestion();
              }}
              className="query-input-form"
            >
              <input
                id="question-input"
                type="text"
                placeholder={`Ask an analytical question on ${selectedSource || 'data'} (e.g. "What was total sales revenue in 2024?")...`}
                value={questionText}
                onChange={(e) => setQuestionText(e.target.value)}
                disabled={isSubmitting}
                className="main-query-input"
              />
              <button
                id="btn-submit"
                type="submit"
                disabled={isSubmitting || !questionText.trim()}
                className="btn-submit-query"
              >
                <Send size={16} />
                <span>Submit</span>
              </button>
            </form>
          </footer>
        </main>
      </div>

      {/* Database Connect & Auto-Introspect Modal */}
      <DatabaseUploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onSourceCreated={async (newSourceId) => {
          await loadSources();
          setSelectedSource(newSourceId);
          await loadCatalog(newSourceId);
          setCurrentView('catalog');
        }}
      />
    </div>
  );
}

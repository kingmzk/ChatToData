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
  Upload,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
  TrendingUp,
  Zap,
  GitCompare,
  SlidersHorizontal,
  Command
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
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [sessionSearch, setSessionSearch] = useState('');

  const pollIntervals = useRef({});
  const inputRef = useRef(null);

  // Initial Load: sources & conversation list
  useEffect(() => {
    loadSources();
    loadConversations();

    const currentPolls = pollIntervals.current;
    return () => {
      Object.values(currentPolls).forEach(clearInterval);
    };
  }, []);

  // Keyboard shortcut: Cmd+K / Ctrl+K to focus query input
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
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
      const now = new Date();
      const title = `Analysis Session (${now.toLocaleDateString([], { month: 'short', day: 'numeric' })})`;
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

  const filteredConversations = conversations.filter((c) => {
    if (!sessionSearch.trim()) return true;
    const q = sessionSearch.toLowerCase();
    return (
      (c.title && c.title.toLowerCase().includes(q)) ||
      (c.tenant_id && c.tenant_id.toLowerCase().includes(q)) ||
      (c.conversation_id && c.conversation_id.toLowerCase().includes(q))
    );
  });

  return (
    <div className="analytics-app-container">
      {/* Top Studio Header */}
      <header className="app-header">
        <div className="header-left">
          <button 
            onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)} 
            className="btn-toggle-sidebar"
            title={isSidebarCollapsed ? "Expand Sessions Sidebar" : "Collapse Sidebar for Widescreen"}
          >
            {isSidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </button>

          <div className="brand-logo">
            <div className="brand-icon-box">
              <Sparkles size={16} className="brand-icon-sparkle" />
            </div>
            <div className="brand-text-col">
              <span className="brand-title">Talk2Data</span>
              <span className="brand-edition">Enterprise Studio</span>
            </div>
          </div>

          <div className="connection-badge">
            <span className={`status-dot ${isConnected ? 'live' : 'offline'}`} />
            <span className="connection-text">{isConnected ? 'Governance Engine Live' : 'Connecting...'}</span>
          </div>
        </div>

        {/* View Switcher Tabs */}
        <nav className="header-nav-tabs">
          <button
            onClick={() => setCurrentView('dashboard')}
            className={`nav-tab ${currentView === 'dashboard' ? 'active' : ''}`}
          >
            <LayoutDashboard size={14} />
            <span>Studio Dashboard</span>
          </button>
          <button
            onClick={() => setCurrentView('stream')}
            className={`nav-tab ${currentView === 'stream' ? 'active' : ''}`}
          >
            <MessageSquare size={14} />
            <span>Conversation Stream</span>
            {turns.length > 0 && <span className="tab-counter">{turns.length}</span>}
          </button>
          <button
            onClick={() => setCurrentView('catalog')}
            className={`nav-tab ${currentView === 'catalog' ? 'active' : ''}`}
          >
            <BookOpen size={14} />
            <span>Catalog & Metrics</span>
          </button>
          <button
            onClick={() => setCurrentView('governance')}
            className={`nav-tab ${currentView === 'governance' ? 'active' : ''}`}
          >
            <Shield size={14} />
            <span>Governance Audit</span>
          </button>
        </nav>

        {/* Source Selector & New Conversation */}
        <div className="header-right">
          <div className="source-picker-group">
            <Database size={14} color="var(--accent-cyan)" />
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
          <button 
            onClick={() => setIsUploadModalOpen(true)} 
            className="btn-connect-db" 
            title="Upload new SQLite database and extract relationships"
          >
            <Upload size={13} />
            <span>Connect DB</span>
          </button>
          <button 
            onClick={createNewConversation} 
            className="btn-new-conv"
            title="Start a new analytical session"
          >
            <Plus size={14} />
            <span>New Session</span>
          </button>
        </div>
      </header>

      {/* Main Workspace Layout */}
      <div className="workspace-body">
        {/* Sidebar: Conversation Sessions */}
        <aside className={`workspace-sidebar ${isSidebarCollapsed ? 'collapsed' : ''}`}>
          <div className="sidebar-header">
            <div className="sidebar-title-row">
              <h3>Analysis Sessions</h3>
              <span className="conv-count">{conversations.length}</span>
            </div>
            <div className="sidebar-search-wrap">
              <Search size={13} className="sidebar-search-icon" />
              <input 
                type="text"
                placeholder="Filter sessions..."
                value={sessionSearch}
                onChange={(e) => setSessionSearch(e.target.value)}
                className="sidebar-search-input"
              />
              {sessionSearch && (
                <button onClick={() => setSessionSearch('')} className="sidebar-search-clear">
                  <X size={11} />
                </button>
              )}
            </div>
          </div>
          <div id="conv-list" className="conversations-scroll">
            {filteredConversations.map((c) => (
              <div
                key={c.conversation_id}
                onClick={() => selectConversation(c.conversation_id)}
                className={`conv-item ${c.conversation_id === currentConversationId ? 'active' : ''}`}
              >
                <div className="conv-indicator" />
                <div className="conv-content">
                  <div className="conv-title">{c.title || 'Analysis Session'}</div>
                  <div className="conv-meta">
                    <span className="conv-tenant">{c.tenant_id}</span>
                    <span className="conv-time">
                      {new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </div>
              </div>
            ))}
            {filteredConversations.length === 0 && (
              <div className="no-sessions-found">
                <span>No matching sessions</span>
              </div>
            )}
          </div>
        </aside>

        {/* Central Analytical Viewport */}
        <main className={`workspace-main ${isSidebarCollapsed ? 'sidebar-hidden' : ''}`}>
          {/* Central Content Viewport */}
          <div className="viewport-content">
            {currentView === 'dashboard' && (
              <StudioDashboard
                activeTurn={activeTurn}
                answer={activeAnswer}
                followUps={followUps}
                onSelectChoice={handleSelectChoice}
                onSubmitQuestion={handleSubmitQuestion}
                onNavigateToCatalog={() => setCurrentView('catalog')}
                sourceCatalog={sourceCatalog}
                selectedSource={selectedSource}
              />
            )}

            {currentView === 'stream' && (
              <ConversationStream
                turns={turns}
                answers={answers}
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

          {/* Bottom Chatbot Writing Section (Normal Chatbot Input Bar) */}
          <footer className="workspace-footer">
            <div className="chat-input-container">
              {/* Quick Action Suggestion Chips */}
              <div className="command-chips-row">
                <span className="chips-label">Quick Inquiries:</span>
                <button 
                  type="button"
                  onClick={() => handleSubmitQuestion('What was total sales revenue in 2024?')}
                  className="chip-btn"
                >
                  <TrendingUp size={11} className="chip-icon" />
                  <span>Total Revenue 2024</span>
                </button>
                <button 
                  type="button"
                  onClick={() => handleSubmitQuestion('Forecast annual revenue for the next two years.')}
                  className="chip-btn"
                >
                  <Zap size={11} className="chip-icon" />
                  <span>Forecast 2 Years</span>
                </button>
                <button 
                  type="button"
                  onClick={() => handleSubmitQuestion('Compare revenue between 2023 and 2024.')}
                  className="chip-btn"
                >
                  <GitCompare size={11} className="chip-icon" />
                  <span>Compare 2023 vs 2024</span>
                </button>
                <button 
                  type="button"
                  onClick={() => handleSubmitQuestion('Detect anomalies and outliers in monthly sales.')}
                  className="chip-btn"
                >
                  <SlidersHorizontal size={11} className="chip-icon" />
                  <span>Anomaly Check</span>
                </button>
              </div>

              {/* Chatbot Prompt Form */}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSubmitQuestion();
                }}
                className="query-input-form"
              >
                <div className="input-field-wrapper">
                  <input
                    ref={inputRef}
                    id="question-input"
                    type="text"
                    placeholder={`Ask an analytical question on ${selectedSource || 'data'} (e.g. "What was total sales revenue in 2024?")...`}
                    value={questionText}
                    onChange={(e) => setQuestionText(e.target.value)}
                    disabled={isSubmitting}
                    className="main-query-input"
                  />
                  {questionText && (
                    <button 
                      type="button" 
                      onClick={() => setQuestionText('')} 
                      className="btn-clear-query"
                      title="Clear input"
                    >
                      <X size={14} />
                    </button>
                  )}
                  <div className="keyboard-shortcut-hint" title="Press Ctrl+K or Cmd+K to focus">
                    <Command size={10} />
                    <span>K</span>
                  </div>
                </div>

                <button
                  id="btn-submit"
                  type="submit"
                  disabled={isSubmitting || !questionText.trim()}
                  className="btn-submit-query"
                  title="Send message (Enter)"
                >
                  <Send size={15} />
                  <span>Send</span>
                </button>
              </form>

              <div className="command-footer-guarantee">
                <span className="guarantee-dot" />
                <span>Strict Parameterized SQL AST • Zero Data Leakage • Cryptographically Sealed</span>
              </div>
            </div>
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

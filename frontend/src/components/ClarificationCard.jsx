import React from 'react';
import { HelpCircle, ArrowRight, Sparkles, CheckCircle2 } from 'lucide-react';

export default function ClarificationCard({ turn, onSelectChoice }) {
  if (!turn || turn.status !== 'AWAITING_CLARIFICATION') {
    return null;
  }

  let clarif = turn.clarification_request;
  if (!clarif && turn.clarification_request_json) {
    try {
      clarif = typeof turn.clarification_request_json === 'string'
        ? JSON.parse(turn.clarification_request_json)
        : turn.clarification_request_json;
    } catch (err) {
      clarif = null;
    }
  }

  if (!clarif) return null;

  const promptText = clarif.prompt || clarif.question_prompt || 'Disambiguation Required — Please select an option below:';
  const ambiguityCode = clarif.ambiguity_code || 'SPECIFICATION_REQUIRED';
  const options = clarif.options || [];

  return (
    <div className="clarification-card">
      <div className="clarification-header">
        <div className="clarification-icon-wrap">
          <HelpCircle size={22} color="var(--accent-amber)" />
        </div>
        <div className="clarification-text-block">
          <div className="clarification-badge-row">
            <span className="clarification-tag">
              <Sparkles size={11} />
              <span>Zero-Hallucination Disambiguation</span>
            </span>
            <span className="ambiguity-code-badge">{ambiguityCode.replace(/_/g, ' ')}</span>
          </div>
          <h3 className="clarification-prompt">{promptText}</h3>
          <p className="clarification-subtext">
            Talk2Data avoids guessing intent. Select your intended analytical target below to compile deterministic, verified SQL:
          </p>
        </div>
      </div>

      <div className="clarification-options-grid">
        {options.map((opt, idx) => (
          <button
            key={opt.choice_id || idx}
            onClick={() => onSelectChoice(turn.turn_id, opt.choice_id)}
            className="clarification-choice-btn"
          >
            <div className="choice-indicator-wrap">
              <div className="choice-radio-dot" />
            </div>
            <div className="choice-content-wrap">
              <span className="choice-title">{opt.label || opt.choice_id}</span>
              {opt.target_type && (
                <span className="choice-type-pill">{opt.target_type}</span>
              )}
            </div>
            <div className="choice-arrow-wrap">
              <ArrowRight size={15} />
            </div>
          </button>
        ))}
      </div>

      <div className="clarification-footer-note">
        <CheckCircle2 size={13} color="var(--accent-emerald)" />
        <span>Selected choice will immediately compile against approved release <code>{turn.source_id || 'catalog'}</code></span>
      </div>
    </div>
  );
}

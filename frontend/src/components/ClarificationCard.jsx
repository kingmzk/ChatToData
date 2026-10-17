import React from 'react';
import { HelpCircle } from 'lucide-react';

export default function ClarificationCard({ turn, onSelectChoice }) {
  if (!turn || turn.status !== 'AWAITING_CLARIFICATION' || !turn.clarification_request_json) {
    return null;
  }

  let clarif = null;
  try {
    clarif = JSON.parse(turn.clarification_request_json);
  } catch (err) {
    return null;
  }

  if (!clarif) return null;

  return (
    <div className="clarification-card">
      <div className="clarification-header">
        <HelpCircle size={18} color="var(--accent-amber)" />
        <span className="clarification-title">
          {clarif.prompt || 'Disambiguation Required — Please select an option below:'}
        </span>
      </div>
      <div className="clarification-options">
        {clarif.options && clarif.options.map((opt) => (
          <button
            key={opt.choice_id}
            onClick={() => onSelectChoice(turn.turn_id, opt.choice_id)}
            className="clarification-btn"
          >
            <span className="choice-indicator">●</span>
            <span className="choice-label">{opt.label || opt.choice_id}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

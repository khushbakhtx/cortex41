import React, { useEffect, useRef } from 'react';

const TYPE_LABELS = {
  thinking:    'Thinking',
  plan:        'Plan',
  subtask:     'Sub-task',
  subtask_done:'Sub-task ✓',
  action:      'Action',
  success:     'Done ✓',
  error:       'Error',
  info:        'Info',
  stats:       'Stats',
  audio_response: 'Voice',
};

function fmt(ts) {
  return ts.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function ActionDetail({ data }) {
  if (!data) return null;
  const parts = [];
  if (data.type)       parts.push(data.type);
  if (data.x != null && data.y != null) parts.push(`(${data.x}, ${data.y})`);
  if (data.confidence) parts.push(`${Math.round(data.confidence * 100)}%`);
  if (data.goal_progress) parts.push(`[${data.goal_progress}]`);
  return parts.length ? (
    <span className="mf" style={{ fontSize: 9, color: 'inherit', opacity: 0.55, marginLeft: 6, letterSpacing: '0.07em' }}>
      · {parts.join(' · ')}
    </span>
  ) : null;
}

function PlanDetail({ data }) {
  if (!data?.sub_tasks?.length) return null;
  return (
    <div style={{ marginTop: 6 }}>
      {data.sub_tasks.map(t => (
        <div key={t.id} style={{ display: 'flex', gap: 8, marginTop: 3 }}>
          <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)', width: 14, flexShrink: 0 }}>{t.id}.</span>
          <span style={{ fontSize: 11, color: 'var(--text-2)', lineHeight: 1.4 }}>{t.description}</span>
        </div>
      ))}
    </div>
  );
}

function StatsDetail({ data }) {
  if (!data) return null;
  const cache  = data.cache  || {};
  const router = data.router || {};
  return (
    <div style={{ display: 'flex', gap: 16, marginTop: 4, flexWrap: 'wrap' }}>
      {data.total_steps != null && (
        <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)' }}>
          steps: {data.total_steps}
        </span>
      )}
      {cache.hit_rate != null && (
        <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)' }}>
          cache: {Math.round(cache.hit_rate * 100)}%
        </span>
      )}
      {router.flash_pct != null && (
        <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)' }}>
          flash: {Math.round(router.flash_pct * 100)}%
        </span>
      )}
    </div>
  );
}

export default function AgentFeed({ events }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div className="panel-section-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span className="label">Agent Feed</span>
        <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)' }}>{events.length}</span>
      </div>

      <div className="feed-list">
        {events.length === 0 && (
          <div style={{ padding: '32px 18px', textAlign: 'center' }}>
            <span className="label" style={{ lineHeight: 1.8 }}>
              Events will<br />stream here
            </span>
          </div>
        )}

        {events.map((ev) => (
          <div key={ev.id} className={`feed-item feed-type-${ev.type}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span className="feed-item-type">
                {TYPE_LABELS[ev.type] || ev.type}
              </span>
              <span className="feed-item-time">{fmt(ev.timestamp)}</span>
            </div>
            {ev.message && (
              <div className="feed-item-msg">
                {ev.message}
                {ev.type === 'action' && <ActionDetail data={ev.data} />}
              </div>
            )}
            {ev.type === 'plan' && <PlanDetail data={ev.data} />}
            {ev.type === 'stats' && <StatsDetail data={ev.data} />}
          </div>
        ))}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

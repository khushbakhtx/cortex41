import React from 'react';

const STATUS_ICON = {
  done:        '✓',
  in_progress: '↻',
  failed:      '✗',
  pending:     '○',
};

const STATUS_META = {
  done:        'complete',
  in_progress: 'in progress',
  failed:      'failed',
  pending:     'pending',
};

export default function PlanPanel({ plan, activeSubTaskId }) {
  if (!plan || !plan.sub_tasks?.length) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div className="panel-section-header">
          <span className="label">Plan</span>
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 24, gap: 12 }}>
          <span className="ef" style={{ fontSize: 40, fontWeight: 300, color: '#e8e8e8', fontStyle: 'italic' }}>—</span>
          <span className="label" style={{ textAlign: 'center', lineHeight: 1.7 }}>
            Send a goal<br />to generate a plan
          </span>
        </div>
      </div>
    );
  }

  const tasks = plan.sub_tasks;
  const total = tasks.length;
  const done  = tasks.filter(t => t.status === 'done').length;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div className="panel-section-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span className="label">Plan</span>
        <span className="mf" style={{ fontSize: 9, color: '#bbb', letterSpacing: '0.08em' }}>
          {done}/{total}
        </span>
      </div>

      {/* Thin progress bar */}
      <div style={{ height: 2, background: '#f0f0f0', flexShrink: 0 }}>
        <div
          style={{
            height: '100%',
            background: '#0a0a0a',
            width: `${total ? (done / total) * 100 : 0}%`,
            transition: 'width 0.6s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        />
      </div>

      <div className="plan-list">
        {tasks.map((task, i) => {
          const isActive = task.id === activeSubTaskId || task.status === 'in_progress';
          const isDone   = task.status === 'done';
          const isFailed = task.status === 'failed';

          let itemClass = 'task-item';
          if (isActive) itemClass += ' active';
          else if (isDone) itemClass += ' done';

          const icon = STATUS_ICON[task.status] || STATUS_ICON.pending;

          return (
            <div
              key={task.id}
              className={itemClass}
              style={{ animationDelay: `${i * 0.05}s` }}
            >
              <div className="task-num">{task.id}</div>
              <div className="task-body">
                <div className="task-desc">{task.description}</div>
                <div className="task-meta">
                  {task.success_criteria
                    ? `→ ${task.success_criteria}`
                    : STATUS_META[task.status] || 'pending'}
                </div>
              </div>
              <div
                className="task-status-icon"
                style={{
                  color: isActive
                    ? 'rgba(255,255,255,0.6)'
                    : isDone
                    ? '#2d6a4f'
                    : isFailed
                    ? '#8b1a1a'
                    : '#ccc',
                  animation: isActive ? 'spin 1.5s linear infinite' : 'none',
                  display: 'inline-block',
                }}
              >
                {icon}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

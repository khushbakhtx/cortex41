import React from 'react';

function StatBox({ value, label, delay = 0 }) {
  return (
    <div className="stat-box fu" style={{ animationDelay: `${delay}s` }}>
      <div className="stat-value ef">{value ?? '—'}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export default function StatsPanel({ stats }) {
  const cache  = stats?.cache  || {};
  const router = stats?.router || {};

  const flashPct = router.flash_pct   != null ? `${Math.round(router.flash_pct)}%`   : '—';
  const cachePct = cache.hit_rate_pct != null ? `${Math.round(cache.hit_rate_pct)}%` : '—';
  const steps    = stats?.total_steps ?? '—';
  const proCount = router.pro_count != null ? router.pro_count : '—';

  return (
    <div style={{ borderTop: '1px solid var(--border)', flexShrink: 0 }}>
      <div style={{ padding: '12px 16px 8px' }}>
        <span className="label">Performance</span>
      </div>
      <div className="stats-grid">
        <StatBox value={steps}    label="Steps"     delay={0} />
        <StatBox value={cachePct} label="Cache hit"  delay={0.05} />
        <StatBox value={flashPct} label="Flash use"  delay={0.1} />
        <StatBox value={proCount} label="Pro calls"  delay={0.15} />
      </div>
    </div>
  );
}

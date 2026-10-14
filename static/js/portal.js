/* ==========================================================================
   Portal — Announcements · Documents · Awards · Holidays
   v3 — premium polish
   ========================================================================== */

/* ==========================================================================
   LAYOUT PRIMITIVES
   ========================================================================== */
.portal-page {
  max-width: 1180px;
  margin: 0 auto;
  padding-bottom: 40px;
}
.portal-page-narrow { max-width: 800px; }

/* ==========================================================================
   HERO
   ========================================================================== */
.portal-hero {
  display: flex; align-items: center; justify-content: space-between;
  gap: 20px; flex-wrap: wrap;
  padding: 22px 26px;
  margin-bottom: 20px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 16px;
  position: relative;
  overflow: hidden;
}
.portal-hero::before {
  content: ""; position: absolute; inset: 0;
  background: linear-gradient(135deg,
    rgba(31, 107, 62, 0.06) 0%,
    rgba(31, 107, 62, 0) 45%);
  pointer-events: none;
}
.portal-hero-awards::before {
  background: linear-gradient(135deg,
    rgba(217, 119, 6, 0.08) 0%,
    rgba(217, 119, 6, 0) 45%);
}
.portal-hero-holidays::before {
  background: linear-gradient(135deg,
    rgba(3, 105, 161, 0.08) 0%,
    rgba(3, 105, 161, 0) 45%);
}

.portal-hero-inner {
  display: flex; align-items: center; gap: 16px;
  position: relative; min-width: 0;
}
.portal-hero-icon {
  width: 48px; height: 48px; border-radius: 14px;
  background: rgba(31, 107, 62, 0.12);
  color: var(--brand);
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.portal-hero-awards .portal-hero-icon {
  background: rgba(217, 119, 6, 0.14); color: #b45309;
}
.portal-hero-holidays .portal-hero-icon {
  background: rgba(3, 105, 161, 0.14); color: #0369a1;
}

.portal-hero h1 {
  margin: 0;
  font-size: 22px; letter-spacing: -0.015em;
  color: var(--fg);
}
.portal-hero p {
  margin: 4px 0 0;
  color: var(--fg-subtle); font-size: 13.5px;
}
.portal-hero .btn { position: relative; }

/* ==========================================================================
   BACK LINK
   ========================================================================== */
.portal-back {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12.5px; font-weight: 600;
  color: var(--fg-subtle); text-decoration: none;
  margin-bottom: 14px;
  padding: 4px 0;
}
.portal-back:hover { color: var(--brand); }

/* ==========================================================================
   FILTER BAR (shared)
   ========================================================================== */
.portal-filter-bar {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr auto;
  gap: 12px;
  align-items: end;
  padding: 16px;
  margin-bottom: 20px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 14px;
}
.portal-filter-bar-compact {
  grid-template-columns: 1fr auto;
}

.portal-filter-field {
  display: flex; flex-direction: column; gap: 6px;
  min-width: 0;
}
.portal-filter-field label {
  font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.07em;
  color: var(--fg-subtle);
  margin: 0;
}
.portal-filter-field input,
.portal-filter-field select {
  width: 100%;
  height: 40px;
  padding: 0 12px;
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 9px;
  font: inherit; font-size: 13.5px;
  outline: 0;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.portal-filter-field input::placeholder { color: var(--fg-subtle); }
.portal-filter-field input:focus,
.portal-filter-field select:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px rgba(31, 107, 62, 0.15);
}

.portal-filter-status {
  display: flex; align-items: center;
  padding-bottom: 8px;
}
.portal-loading {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--fg-subtle);
}

.htmx-indicator { opacity: 0; transition: opacity 0.2s ease; pointer-events: none; }
.htmx-request .htmx-indicator,
.htmx-request.htmx-indicator { opacity: 1; pointer-events: auto; }

/* ==========================================================================
   AWARD CARDS
   ========================================================================== */
.award-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.award-card {
  display: flex; flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 16px;
  overflow: hidden;
  text-decoration: none; color: inherit;
  position: relative;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.award-card:hover {
  transform: translateY(-3px);
  border-color: var(--brand);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.08);
}
.award-card.kind-service_anniversary { border-top: 3px solid #d97706; }
.award-card.kind-promotion           { border-top: 3px solid #0369a1; }
.award-card.kind-retirement          { border-top: 3px solid #7e22ce; }
.award-card.kind-certification       { border-top: 3px solid #059669; }
.award-card.is-featured {
  background: linear-gradient(180deg,
    rgba(217, 119, 6, 0.04) 0%,
    var(--bg-card) 40%);
}

.award-card-head {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 16px 18px 12px;
  position: relative;
}
.award-thumb {
  width: 56px; height: 56px; border-radius: 14px;
  background: var(--bg-subtle);
  display: flex; align-items: center; justify-content: center;
  overflow: hidden; flex-shrink: 0;
  color: var(--brand);
}
.award-thumb img { width: 100%; height: 100%; object-fit: cover; }
.award-thumb-placeholder {
  background: linear-gradient(135deg,
    rgba(31, 107, 62, 0.10) 0%,
    rgba(31, 107, 62, 0.04) 100%);
}

.award-card-tags {
  display: flex; flex-direction: column; gap: 5px;
  align-items: flex-start;
  min-width: 0; flex: 1;
}
.award-kind {
  font-size: 10.5px; font-weight: 800;
  text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--fg-subtle);
}
.award-cat {
  display: inline-flex; align-items: center;
  padding: 3px 9px; border-radius: 999px;
  background: color-mix(in srgb, var(--award-accent) 14%, transparent);
  color: var(--award-accent);
  font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.04em;
}

.award-featured {
  position: absolute; top: 14px; right: 14px;
  width: 26px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%;
  background: #f59e0b; color: #fff;
  box-shadow: 0 2px 8px rgba(217, 119, 6, 0.35);
}

.award-card-body {
  padding: 4px 18px 14px;
  flex: 1;
}
.award-card h3 {
  margin: 0 0 10px;
  font-size: 15.5px; font-weight: 700;
  color: var(--fg);
  letter-spacing: -0.01em;
  line-height: 1.3;
}
.award-recipient {
  display: flex; align-items: center; gap: 9px;
  margin-bottom: 10px;
}
.award-recipient-mini {
  width: 30px; height: 30px; border-radius: 50%;
  background: var(--bg-subtle);
  color: var(--brand);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
  overflow: hidden; flex-shrink: 0;
}
.award-recipient-mini img { width: 100%; height: 100%; object-fit: cover; }
.award-recipient-info { min-width: 0; }
.award-recipient-info strong {
  display: block; font-size: 13px; color: var(--fg);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.award-recipient-info span {
  display: block; font-size: 11.5px; color: var(--fg-subtle);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.award-desc {
  font-size: 12.5px; line-height: 1.55;
  color: var(--fg-muted);
  margin: 0;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}

.award-card-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px;
  padding: 11px 18px;
  border-top: 1px solid var(--border);
  font-size: 11.5px;
  color: var(--fg-subtle);
  background: var(--bg-subtle);
}
.award-date { font-variant-numeric: tabular-nums; }
.award-years {
  color: var(--brand); font-weight: 700;
  padding: 2px 8px; border-radius: 999px;
  background: rgba(31, 107, 62, 0.10);
}

/* ==========================================================================
   AWARD DETAIL
   ========================================================================== */
.award-detail {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 18px;
  overflow: hidden;
}
.award-detail.kind-service_anniversary { border-top: 4px solid #d97706; }
.award-detail.kind-promotion           { border-top: 4px solid #0369a1; }
.award-detail.kind-retirement          { border-top: 4px solid #7e22ce; }
.award-detail.kind-certification       { border-top: 4px solid #059669; }

.award-detail-banner {
  aspect-ratio: 21 / 9;
  background: var(--bg-subtle);
  overflow: hidden;
  position: relative;
}
.award-detail-banner img { width: 100%; height: 100%; object-fit: cover; display: block; }
.award-detail-banner-placeholder {
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg,
    rgba(217, 119, 6, 0.10) 0%,
    rgba(217, 119, 6, 0.02) 100%);
  color: #b45309;
}

.award-detail-body { padding: 26px 30px 30px; }

.award-detail-badges {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 14px;
}
.award-kind-badge {
  display: inline-flex; align-items: center;
  padding: 4px 12px; border-radius: 999px;
  background: rgba(31, 107, 62, 0.12);
  color: var(--brand);
  font-size: 11px; font-weight: 800;
  text-transform: uppercase; letter-spacing: 0.05em;
}
.award-featured-badge {
  display: inline-flex; align-items: center;
  padding: 4px 12px; border-radius: 999px;
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
  font-size: 11px; font-weight: 800;
  text-transform: uppercase; letter-spacing: 0.05em;
}

.award-detail h1 {
  font-size: 28px; letter-spacing: -0.02em;
  line-height: 1.2;
  margin: 0 0 20px;
  color: var(--fg);
}

/* Recipient card */
.award-recipient-card {
  display: flex; align-items: center; gap: 16px;
  padding: 16px 18px;
  background: var(--bg-subtle);
  border-radius: 14px;
  margin-bottom: 18px;
}
.award-recipient-avatar-lg {
  width: 60px; height: 60px; border-radius: 50%;
  background: var(--bg-card);
  color: var(--brand);
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 18px;
  overflow: hidden; flex-shrink: 0;
  border: 2px solid rgba(31, 107, 62, 0.15);
}
.award-recipient-avatar-lg img { width: 100%; height: 100%; object-fit: cover; }

.award-recipient-details { flex: 1; min-width: 0; }
.award-recipient-label {
  font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--fg-subtle);
  margin-bottom: 3px;
}
.award-recipient-name {
  display: block;
  font-size: 17px; font-weight: 700;
  color: var(--fg); text-decoration: none;
  letter-spacing: -0.01em;
}
.award-recipient-name:hover { color: var(--brand); }
.award-recipient-role {
  font-size: 12.5px; color: var(--fg-subtle);
  margin-top: 2px;
}

.award-years-badge {
  display: flex; flex-direction: column; align-items: center;
  padding: 10px 16px;
  background: rgba(217, 119, 6, 0.12);
  border-radius: 12px;
  flex-shrink: 0;
}
.award-years-num {
  font-size: 24px; font-weight: 800; line-height: 1;
  color: #b45309;
  font-variant-numeric: tabular-nums;
}
.award-years-lbl {
  font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.06em;
  color: #b45309; opacity: 0.8;
  margin-top: 3px;
}

/* Meta row */
.award-meta-row {
  display: flex; gap: 20px; flex-wrap: wrap;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 22px;
}
.award-meta-item {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: 12.5px; color: var(--fg-muted);
}
.award-meta-item svg { opacity: 0.65; color: var(--fg-subtle); }

.award-description {
  font-size: 15px; line-height: 1.7;
  color: var(--fg);
  margin: 0 0 22px;
  white-space: pre-line;
}

.award-certificate { margin-top: 22px; }
.award-certificate h2 {
  font-size: 11.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--fg-subtle);
  margin: 0 0 10px;
}
.award-certificate-link {
  display: inline-flex; align-items: center; gap: 12px;
  padding: 12px 18px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--fg);
  font-weight: 600; font-size: 13.5px;
  text-decoration: none;
  transition: all 0.15s ease;
}
.award-certificate-link:hover {
  background: var(--bg);
  border-color: var(--brand);
  color: var(--brand);
  transform: translateX(2px);
}
.award-certificate-link svg:first-child { color: var(--brand); }
.award-certificate-arrow { margin-left: 4px; opacity: 0.6; }

.award-detail-actions {
  display: flex; gap: 10px; flex-wrap: wrap;
  margin-top: 26px; padding-top: 20px;
  border-top: 1px solid var(--border);
}

/* ==========================================================================
   HOLIDAYS
   ========================================================================== */
.holidays-layout {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
  gap: 22px;
  align-items: start;
}
@media (max-width: 960px) {
  .holidays-layout { grid-template-columns: 1fr; }
  .holidays-side { order: -1; }
}

.holiday-list { display: grid; gap: 10px; }

.holiday-row {
  display: grid;
  grid-template-columns: 68px 1fr auto;
  gap: 16px; align-items: center;
  padding: 14px 18px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.holiday-row:hover {
  border-color: rgba(31, 107, 62, 0.35);
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05);
}
.holiday-row.is-today {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px rgba(31, 107, 62, 0.12);
}
.holiday-row.kind-public       { border-left: 3px solid #0369a1; }
.holiday-row.kind-religious    { border-left: 3px solid #7e22ce; }
.holiday-row.kind-departmental { border-left: 3px solid #b45309; }

.holiday-date {
  display: flex; flex-direction: column; align-items: center;
  padding: 8px 0;
  border-right: 1px solid var(--border);
  padding-right: 16px;
  flex-shrink: 0;
}
.holiday-month {
  font-size: 10px; font-weight: 800;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--fg-subtle);
}
.holiday-day {
  font-size: 26px; font-weight: 800;
  line-height: 1;
  color: var(--brand);
  font-variant-numeric: tabular-nums;
  margin: 3px 0;
}
.holiday-weekday {
  font-size: 10.5px; font-weight: 600;
  color: var(--fg-subtle);
}

.holiday-body { min-width: 0; }
.holiday-name-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.holiday-name {
  font-size: 14.5px; font-weight: 700;
  color: var(--fg); letter-spacing: -0.005em;
}
.holiday-today-badge {
  display: inline-flex; align-items: center;
  padding: 2px 9px; border-radius: 999px;
  background: var(--brand); color: #fff;
  font-size: 10px; font-weight: 800;
  text-transform: uppercase; letter-spacing: 0.06em;
}
.holiday-meta {
  font-size: 12px; color: var(--fg-subtle);
  margin-top: 3px;
}
.holiday-kind { color: var(--fg-muted); font-weight: 600; }
.holiday-meta-sep { opacity: 0.5; margin: 0 2px; }
.holiday-desc { font-size: 12.5px; line-height: 1.5; margin: 6px 0 0; }

.holiday-actions { display: flex; gap: 6px; flex-shrink: 0; }

/* ==========================================================================
   WIDGETS (dashboard + absence)
   ========================================================================== */
.portal-widget {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 18px;
}
.portal-widget-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.portal-widget-head h2 { margin: 0; font-size: 14.5px; color: var(--fg); letter-spacing: -0.005em; }
.portal-widget-sub { font-size: 11.5px; color: var(--fg-subtle); font-variant-numeric: tabular-nums; }

.portal-widget-list { display: grid; gap: 2px; }
.portal-widget-item {
  display: flex; gap: 11px;
  padding: 9px 10px;
  border-radius: 10px;
  text-decoration: none; color: inherit;
  transition: background 0.12s ease;
}
.portal-widget-item:hover { background: var(--bg-subtle); }

.portal-widget-thumb {
  width: 40px; height: 40px; flex-shrink: 0;
  border-radius: 10px;
  background: var(--bg-subtle);
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700;
  color: var(--brand);
  overflow: hidden;
}
.portal-widget-thumb img { width: 100%; height: 100%; object-fit: cover; }
.portal-widget-thumb-absent {
  background: rgba(220, 38, 38, 0.10);
  color: #b91c1c;
}

.portal-widget-body { min-width: 0; flex: 1; }
.portal-widget-title {
  font-size: 13px; font-weight: 600;
  color: var(--fg);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.portal-widget-time {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11.5px; color: var(--fg-subtle);
  margin-top: 2px;
}
.portal-widget-time .absence-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #dc2626; display: inline-block;
}
.portal-widget-foot {
  margin-top: 10px; padding-top: 10px;
  border-top: 1px solid var(--border);
  text-align: center; font-size: 11.5px;
}
.portal-widget-loading {
  display: flex; align-items: center; justify-content: center;
  padding: 30px;
}
.portal-widget-empty-state {
  text-align: center; padding: 22px 12px 10px;
}
.portal-widget-empty-icon {
  width: 44px; height: 44px;
  margin: 0 auto 10px;
  border-radius: 50%;
  background: rgba(31, 107, 62, 0.10);
  color: var(--brand);
  display: flex; align-items: center; justify-content: center;
}
.portal-widget-empty-title { font-size: 13.5px; font-weight: 700; color: var(--fg); }
.portal-widget-empty-sub { font-size: 12px; color: var(--fg-subtle); margin-top: 3px; }

/* ==========================================================================
   FORM
   ========================================================================== */
.portal-form .portal-section {
  padding: 22px 24px;
  margin-bottom: 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 14px;
}
.portal-section-head { margin-bottom: 18px; }
.portal-section-head h3 {
  margin: 0 0 4px;
  font-size: 14px; font-weight: 700;
  color: var(--fg); letter-spacing: -0.005em;
}
.portal-section-head p {
  margin: 0;
  font-size: 12.5px; color: var(--fg-subtle);
}

.portal-field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-bottom: 14px;
}
.portal-field-grid > .portal-field { margin-bottom: 0; }

.portal-field {
  display: flex; flex-direction: column; gap: 6px;
  margin-bottom: 14px;
  min-width: 0;
}
.portal-field label {
  font-size: 12.5px; font-weight: 600;
  color: var(--fg-muted);
  margin: 0;
}
.portal-field label .muted {
  font-weight: 400; font-size: 11.5px;
}
.portal-field input,
.portal-field select,
.portal-field textarea {
  width: 100%;
  padding: 10px 12px;
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 9px;
  font: inherit; font-size: 13.5px;
  outline: 0;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.portal-field input:focus,
.portal-field select:focus,
.portal-field textarea:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px rgba(31, 107, 62, 0.15);
}
.portal-field textarea { resize: vertical; min-height: 110px; }
.portal-field-check { justify-content: end; }

.portal-check {
  display: inline-flex; align-items: center; gap: 9px;
  font-size: 13.5px; color: var(--fg);
  cursor: pointer;
  padding: 6px 0;
}
.portal-check input { width: auto; }

.portal-form-actions {
  display: flex; gap: 10px; flex-wrap: wrap;
  padding-top: 6px;
}

/* ==========================================================================
   EMPTY STATES
   ========================================================================== */
.portal-empty {
  padding: 60px 24px;
  text-align: center;
  background: var(--bg-card);
  border: 1px dashed var(--border);
  border-radius: 14px;
}
.portal-empty-icon {
  width: 64px; height: 64px;
  margin: 0 auto 14px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%;
  background: var(--bg-subtle);
  color: var(--fg-subtle);
}
.portal-empty h3 { margin: 0 0 6px; font-size: 16px; color: var(--fg); }
.portal-empty p { margin: 0; color: var(--fg-subtle); font-size: 13.5px; }

/* ==========================================================================
   CONFIRM DIALOG
   ========================================================================== */
.portal-confirm {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 32px 30px;
  text-align: center;
}
.portal-confirm-icon {
  width: 56px; height: 56px;
  margin: 0 auto 16px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
}
.portal-confirm-icon-danger {
  background: rgba(220, 38, 38, 0.10);
  color: var(--danger);
}
.portal-confirm h2 {
  margin: 0 0 6px;
  font-size: 18px; color: var(--fg);
  letter-spacing: -0.01em;
}
.portal-confirm-sub {
  font-size: 14px; font-weight: 600;
  color: var(--fg-muted);
  margin: 0 0 12px;
}
.portal-confirm p.muted {
  max-width: 420px;
  margin: 0 auto 22px;
  line-height: 1.6;
  font-size: 13px;
}
.portal-confirm-actions {
  display: flex; gap: 10px; justify-content: center;
  flex-wrap: wrap;
}

/* ==========================================================================
   MOBILE
   ========================================================================== */
@media (max-width: 720px) {
  .portal-hero { padding: 18px; }
  .portal-hero h1 { font-size: 19px; }
  .portal-hero-icon { width: 42px; height: 42px; border-radius: 12px; }

  .portal-filter-bar {
    grid-template-columns: 1fr;
    gap: 10px;
    padding: 14px;
  }
  .portal-filter-bar-compact { grid-template-columns: 1fr auto; }

  .award-grid { grid-template-columns: 1fr; }
  .award-detail-body { padding: 22px 20px 24px; }
  .award-detail h1 { font-size: 22px; }
  .award-recipient-card { flex-wrap: wrap; }
  .award-years-badge { margin-left: auto; }

  .holiday-row {
    grid-template-columns: 60px 1fr;
    gap: 12px;
  }
  .holiday-actions { grid-column: 2; justify-content: flex-end; }

  .portal-confirm { padding: 24px 20px; }
}

/* ==========================================================================
   MULTI-PICKER  (unchanged from v2 — kept here for one-file convenience)
   ========================================================================== */
.mp { position: relative; width: 100%; font-size: 13.5px; }
.mp-control {
  display: flex; align-items: center; gap: 6px;
  min-height: 42px; padding: 6px 10px;
  background: var(--bg); color: var(--fg);
  border: 1px solid var(--border); border-radius: 9px;
  cursor: text;
}
.mp-control:focus-within { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(31,107,62,0.15); }
.mp-chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; flex: 1; min-width: 0; }
.mp-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 4px 2px 8px; border-radius: 999px;
  background: rgba(31,107,62,0.10); color: var(--brand);
  font-size: 12px; font-weight: 600;
}
.mp-chip-label { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mp-chip-x { background: transparent; border: 0; color: var(--brand); cursor: pointer; font-size: 15px; line-height: 1; padding: 0 4px; }
.mp-chip-x:hover { color: var(--danger); }
.mp-search { border: 0; outline: 0; background: transparent; font: inherit; color: var(--fg); min-width: 100px; flex: 1; }
.mp-search::placeholder { color: var(--fg-subtle); }
.mp-caret { background: transparent; border: 0; color: var(--fg-subtle); cursor: pointer; }
.mp-panel {
  position: absolute; z-index: 40; left: 0; right: 0; top: calc(100% + 4px);
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 10px; box-shadow: 0 12px 32px rgba(15,23,42,0.14);
  max-height: 280px; overflow: hidden;
  display: flex; flex-direction: column;
}
.mp-panel-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; border-bottom: 1px solid var(--border);
  font-size: 11.5px; color: var(--fg-subtle);
  background: var(--bg-subtle);
}
.mp-clear { background: transparent; border: 0; color: var(--brand); font-weight: 600; cursor: pointer; }
.mp-list { list-style: none; padding: 4px; margin: 0; overflow-y: auto; flex: 1; }
.mp-item label {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: 6px; cursor: pointer;
  font-weight: 400; color: var(--fg);
}
.mp-item label:hover { background: var(--bg-subtle); }
.mp-checkbox { display: none; }
.mp-item:has(.mp-checkbox:checked) { background: rgba(31,107,62,0.08); }
.mp-item:has(.mp-checkbox:checked) .mp-tick { color: var(--brand); }
.mp-tick { color: transparent; margin-left: auto; font-weight: 700; }
.mp-label { flex: 1; min-width: 0; }
@media (max-width: 720px) { .mp-panel { max-height: 60vh; } }
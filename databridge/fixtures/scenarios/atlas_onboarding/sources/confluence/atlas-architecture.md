Atlas architecture
Jordan Doe owns the technical design and coordinates the integration.
Samira Khan's merchant status screen calls Elena Park's onboarding API.
The API consumes verification callbacks and writes an application-state event for Mei Tan's funnel dashboard.
A duplicate callback must not move a completed application back to pending.
Priya Raman owns staging, alert routing and the rollback procedure.
The pilot keeps a human operations review before any merchant is approved.
The integration uses a fictional verification sandbox; no real merchant data or live vendor credentials exist in this fixture.

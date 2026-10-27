-- Bound dashboard reads as audit history grows on the free tier.
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC);

import { useEffect, useState, type FormEvent } from "react";
import "./App.css";

type View = "overview" | "verify" | "alerts";

type PackageClaimsInput = {
  product_name: string;
  active_ingredients: string;
  strength: string;
  dosage_form: string;
  nafdac_reg_number: string;
  manufacturer: string;
  batch_number: string;
  manufacturing_date: string;
  expiry_date: string;
};

type VerificationCheck = {
  field: string;
  package_value: string | null;
  reference_value: string | null;
  outcome: "match" | "mismatch" | "not_checked";
  explanation: string;
  check_scope: "package_vs_registry" | "registry_record_only";
};

type VerificationAlert = {
  alert_id: string;
  title: string;
  product_name: string;
  source_name: string;
  source_url: string;
  published_at: string | null;
  alert_scope: string | null;
  batch_expiry_date: string | null;
  batch_expiry_status: "expired" | "not_expired" | "unknown";
  batch_match_status: "match" | "different" | "not_checked";
  explanation: string;
};

type VerificationResult = {
  status:
    | "no_significant_discrepancy_detected"
    | "verification_concern_detected"
    | "high_risk_discrepancy_detected";
  summary: string;
  matched_record: boolean;
  registry_snapshot_retrieved_at: string | null;
  alerts_snapshot_retrieved_at: string | null;
  checks: VerificationCheck[];
  safety_alerts: VerificationAlert[];
  source_name: string | null;
  source_url: string | null;
  dataset_label: string | null;
  limitations: string[];
};

const emptyClaims: PackageClaimsInput = {
  product_name: "",
  active_ingredients: "",
  strength: "",
  dosage_form: "",
  nafdac_reg_number: "",
  manufacturer: "",
  batch_number: "",
  manufacturing_date: "",
  expiry_date: "",
};

const fieldLabels: Record<string, string> = {
  product_name: "Product name",
  active_ingredients: "Active ingredients",
  strength: "Strength",
  dosage_form: "Dosage form",
  nafdac_reg_number: "NAFDAC registration number",
  manufacturer: "Manufacturer",
  batch_number: "Batch number",
  manufacturing_date: "Manufacturing date",
  expiry_date: "Package expiry date",
  registration_expiry_date: "Registration expiry date",
};

type SafetyAlert = {
  alert_id: string;
  alert_number: string;
  title: string;
  product_name: string;
  product_ingredients: string;
  product_strength: string;
  reported_registration_number: string;
  reference_product_name: string;
  reference_product_strength: string;
  reference_manufacturer: string;
  batch_number: string;
  manufacturing_date: string;
  expiry_date: string;
  alert_scope: string;
  risk_classification: string;
  source_name: string;
  source_url: string;
  published_at: string;
  retrieved_at: string;
  dataset_label: string;
  curation_note: string;
};

function getBatchExpiryStatus(value: string | null): "expired" | "not_expired" | "unknown" {
  if (!value) return "unknown";

  let year: number;
  let month: number;
  let day: number;

  const monthYear = /^(\d{2})\/(\d{4})$/.exec(value);
  const isoDate = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);

  if (monthYear) {
    month = Number(monthYear[1]);
    year = Number(monthYear[2]);
    day = new Date(year, month, 0).getDate();
  } else if (isoDate) {
    year = Number(isoDate[1]);
    month = Number(isoDate[2]);
    day = Number(isoDate[3]);
  } else {
    return "unknown";
  }

  const expiry = new Date(year, month - 1, day);
  if (
    expiry.getFullYear() !== year ||
    expiry.getMonth() !== month - 1 ||
    expiry.getDate() !== day
  ) {
    return "unknown";
  }

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return expiry < today ? "expired" : "not_expired";
}

function App() {
  const [view, setView] = useState<View>("overview");
  const [alerts, setAlerts] = useState<SafetyAlert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(false);
  const [alertsError, setAlertsError] = useState("");
  const [alertReload, setAlertReload] = useState(0);
  const [claims, setClaims] = useState<PackageClaimsInput>(emptyClaims);
  const [verification, setVerification] = useState<VerificationResult | null>(null);
  const [verificationLoading, setVerificationLoading] = useState(false);
  const [verificationError, setVerificationError] = useState("");

  useEffect(() => {
    if (view !== "alerts") return;

    let cancelled = false;

    async function loadAlerts() {
      setAlertsLoading(true);
      setAlertsError("");

      try {
        const response = await fetch("/api/v1/alerts/records");

        if (!response.ok) {
          throw new Error(`The API returned HTTP ${response.status}.`);
        }

        const data: { records: SafetyAlert[] } = await response.json();

        if (!cancelled) {
          setAlerts(data.records);
        }
      } catch (error) {
        if (!cancelled) {
          setAlertsError(
            error instanceof Error
              ? error.message
              : "Unable to load safety alerts.",
          );
        }
      } finally {
        if (!cancelled) {
          setAlertsLoading(false);
        }
      }
    }

    void loadAlerts();

    return () => {
      cancelled = true;
    };
  }, [view, alertReload]);

  async function submitVerification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setVerificationLoading(true);
    setVerificationError("");
    setVerification(null);

    const submittedClaims = Object.fromEntries(
      Object.entries(claims).map(([key, value]) => [
        key,
        value.trim() || null,
      ]),
    );

    try {
      const response = await fetch("/api/v1/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ claims: submittedClaims }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const detail =
          typeof body?.detail === "string"
            ? body.detail
            : `The API returned HTTP ${response.status}.`;
        throw new Error(detail);
      }

      const result: VerificationResult = await response.json();
      setVerification(result);
    } catch (error) {
      setVerificationError(
        error instanceof Error
          ? error.message
          : "Unable to complete the verification request.",
      );
    } finally {
      setVerificationLoading(false);
    }
  }

  function updateClaim(field: keyof PackageClaimsInput, value: string) {
    setClaims((current) => ({ ...current, [field]: value }));
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={() => setView("overview")}>
          <span className="brand-mark">C</span>
          <span>
            <strong>CONFAM</strong>
            <small>Medicine safety intelligence</small>
          </span>
        </a>

        <div className="nav-label">WORKSPACE</div>
        <nav className="navigation" aria-label="Main navigation">
          <button
            className={view === "overview" ? "nav-item active" : "nav-item"}
            onClick={() => setView("overview")}
          >
            <span>◫</span> Overview
          </button>
          <button
            className={view === "verify" ? "nav-item active" : "nav-item"}
            onClick={() => setView("verify")}
          >
            <span>⌕</span> Check a medicine
          </button>
          <button
            className={view === "alerts" ? "nav-item active" : "nav-item"}
            onClick={() => setView("alerts")}
          >
            <span>⚑</span> Safety alerts
          </button>
        </nav>

        <div className="sidebar-note">
          <span className="status-dot" />
          <div>
            <strong>Evidence-first checks</strong>
            <p>Every result should be traceable to a source.</p>
          </div>
        </div>

        <div className="sidebar-footer">INDEPENDENT SAFETY TOOL</div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> {view === "overview" ? "Overview" : view === "verify" ? "Medicine check" : "Safety alerts"}</div>
          <div className="topbar-right">
            <span className="snapshot-pill"><span className="status-dot" /> Local evidence snapshot</span>
            <span className="avatar">CF</span>
          </div>
        </header>

        {view === "overview" && (
          <section className="page">
            <div className="eyebrow">MEDICINE SAFETY, WITH EVIDENCE</div>
            <div className="hero">
              <div className="hero-copy">
                <h1>Know what the label says.<br /><span>Check what the evidence says.</span></h1>
                <p>
                  Compare medicine-package claims with available registration
                  records and published safety alerts. Understand discrepancies
                  before you decide what to do next.
                </p>
                <button className="primary-button" onClick={() => setView("verify")}>
                  Check a medicine <span>→</span>
                </button>
                <div className="hero-footnote">
                  No image has been analysed yet. Start a check to review package claims.
                </div>
              </div>
              <div className="hero-art" aria-hidden="true">
                <div className="art-orbit orbit-one" />
                <div className="art-orbit orbit-two" />
                <div className="art-card">
                  <div className="art-card-top"><span className="mini-mark">C</span><span>PACKAGE REVIEW</span></div>
                  <div className="art-lines"><i /><i /><i /></div>
                  <div className="art-rule" />
                  <div className="art-check"><span>✓</span><div><b>Evidence comparison</b><small>Review sources and discrepancies</small></div></div>
                </div>
                <div className="art-stamp"><span>+</span><small>SAFETY<br />STARTS WITH<br />EVIDENCE</small></div>
              </div>
            </div>

            <div className="section-heading">
              <div><span className="eyebrow">YOUR WORKSPACE</span><h2>Start with a clear question</h2></div>
              <span className="section-caption">Designed for transparency, not guesswork.</span>
            </div>

            <div className="action-grid">
              <button className="action-card" onClick={() => setView("verify")}>
                <span className="action-icon blue">⌕</span>
                <span className="action-title">Check a medicine <b>↗</b></span>
                <span className="action-description">Review a package's visible claims against the evidence available to CONFAM.</span>
                <span className="action-link">Start a check <span>→</span></span>
              </button>
              <button className="action-card" onClick={() => setView("alerts")}>
                <span className="action-icon orange">⚑</span>
                <span className="action-title">Explore safety alerts <b>↗</b></span>
                <span className="action-description">Read published alerts and understand the specific products or batches they describe.</span>
                <span className="action-link">Review alerts <span>→</span></span>
              </button>
            </div>

            <div className="notice">
              <span className="notice-icon">i</span>
              <p><strong>Important to know</strong> A matching registration number or no detected discrepancy does not prove that a physical medicine is authentic. CONFAM is an independent decision-support tool, not a NAFDAC service.</p>
            </div>
          </section>
        )}

        {view === "verify" && (
          <section className="page">
            <div className="eyebrow">PACKAGE REVIEW</div>
            <h1 className="page-title">Check a medicine</h1>
            <p className="page-intro">
              Enter the information printed on the package. CONFAM will compare
              supported claims with its available registration snapshot and
              look for relevant safety-alert records.
            </p>

            <form className="verification-form" onSubmit={submitVerification}>
              <div className="form-heading">
                <h2>Package information</h2>
                <p>Enter the details as printed. Leave unknown fields blank.</p>
              </div>

              <div className="claims-grid">
                <label className="claim-field">
                  Product name <span className="required-mark">Required</span>
                  <input
                    name="product_name"
                    value={claims.product_name}
                    onChange={(event) => updateClaim("product_name", event.target.value)}
                    placeholder="e.g. AG Spironolactone"
                    required
                  />
                </label>

                <label className="claim-field">
                  NAFDAC registration number <span className="required-mark">Required</span>
                  <input
                    name="nafdac_reg_number"
                    value={claims.nafdac_reg_number}
                    onChange={(event) => updateClaim("nafdac_reg_number", event.target.value)}
                    placeholder="e.g. A4-100160"
                    required
                  />
                </label>

                <label className="claim-field">
                  Active ingredients
                  <input
                    name="active_ingredients"
                    value={claims.active_ingredients}
                    onChange={(event) => updateClaim("active_ingredients", event.target.value)}
                    placeholder="e.g. Spironolactone"
                  />
                </label>

                <label className="claim-field">
                  Strength
                  <input
                    name="strength"
                    value={claims.strength}
                    onChange={(event) => updateClaim("strength", event.target.value)}
                    placeholder="e.g. 25 mg"
                  />
                </label>

                <label className="claim-field">
                  Dosage form
                  <input
                    name="dosage_form"
                    value={claims.dosage_form}
                    onChange={(event) => updateClaim("dosage_form", event.target.value)}
                    placeholder="e.g. Tablet"
                  />
                </label>

                <label className="claim-field">
                  Manufacturer
                  <input
                    name="manufacturer"
                    value={claims.manufacturer}
                    onChange={(event) => updateClaim("manufacturer", event.target.value)}
                    placeholder="As printed on the package"
                  />
                </label>

                <label className="claim-field">
                  Batch number
                  <input
                    name="batch_number"
                    value={claims.batch_number}
                    onChange={(event) => updateClaim("batch_number", event.target.value)}
                    placeholder="Enter the printed batch number"
                  />
                </label>

                <label className="claim-field">
                  Manufacturing date
                  <input
                    name="manufacturing_date"
                    value={claims.manufacturing_date}
                    onChange={(event) => updateClaim("manufacturing_date", event.target.value)}
                    placeholder="e.g. 07/2025"
                  />
                </label>

                <label className="claim-field">
                  Package expiry date
                  <input
                    name="expiry_date"
                    value={claims.expiry_date}
                    onChange={(event) => updateClaim("expiry_date", event.target.value)}
                    placeholder="e.g. 07/2027"
                  />
                </label>
              </div>

              <p className="form-scope-note">
                Current engine limitation: batch and package dates are collected
                but are not compared with registry fields. A batch number may
                help identify a relevant safety alert. Registration expiry is a
                separate registry-record check.
              </p>

              <button className="primary-button" type="submit" disabled={verificationLoading}>
                {verificationLoading ? "Checking evidence…" : "Compare with available evidence"}
                {!verificationLoading && <span>→</span>}
              </button>
            </form>

            {verificationError && (
              <div className="verification-error" role="alert">
                <h2>Verification request failed</h2>
                <p>{verificationError}</p>
                <p>Confirm that the FastAPI backend is running, then submit again.</p>
              </div>
            )}

            {verification && (
              <section className="verification-result" aria-live="polite">
                <div className="result-heading">
                  <div>
                    <div className="eyebrow">EVIDENCE REVIEW RESULT</div>
                    <h2>
                      {verification.status === "no_significant_discrepancy_detected"
                        ? "No significant discrepancy detected"
                        : verification.status === "high_risk_discrepancy_detected"
                          ? "High-risk discrepancy detected"
                          : "Verification concern detected"}
                    </h2>
                  </div>
                  <span className={`result-status ${verification.status}`}>
                    {verification.status === "no_significant_discrepancy_detected"
                      ? "No significant discrepancy"
                      : verification.status === "high_risk_discrepancy_detected"
                        ? "High risk"
                        : "Review needed"}
                  </span>
                </div>

                <p className="result-summary">{verification.summary}</p>

                <div className="result-meta">
                  <span>
                    Registry record: <strong>{verification.matched_record ? "Found" : "Not found"}</strong>
                  </span>
                  <span>
                    Registry snapshot: <strong>{verification.registry_snapshot_retrieved_at || "Unknown"}</strong>
                  </span>
                  <span>
                    Alert snapshot: <strong>{verification.alerts_snapshot_retrieved_at || "Unknown"}</strong>
                  </span>
                </div>

                {verification.source_url && (
                  <p className="result-source">
                    Reference source:{" "}
                    <a href={verification.source_url} target="_blank" rel="noreferrer">
                      {verification.source_name || "Open source record"} ↗
                    </a>
                  </p>
                )}

                <h3>Field-by-field comparison</h3>
                {verification.checks.length > 0 ? (
                  <div className="checks-list">
                    {verification.checks.map((check, index) => (
                      <article className="check-row" key={`${check.field}-${index}`}>
                        <div className="check-row-heading">
                          <strong>{fieldLabels[check.field] || check.field.replaceAll("_", " ")}</strong>
                          <span className={`check-outcome ${check.outcome}`}>
                            {check.outcome === "match"
                              ? "Match"
                              : check.outcome === "mismatch"
                                ? "Mismatch"
                                : "Not checked"}
                          </span>
                        </div>
                        <div className="check-values">
                          <p><span>Package claim</span>{check.package_value || "Not provided"}</p>
                          <p><span>Reference record</span>{check.reference_value || "Not available"}</p>
                        </div>
                        <p className="check-explanation">{check.explanation}</p>
                        {check.check_scope === "registry_record_only" && (
                          <p className="check-scope-label">Registry record check — not a package-label comparison</p>
                        )}
                      </article>
                    ))}
                  </div>
                ) : (
                  <p>No field-level comparisons were available for this result.</p>
                )}

                {verification.safety_alerts.length > 0 && (
                  <div className="matched-alerts">
                    <h3>Relevant safety-alert evidence</h3>
                    {verification.safety_alerts.map((alert) => (
                      <article className="matched-alert" key={alert.alert_id}>
                        <strong>{alert.title}</strong>
                        <p>{alert.explanation}</p>
                        {alert.published_at && <p>Published: {alert.published_at}</p>}
                        {alert.alert_scope && <p>Scope: {alert.alert_scope}</p>}
                        <p>
                          Batch comparison:{" "}
                          {alert.batch_match_status === "match"
                            ? "Matches the batch reported in the alert"
                            : alert.batch_match_status === "different"
                              ? "Differs from the batch reported in the alert"
                              : "Not checked — insufficient batch information"}
                        </p>
                        {alert.batch_expiry_date && (
                          <p>
                            Reported batch expiry: {alert.batch_expiry_date} ·{" "}
                            {alert.batch_expiry_status === "expired"
                              ? "Historical batch — expiry has passed"
                              : alert.batch_expiry_status === "not_expired"
                                ? "Reported batch expiry has not passed"
                                : "Batch expiry status unknown"}
                          </p>
                        )}
                        {!alert.batch_expiry_date && (
                          <p>Batch expiry status unknown; the original notice remains the source of context.</p>
                        )}
                        <a href={alert.source_url} target="_blank" rel="noreferrer">
                          Read original {alert.source_name} notice ↗
                        </a>
                      </article>
                    ))}
                  </div>
                )}

                <div className="result-limitations">
                  <h3>What this result cannot establish</h3>
                  <ul>
                    {verification.limitations.map((limitation, index) => (
                      <li key={`${index}-${limitation}`}>{limitation}</li>
                    ))}
                    <li>A matching registration number or no detected discrepancy does not prove physical authenticity.</li>
                  </ul>
                </div>

                <button
                  className="text-button"
                  onClick={() => {
                    setVerification(null);
                    setVerificationError("");
                    setClaims(emptyClaims);
                  }}
                >
                  Start a new check
                </button>
              </section>
            )}

            <div className="notice">
              <span className="notice-icon">i</span>
              <p>
                <strong>Independent decision support.</strong> This result is
                based on a small, versioned dataset. It does not establish
                chemical composition, contamination, or physical authenticity
                and is not an official NAFDAC verdict.
              </p>
            </div>

            <div className="upload-panel">
              <div className="upload-symbol">↑</div>
              <h2>Package image analysis</h2>
              <p>Image extraction is not connected yet. Enter package details manually for now.</p>
              <span className="coming-soon">IMAGE EXTRACTION NOT CONFIGURED</span>
            </div>

            <button className="text-button" onClick={() => setView("overview")}>← Back to overview</button>
          </section>
        )}

        {view === "alerts" && (
          <section className="page">
            <div className="eyebrow">PUBLIC SAFETY INFORMATION</div>
            <h1 className="page-title">Safety alerts</h1>
            <p className="page-intro">Published safety information can help identify concerns. Each alert must be read within its original scope.</p>
            <div className="alert-data-note">
              <strong>Curated evidence snapshot</strong>
              <p>
                This is a limited collection of published records, not a
                complete or live NAFDAC alert feed. Always read the original
                notice and check its scope.
              </p>
            </div>

            {alertsLoading && (
              <div className="alert-empty" role="status">
                <h2>Loading safety alerts…</h2>
                <p>Retrieving available records from the CONFAM API.</p>
              </div>
            )}

            {!alertsLoading && alertsError && (
              <div className="alert-empty" role="alert">
                <h2>Could not load safety alerts</h2>
                <p>{alertsError}</p>
                <p>
                  Check that the FastAPI backend is running on port 8000,
                  then try again.
                </p>
                <button
                  className="primary-button"
                  onClick={() => setAlertReload((count) => count + 1)}
                >
                  Try again <span>↻</span>
                </button>
              </div>
            )}

            {!alertsLoading && !alertsError && alerts.length === 0 && (
              <div className="alert-empty">
                <h2>No alert records available</h2>
                <p>The API responded successfully but returned no records.</p>
              </div>
            )}

            {!alertsLoading && !alertsError && alerts.length > 0 && (
              <>
                <div className="alert-results-heading">
                  <h2>Available records</h2>
                  <span>{alerts.length} record{alerts.length === 1 ? "" : "s"}</span>
                </div>

                <div className="alert-list">
                  {alerts.map((alert) => (
                    <article className="alert-record" key={alert.alert_id}>
                      <div className="alert-record-top">
                        <span className="alert-number">
                          NAFDAC Alert {alert.alert_number}
                        </span>
                        <span className="historical-label">
                          Historical notice
                        </span>
                      </div>

                      <h2>{alert.title}</h2>

                      <p className="alert-product">
                        <strong>{alert.product_name}</strong>
                        {alert.product_strength
                          ? ` · ${alert.product_strength}`
                          : ""}
                      </p>

                      <p className="alert-batch-status">
                        {getBatchExpiryStatus(alert.expiry_date) === "expired"
                          ? "Reported batch: expiry has passed"
                          : getBatchExpiryStatus(alert.expiry_date) === "not_expired"
                            ? "Reported batch: expiry has not passed"
                            : "Reported batch: expiry status unknown"}
                      </p>

                      <dl className="alert-facts">
                        <div>
                          <dt>Reported registration number</dt>
                          <dd>{alert.reported_registration_number || "Not stated"}</dd>
                        </div>
                        <div>
                          <dt>Reported batch</dt>
                          <dd>{alert.batch_number || "Not stated"}</dd>
                        </div>
                        <div>
                          <dt>Published</dt>
                          <dd>{alert.published_at || "Date not available"}</dd>
                        </div>
                        <div>
                          <dt>Expiry date stated in notice</dt>
                          <dd>{alert.expiry_date || "Not stated"}</dd>
                        </div>
                      </dl>

                      <div className="alert-scope-note">
                        <strong>Why this alert matters</strong>
                        <p>{alert.curation_note}</p>
                      </div>

                      <p className="alert-source">
                        Source: {alert.source_name} · Scope: {alert.alert_scope}
                      </p>

                      <a
                        className="alert-source-link"
                        href={alert.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Read the original notice <span>↗</span>
                      </a>
                    </article>
                  ))}
                </div>
              </>
            )}
            <div className="notice">
              <span className="notice-icon">i</span>
              <p><strong>Alert scope matters.</strong> A notice about a particular batch or reported product should not be generalized to every product sharing its name.</p>
            </div>
          </section>
        )}

        <footer className="page-footer">
          <span>CONFAM <span className="footer-separator">·</span> Independent medicine-safety intelligence</span>
          <span>Evidence can inform a decision. It cannot prove authenticity.</span>
        </footer>
      </main>
    </div>
  );
}

export default App;

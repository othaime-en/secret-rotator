/**
 * API Client for Secret Rotation Dashboard
 *
 * Provides methods to interact with the Flask backend API.
 * All methods return Promises that resolve to JSON data (or reject
 * with an Error carrying the server's error message).
 */

class APIClient {
  constructor(baseURL = "") {
    this.baseURL = baseURL;
  }

  /**
   * Read the CSRF token rendered into <head> by csrf_meta_tag() (S4).
   * Required on every POST/PUT/PATCH/DELETE request or Flask-WTF
   * rejects the request with a 400 before it reaches the route.
   */
  _csrfHeaders(extra = {}) {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const token = meta ? meta.getAttribute("content") : "";
    return Object.assign({ "X-CSRFToken": token }, extra);
  }

  /**
   * Shared response handler: parses the JSON body and throws an Error
   * (carrying .status and .body) for any non-2xx response.
   */
  async _handleResponse(response) {
    let body = null;
    try {
      body = await response.json();
    } catch (e) {
      // Non-JSON body — shouldn't normally happen, every route here
      // returns JSON — but don't let a parse error mask the real
      // HTTP error status below.
    }

    if (!response.ok) {
      const message =
        (body && (body.error || body.message)) || `HTTP ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.body = body;
      throw error;
    }

    return body;
  }

  async fetchStatus() {
    const response = await fetch(`${this.baseURL}/api/status`);
    return this._handleResponse(response);
  }

  async fetchJobs() {
    const response = await fetch(`${this.baseURL}/api/jobs`);
    return this._handleResponse(response);
  }

  /**
   * Start a background rotation of all secrets.
   *
   * The server responds immediately (202) with a job id rather than
   * waiting for rotation to finish — poll getRotationJob(jobId) for
   * progress and the final result.
   *
   * If a rotation is already in flight, the server responds 409 with
   * that job's id instead of starting a second one. That's treated as
   * a normal (non-throwing) result here too, with already_running
   * set on the returned object, since the caller almost always just
   * wants a job id to poll — whichever rotation is actually running.
   *
   * @returns {Promise<Object>} Job info: { job_id, status, progress, already_running?, ... }
   */
  async rotateAll() {
    const response = await fetch(`${this.baseURL}/api/rotate`, {
      method: "POST",
      headers: this._csrfHeaders({ "Content-Type": "application/json" }),
    });

    if (response.status === 202 || response.status === 409) {
      return response.json();
    }
    return this._handleResponse(response);
  }

  /**
   * Poll the status of a background rotation job started by rotateAll().
   * @param {string} jobId
   * @returns {Promise<Object>} Job info: { job_id, status, progress, results, error, ... }
   */
  async getRotationJob(jobId) {
    const response = await fetch(
      `${this.baseURL}/api/rotate/${encodeURIComponent(jobId)}`,
    );
    return this._handleResponse(response);
  }

  async fetchBackups(secretId = null) {
    const url = secretId
      ? `${this.baseURL}/api/backups?secret_id=${encodeURIComponent(secretId)}`
      : `${this.baseURL}/api/backups`;

    const response = await fetch(url);
    return this._handleResponse(response);
  }

  async fetchBackupDetail(backupFile) {
    const response = await fetch(
      `${this.baseURL}/api/backups/${encodeURIComponent(backupFile)}`,
    );
    return this._handleResponse(response);
  }

  async restoreBackup(backupFile) {
    const response = await fetch(`${this.baseURL}/api/restore`, {
      method: "POST",
      headers: this._csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ backup_file: backupFile }),
    });
    return this._handleResponse(response);
  }

  async fetchBackupHealth() {
    const response = await fetch(`${this.baseURL}/api/backup-health`);
    return this._handleResponse(response);
  }

  async fetchVerificationHistory(days = 7) {
    const response = await fetch(
      `${this.baseURL}/api/verification-history?days=${days}`,
    );
    return this._handleResponse(response);
  }

  async runVerification() {
    const response = await fetch(`${this.baseURL}/api/run-verification`, {
      method: "POST",
      headers: this._csrfHeaders({ "Content-Type": "application/json" }),
    });
    return this._handleResponse(response);
  }
}

window.APIClient = APIClient;

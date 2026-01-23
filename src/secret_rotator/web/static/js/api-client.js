/**
 * API Client for Secret Rotation Dashboard
 * 
 * Provides methods to interact with the Flask backend API.
 * All methods return Promises that resolve to JSON data.
 */

class APIClient {
    constructor(baseURL = '') {
        this.baseURL = baseURL;
    }

    /**
     * Fetch system status
     * @returns {Promise<Object>} Status data
     */
    async fetchStatus() {
        const response = await fetch(`${this.baseURL}/api/status`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Failed to fetch status`);
        }
        return response.json();
    }

    /**
     * Fetch all rotation jobs
     * @returns {Promise<Object>} Jobs data
     */
    async fetchJobs() {
        const response = await fetch(`${this.baseURL}/api/jobs`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Failed to fetch jobs`);
        }
        return response.json();
    }

    /**
     * Trigger rotation of all secrets
     * @returns {Promise<Object>} Rotation results
     */
    async rotateAll() {
        const response = await fetch(`${this.baseURL}/api/rotate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Rotation failed`);
        }
        return response.json();
    }

    /**
     * Fetch backups list
     * @param {string|null} secretId - Optional filter by secret ID
     * @returns {Promise<Object>} Backups data
     */
    async fetchBackups(secretId = null) {
        const url = secretId
            ? `${this.baseURL}/api/backups?secret_id=${encodeURIComponent(secretId)}`
            : `${this.baseURL}/api/backups`;

        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Failed to fetch backups`);
        }
        return response.json();
    }

    /**
     * Fetch backup detail
     * @param {string} backupFile - Path to backup file
     * @returns {Promise<Object>} Backup details
     */
    async fetchBackupDetail(backupFile) {
        const response = await fetch(
            `${this.baseURL}/api/backups/${encodeURIComponent(backupFile)}`
        );
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Failed to fetch backup detail`);
        }
        return response.json();
    }

    /**
     * Restore a backup
     * @param {string} backupFile - Path to backup file
     * @returns {Promise<Object>} Restoration result
     */
    async restoreBackup(backupFile) {
        const response = await fetch(`${this.baseURL}/api/restore`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ backup_file: backupFile })
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Restore failed`);
        }
        return response.json();
    }

    /**
     * Fetch backup health metrics
     * @returns {Promise<Object>} Health data
     */
    async fetchBackupHealth() {
        const response = await fetch(`${this.baseURL}/api/backup-health`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Failed to fetch backup health`);
        }
        return response.json();
    }

    /**
     * Fetch verification history
     * @param {number} days - Number of days to fetch
     * @returns {Promise<Object>} Verification history
     */
    async fetchVerificationHistory(days = 7) {
        const response = await fetch(
            `${this.baseURL}/api/verification-history?days=${days}`
        );
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Failed to fetch verification history`);
        }
        return response.json();
    }

    /**
     * Trigger manual backup verification
     * @returns {Promise<Object>} Verification report
     */
    async runVerification() {
        const response = await fetch(`${this.baseURL}/api/run-verification`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: Verification failed`);
        }
        return response.json();
    }
}

window.APIClient = APIClient;
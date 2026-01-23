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
     * @returns {Promise<Object>} Backups data
     */
    async fetchBackups() {
        const response = await fetch(`${this.baseURL}/api/backups`);
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
}
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
}
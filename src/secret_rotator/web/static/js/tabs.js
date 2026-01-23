/**
 * Tab Manager for Dashboard Navigation
 * 
 * Handles switching between different dashboard views (Jobs, Backups, Health, Logs)
 */

class TabManager {
    constructor(containerSelector) {
        this.container = document.querySelector(containerSelector);
        if (!this.container) {
            console.error(`Tab container not found: ${containerSelector}`);
            return;
        }
        this.setupEventListeners();
    }

    setupEventListeners() {
        const tabs = this.container.querySelectorAll('.tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', (e) => this.switchTab(e.target));
        });
    }

    switchTab(tabElement) {
        // Remove active class from all tabs and content
        this.container.querySelectorAll('.tab').forEach(t =>
            t.classList.remove('active')
        );
        document.querySelectorAll('.tab-content').forEach(c =>
            c.classList.remove('active')
        );

        tabElement.classList.add('active');

        const tabName = tabElement.getAttribute('data-tab');
        const contentId = `${tabName}-content`;
        const content = document.getElementById(contentId);

        if (content) {
            content.classList.add('active');
        }

        // Trigger load callback if exists
        const loadCallback = tabElement.getAttribute('data-onload');
        if (loadCallback && typeof window[loadCallback] === 'function') {
            window[loadCallback]();
        }
    }
}

window.TabManager = TabManager;

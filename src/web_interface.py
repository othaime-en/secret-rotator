import json
import threading
from urllib.parse import parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler
from utils.logger import logger

class RotationWebHandler(BaseHTTPRequestHandler):
    """Simple web interface for rotation system"""
    
    def __init__(self, rotation_engine, *args, **kwargs):
        self.rotation_engine = rotation_engine
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        if self.path == "/":
            self._serve_dashboard()
        elif self.path == "/api/status":
            self._serve_status()
        elif self.path == "/api/jobs":
            self._serve_jobs()
        else:
            self._serve_404()
    
    def do_POST(self):
        """Handle POST requests"""
        if self.path == "/api/rotate":
            self._handle_rotation()
        else:
            self._serve_404()
    
    def _serve_dashboard(self):
        """Serve main dashboard"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Secret Rotation Dashboard</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa; }
                .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
                .job { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #007bff; }
                button { background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; margin: 5px; }
                button:hover { background: #0056b3; }
                .status { padding: 10px; margin: 10px 0; border-radius: 5px; }
                .success { background: #d4edda; color: #155724; }
                .error { background: #f8d7da; color: #721c24; }
                .info { background: #d1ecf1; color: #0c5460; }
                h1 { color: #333; border-bottom: 3px solid #007bff; padding-bottom: 10px; }
                h2 { color: #555; margin-top: 30px; }
                .tab-container { margin: 20px 0; }
                .tab { display: inline-block; padding: 10px 20px; cursor: pointer; background: #e9ecef; border-radius: 5px 5px 0 0; margin-right: 5px; }
                .tab.active { background: #007bff; color: white; }
                .tab-content { display: none; padding: 20px; border: 1px solid #dee2e6; border-radius: 0 5px 5px 5px; }
                .tab-content.active { display: block; }
                #logs { background: #2c3e50; color: #ecf0f1; padding: 15px; border-radius: 5px; font-family: 'Courier New', monospace; max-height: 300px; overflow-y: auto; font-size: 13px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Secret Rotation Dashboard</h1>
                
                <div id="status"></div>
                
                <div class="tab-container">
                    <div class="tab active" onclick="switchTab('jobs')">Rotation Jobs</div>
                    <div class="tab" onclick="switchTab('backups')">Backups</div>
                    <div class="tab" onclick="switchTab('logs')">Logs</div>
                </div>
                
                <div id="jobs-content" class="tab-content active">
                    <h2>Rotation Jobs</h2>
                    <div id="jobs"></div>
                    <button onclick="rotateAll()">Rotate All Secrets</button>
                </div>
                
                <div id="backups-content" class="tab-content">
                    <h2>Backup History</h2>
                    <div id="backups"></div>
                </div>
                
                <div id="logs-content" class="tab-content">
                    <h2>Recent Activity Logs</h2>
                    <div id="logs"></div>
                </div>
            </div>
            
            <script>
                function switchTab(tabName) {
                    document.querySelectorAll('.tab-content').forEach(content => {
                        content.classList.remove('active');
                    });
                    document.querySelectorAll('.tab').forEach(tab => {
                        tab.classList.remove('active');
                    });
                    
                    document.getElementById(tabName + '-content').classList.add('active');
                    event.target.classList.add('active');
                }
                
                function loadJobs() {
                    fetch('/api/jobs')
                        .then(response => response.json())
                        .then(data => {
                            const jobsDiv = document.getElementById('jobs');
                            jobsDiv.innerHTML = data.jobs.map(job => 
                                `<div class="job">
                                    <strong>${job.name}</strong><br>
                                    Provider: ${job.provider}<br>
                                    Rotator: ${job.rotator}<br>
                                    Secret ID: ${job.secret_id}
                                </div>`
                            ).join('');
                        });
                }
                
                function rotateAll() {
                    document.getElementById('status').innerHTML = '<div class="status info">Rotation in progress...</div>';
                    
                    fetch('/api/rotate', { method: 'POST' })
                        .then(response => response.json())
                        .then(data => {
                            const successful = Object.values(data.results).filter(r => r).length;
                            const total = Object.keys(data.results).length;
                            const statusClass = successful === total ? 'success' : 'error';
                            
                            document.getElementById('status').innerHTML = 
                                `<div class="status ${statusClass}">Rotation complete: ${successful}/${total} successful</div>`;
                            
                            const logs = Object.entries(data.results)
                                .map(([job, success]) => `[${new Date().toLocaleTimeString()}] ${job}: ${success ? 'SUCCESS' : 'FAILED'}`)
                                .join('\\n');
                            document.getElementById('logs').innerHTML = logs;
                        })
                        .catch(error => {
                            document.getElementById('status').innerHTML = 
                                '<div class="status error">Error during rotation</div>';
                        });
                }
                
                loadJobs();
            </script>
        </body>
        </html>
        """
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def _send_json(self, data, status=200):
        """Send JSON response"""
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def _serve_status(self):
        """Serve system status"""
        status = {
            "status": "running",
            "providers": len(self.rotation_engine.providers),
            "rotators": len(self.rotation_engine.rotators),
            "jobs": len(self.rotation_engine.rotation_jobs)
        }
        self._send_json(status)
    
    def _serve_jobs(self):
        """Serve job configurations"""
        jobs_data = {"jobs": self.rotation_engine.rotation_jobs}
        self._send_json(jobs_data)
    
    def _handle_rotation(self):
        """Handle rotation request"""
        try:
            results = self.rotation_engine.rotate_all_secrets()
            self._send_json({"results": results})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
    
    def _serve_404(self):
        """Serve 404 error"""
        self.send_response(404)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<h1>404 Not Found</h1>')
    
    def log_message(self, format, *args):
        """Override to use our logger instead of printing"""
        logger.info(f"Web request: {format % args}")

class WebServer:
    """Main web server class to manage the HTTP server"""
    
    def __init__(self, rotation_engine, port=8080):
        self.rotation_engine = rotation_engine
        self.port = port
        self.server = None
        self.thread = None
    
    def start(self):
        """Start the web server in a separate thread""" 
        handler = lambda *args, **kwargs: RotationWebHandler(self.rotation_engine, *args, **kwargs)
        self.server = HTTPServer(('localhost', self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Web server started on http://localhost:{self.port}")

    def stop(self):
        """Stop the web server"""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        logger.info("Web server stopped")
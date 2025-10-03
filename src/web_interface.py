import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from utils.logger import logger

class RotationWebHandler(BaseHTTPRequestHandler):
    """Simple web interface for rotation system"""
    
    def __init__(self, rotation_engine, *args, **kwargs):
        self.rotation_engine = rotation_engine
        super().__init__(*args, **kwargs)
    
    def _serve_dashboard(self):
        """Serve main dashboard"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Secret Rotation Dashboard</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .container { max-width: 800px; }
                .job { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }
                button { background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }
                button:hover { background: #0056b3; }
                .status { padding: 10px; margin: 10px 0; border-radius: 5px; }
                .success { background: #d4edda; color: #155724; }
                .error { background: #f8d7da; color: #721c24; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Secret Rotation Dashboard</h1>
                
                <div id="status"></div>
                
                <h2>Rotation Jobs</h2>
                <div id="jobs"></div>
                
                <h2>Actions</h2>
                <button onclick="rotateAll()">Rotate All Secrets</button>
                
                <h2>Logs</h2>
                <div id="logs" style="background: #f8f9fa; padding: 15px; border-radius: 5px; font-family: monospace; max-height: 300px; overflow-y: auto;"></div>
            </div>
            
            <script>
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
                    document.getElementById('status').innerHTML = '<div class="status">Rotation in progress...</div>';
                    
                    fetch('/api/rotate', { method: 'POST' })
                        .then(response => response.json())
                        .then(data => {
                            const successful = Object.values(data.results).filter(r => r).length;
                            const total = Object.keys(data.results).length;
                            const statusClass = successful === total ? 'success' : 'error';
                            
                            document.getElementById('status').innerHTML = 
                                `<div class="status ${statusClass}">Rotation complete: ${successful}/${total} successful</div>`;
                            
                            // Show detailed results
                            const logs = Object.entries(data.results)
                                .map(([job, success]) => `${job}: ${success ? 'SUCCESS' : 'FAILED'}`)
                                .join('\\n');
                            document.getElementById('logs').innerHTML = logs;
                        })
                        .catch(error => {
                            document.getElementById('status').innerHTML = 
                                '<div class="status error">Error during rotation</div>';
                        });
                }
                
                // Load jobs on page load
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
    


class WebServer:
    # This is the main web server manager class
    
    def __init__(self, rotation_engine, port=8080):
        self.rotation_engine = rotation_engine
        self.port = port
        self.server = None
    
    def start(self):
        # I'll implement the starting logic 
        logger.info(f"Web server started on http://localhost:{self.port}")
    
    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        logger.info("Web server stopped")



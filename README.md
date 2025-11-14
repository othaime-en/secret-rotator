# Secret Rotation Automation

A Python-based system for automating the rotation of passwords, API keys, and other secrets across different services. The system provides scheduled rotation, backup management, and a web interface for monitoring and manual operations.

## Features

- Automated secret rotation with configurable schedules
- Multiple secret types supported (passwords, API keys, database credentials)
- Backup and restore functionality for all rotations
- Web-based dashboard for monitoring and manual rotation
- Extensible plugin system for custom providers and rotators
- Retry logic with exponential backoff
- Comprehensive audit logging

## Installation

1. Clone the repository:

```bash
git clone https://github.com/othaime-en/secret-rotator
cd secret-rotator
```

2. Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install pyyaml schedule cryptography
```

4. Create your configuration file:

```bash
cp config/config.example.yaml config/config.yaml
```

5. Edit `config/config.yaml` with your settings.

## Usage

### Running the Application

Start the application in daemon mode with the web interface and scheduler:

```bash
python src/main.py
```

The web interface will be available at `http://localhost:8080`

### Running a One-Time Rotation

Execute a single rotation without starting the scheduler:

```bash
python src/main.py --mode once
```

## Configuration

Edit `config/config.yaml` to configure:

- Rotation schedules (daily, weekly, custom intervals)
- Secret providers (file storage, AWS Secrets Manager, etc.)
- Rotator settings (password length, complexity requirements)
- Notification preferences
- Backup settings

See `config/config.example.yaml` for all available options.

## Running Tests

Execute the test suite:

```bash
cd tests
python run_tests.py
```

## Project Structure

```
secret-rotation-automation/
├── src/
│   ├── providers/          # Secret storage providers
│   ├── rotators/           # Secret generation logic
│   ├── config/             # Configuration management
│   ├── utils/              # Utility functions
│   ├── rotation_engine.py  # Core rotation orchestration
│   ├── scheduler.py        # Scheduling logic
│   ├── web_interface.py    # Web dashboard
│   └── main.py             # Application entry point
├── tests/                  # Test suite
├── config/                 # Configuration files
└── data/                   # Runtime data and backups
```

## License

This project is licensed under the MIT License. See the LICENSE.md file for details.

## Contributing

Contributions are welcome. Please ensure all tests pass before submitting a pull request.

## Security

This tool handles sensitive credentials. Ensure proper access controls are in place:

- Restrict file permissions on configuration files
- Secure the master encryption key
- Use HTTPS in production environments
- Regularly review audit logs
